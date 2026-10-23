import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.stage8_snapshot import create, restore, verify


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="build-stage8-snapshot-", dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        (self.source / "ros").mkdir(parents=True)
        (self.source / "archive").mkdir()
        (self.source / "ros" / "approved.json").write_bytes(b'{"test":true}\n')
        (self.source / "archive" / "raw.bin").write_bytes(b"\x00\x01PADO")
        self.selection = self.root / "selection.json"
        self.selection.write_text(json.dumps({"version": 1, "files": [
            "ros/approved.json", "archive/raw.bin"]}), encoding="utf-8")
        self.snapshot = self.root / "snapshot"

    def test_create_verify_and_restore_to_new_location(self):
        digest = create(self.source, self.selection, self.snapshot)
        self.assertEqual(len(verify(self.snapshot, digest)), 2)
        destination = self.root / "recovered"
        restore(self.snapshot, destination, digest)
        self.assertEqual((destination / "ros" / "approved.json").read_bytes(),
                         b'{"test":true}\n')
        self.assertEqual((destination / "archive" / "raw.bin").read_bytes(),
                         b"\x00\x01PADO")
        with self.assertRaises(ValueError):
            restore(self.snapshot, destination, digest)
        with self.assertRaisesRegex(ValueError, "locations must be separate"):
            restore(self.snapshot, self.snapshot / "files" / "nested", digest)

    def test_file_tampering_and_extra_file_are_rejected(self):
        digest = create(self.source, self.selection, self.snapshot)
        target = self.snapshot / "files" / "archive" / "raw.bin"
        target.write_bytes(b"\x00\x02PADO")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify(self.snapshot, digest)
        target.write_bytes(b"\x00\x01PADO")
        (self.snapshot / "files" / "unexpected.txt").write_text("x")
        with self.assertRaisesRegex(ValueError, "unexpected snapshot file"):
            verify(self.snapshot, digest)

    def test_manifest_replacement_requires_independent_digest(self):
        digest = create(self.source, self.selection, self.snapshot)
        manifest = self.snapshot / "snapshot-manifest.json"
        manifest.write_bytes(manifest.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "independent record"):
            verify(self.snapshot, digest)

    def test_manifest_path_traversal_is_rejected_even_with_matching_digest(self):
        digest = create(self.source, self.selection, self.snapshot)
        self.assertEqual(len(verify(self.snapshot, digest)), 2)
        manifest = self.snapshot / "snapshot-manifest.json"
        document = json.loads(manifest.read_text(encoding="utf-8"))
        document["files"][0]["path"] = "../escape"
        altered = json.dumps(document).encode("utf-8")
        manifest.write_bytes(altered)
        with self.assertRaisesRegex(ValueError, "unsafe snapshot path"):
            verify(self.snapshot, hashlib.sha256(altered).hexdigest())

    def test_traversal_secret_and_symlink_are_rejected(self):
        for candidate in ("../outside", "/absolute", "ros\\approved.json",
                          "ros/id_ed25519", "ros/hmac.key"):
            with self.subTest(candidate=candidate):
                self.selection.write_text(json.dumps({"version": 1,
                                                      "files": [candidate]}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    create(self.source, self.selection, self.snapshot)
                self.assertFalse(self.snapshot.exists())
        link = self.source / "ros" / "link.json"
        try:
            link.symlink_to(self.source / "ros" / "approved.json")
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        self.selection.write_text(json.dumps({"version": 1,
                                              "files": ["ros/link.json"]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "symlink"):
            create(self.source, self.selection, self.snapshot)


if __name__ == "__main__":
    unittest.main()
