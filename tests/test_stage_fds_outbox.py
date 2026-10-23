"""FDS N13/N15 outbox route and immutable pair checks."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.stage_fds_outbox import FILES, ROOT, stage


class FdsOutboxTest(unittest.TestCase):
    def setUp(self) -> None:
        (ROOT / "out").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "out")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run = self.root / "fds-20261023T100000Z-1234abcd"
        self.run.mkdir()
        (self.run / "run.properties").write_text(
            "schema=fds.run.v1\nquality=PRELIMINARY_NOT_FOR_OPERATIONS\n",
            encoding="utf-8",
        )
        for name, kind, recipient, _, header in FILES:
            body = header + b"rest\n"
            (self.run / name).write_bytes(body)
            (self.run / (name + ".manifest.json")).write_text(json.dumps({
                "file": name, "type": kind, "from": "fds", "to": recipient,
                "version": 1, "created": "2026-10-23T10:00:00Z",
                "sha256": hashlib.sha256(body).hexdigest(),
            }), encoding="utf-8")

    def test_stages_unique_pairs_and_rejects_restage(self) -> None:
        target = stage(self.run, self.root / "outbox")
        self.assertEqual(len(list(target.iterdir())), 6)
        for name, kind, recipient, _, _ in FILES:
            staged_name = name.removesuffix(".csv") + "-" + self.run.name + ".csv"
            body = (target / staged_name).read_bytes()
            manifest = json.loads((target / (staged_name + ".manifest.json")).read_text())
            self.assertEqual((manifest["file"], manifest["type"], manifest["to"]),
                             (staged_name, kind, recipient))
            self.assertEqual(hashlib.sha256(body).hexdigest(), manifest["sha256"])
        with self.assertRaisesRegex(ValueError, "already staged"):
            stage(self.run, self.root / "outbox")

    def test_rejects_tampering_before_publication(self) -> None:
        (self.run / "contact_schedule.csv").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "header mismatch"):
            stage(self.run, self.root / "outbox")
        self.assertFalse((self.root / "outbox" / self.run.name).exists())

    def test_rejects_wrong_route_and_duplicate_manifest_field(self) -> None:
        path = self.run / "contact_schedule.csv.manifest.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata["to"] = "ros"
        path.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "route or schema"):
            stage(self.run, self.root / "outbox")
        path.write_text(path.read_text(encoding="utf-8").replace('"to": "ros"',
                        '"to": "mps", "to": "mps"'), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate manifest field"):
            stage(self.run, self.root / "outbox")


if __name__ == "__main__":
    unittest.main()
