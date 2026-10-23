import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.publish_fds_view import publish


SCRATCH = Path(__file__).resolve().parents[1] / "out"


class PublishFdsViewTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "run"
        self.run.mkdir()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def fixture(self, *, start_offset_hours=-1, input_offset_hours=-2):
        start = self.now + timedelta(hours=start_offset_hours)
        end = start + timedelta(hours=24)
        epoch = self.now + timedelta(hours=input_offset_hours)
        iso = lambda date: date.isoformat().replace("+00:00", "Z")
        payload = {
            "schema": "fds.orbit-view.v1", "quality": "PRELIMINARY_NOT_FOR_OPERATIONS",
            "source": "TLE", "norad_id": 12345, "input_sha256": "a" * 64,
            "input_epoch_utc": iso(epoch), "start_utc": iso(start), "end_utc": iso(end),
            "stations": [{"id": "yonsei"}, {"id": "kari"}],
            "samples": [[iso(start)], [iso(end)]],
        }
        (self.run / "orbit_view.json").write_text(json.dumps(payload), encoding="utf-8")
        (self.run / "run.properties").write_text(
            "schema=fds.run.v1\nsource=TLE\nexpected_norad_id=12345\ninput_sha256=" +
            "a" * 64 + "\n", encoding="utf-8")
        return payload

    def test_fresh_run_is_atomically_published(self):
        self.fixture()
        final = publish(self.run, self.root / "served")
        self.assertEqual(json.loads(final.read_text())["norad_id"], 12345)
        self.assertEqual(sorted(item.name for item in final.parent.iterdir()), ["orbit_view.json"])

    def test_stale_run_is_rejected(self):
        self.fixture(start_offset_hours=-100, input_offset_hours=-101)
        with self.assertRaises(ValueError):
            publish(self.run, self.root / "served")

    def test_mismatched_input_cannot_replace_existing_view(self):
        payload = self.fixture()
        final = publish(self.run, self.root / "served")
        before = final.read_bytes()
        payload["input_sha256"] = "b" * 64
        (self.run / "orbit_view.json").write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ValueError):
            publish(self.run, self.root / "served")
        self.assertEqual(final.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
