import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools.run_fds_pipeline import current_config, verified_pair


SCRATCH = Path(__file__).resolve().parents[1] / "out"


class FdsPipelineInputTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.body = self.root / "orbit.tle"
        self.manifest = self.root / "orbit.tle.manifest.json"
        self.body.write_bytes(b"test-only-body\n")
        self.metadata = {
            "file": self.body.name, "type": "NORAD_TLE", "from": "dgs-net", "to": "fds",
            "version": 1, "created": "2026-10-23T00:00:00Z",
            "sha256": hashlib.sha256(self.body.read_bytes()).hexdigest(),
        }
        self.save_manifest()

    def save_manifest(self):
        self.manifest.write_text(json.dumps(self.metadata), encoding="utf-8")

    def test_valid_pair_is_snapshotted(self):
        self.assertEqual(verified_pair(self.body, self.manifest, "tle"), b"test-only-body\n")

    def test_tamper_or_wrong_route_fails_closed(self):
        self.body.write_bytes(b"changed\n")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            verified_pair(self.body, self.manifest, "tle")
        self.body.write_bytes(b"test-only-body\n")
        self.metadata["from"] = "ros"
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "route or schema"):
            verified_pair(self.body, self.manifest, "tle")

    def test_incomplete_or_duplicate_manifest_fails_closed(self):
        self.manifest.unlink()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            verified_pair(self.body, self.manifest, "tle")
        self.manifest.write_text('{"file":"orbit.tle","file":"orbit.tle"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            verified_pair(self.body, self.manifest, "tle")

    def test_gnss_requires_its_own_route(self):
        self.metadata.update({"type": "GNSS", "from": "ros"})
        self.save_manifest()
        self.assertEqual(verified_pair(self.body, self.manifest, "gnss"), b"test-only-body\n")
        with self.assertRaises(ValueError):
            verified_pair(self.body, self.manifest, "tle")

    def test_config_start_is_derived_and_unique(self):
        template = self.root / "fds.properties"
        template.write_text("expected_norad_id=25544\nstart_utc=REPLACE\nhorizon_hours=1\n",
                            encoding="utf-8")
        current = current_config(template, datetime(2026, 10, 23, 12, 34, 56, tzinfo=timezone.utc))
        self.assertIn("start_utc=2026-10-23T12:34:56Z", current)
        template.write_text("start_utc=OLD\nstart_utc=REPLACE\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "one start_utc"):
            current_config(template, datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
