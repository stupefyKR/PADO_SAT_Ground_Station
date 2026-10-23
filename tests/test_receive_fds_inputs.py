import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.receive_fds_inputs import ingest_inbox, pull_n05
from tools.run_fds_pipeline import current_pair, verified_pair


SCRATCH = Path(__file__).resolve().parents[1] / "out"


def pair(inbox: Path, name: str, data: bytes, route: str) -> None:
    body = inbox / name
    body.write_bytes(data)
    (inbox / (name + ".manifest.json")).write_text(json.dumps({
        "file": name, "type": "NORAD_TLE" if route == "tle" else "GNSS",
        "from": "dgs-net" if route == "tle" else "ros", "to": "fds",
        "version": 1, "created": "2026-10-23T00:00:00Z",
        "sha256": hashlib.sha256(data).hexdigest(),
    }), encoding="ascii")


class IngressTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inbox = self.root / "inbox"
        self.inbox.mkdir()
        self.output = self.root / "out"

    def test_committed_pair_is_immutable_and_referenced_atomically(self):
        pair(self.inbox, "orbit-a.tle", b"a\n", "tle")
        first = ingest_inbox("tle", self.inbox, self.output)
        self.assertEqual(verified_pair(*current_pair(self.output, "tle"), "tle"), b"a\n")
        self.assertEqual(ingest_inbox("tle", self.inbox, self.output), first)
        pair(self.inbox, "orbit-b.tle", b"b\n", "tle")
        second = ingest_inbox("tle", self.inbox, self.output)
        self.assertNotEqual(second, first)
        self.assertEqual((first / "orbit-a.tle").read_bytes(), b"a\n")
        self.assertEqual(verified_pair(*current_pair(self.output, "tle"), "tle"), b"b\n")

    def test_corrupt_newest_pair_blocks_then_valid_pair_recovers(self):
        pair(self.inbox, "state-a.properties", b"first\n", "gnss")
        ingest_inbox("gnss", self.inbox, self.output)
        pair(self.inbox, "state-b.properties", b"second\n", "gnss")
        (self.inbox / "state-b.properties").write_bytes(b"tampered\n")
        with self.assertRaisesRegex(ValueError, "rejected"):
            ingest_inbox("gnss", self.inbox, self.output)
        with self.assertRaisesRegex(ValueError, "blocked"):
            current_pair(self.output, "gnss")
        pair(self.inbox, "state-c.properties", b"third\n", "gnss")
        ingest_inbox("gnss", self.inbox, self.output)
        self.assertFalse((self.output / "blocked.txt").exists())
        self.assertEqual(verified_pair(*current_pair(self.output, "gnss"), "gnss"), b"third\n")

    def test_partial_upload_is_not_committed(self):
        (self.inbox / "state.properties.partial").write_bytes(b"partial")
        (self.inbox / "state.properties.manifest.json.partial").write_bytes(b"partial")
        self.assertIsNone(ingest_inbox("gnss", self.inbox, self.output))
        self.assertIsNone(current_pair(self.output, "gnss")[0])

    def test_existing_snapshot_tamper_cannot_be_republished(self):
        pair(self.inbox, "orbit.tle", b"valid\n", "tle")
        snapshot = ingest_inbox("tle", self.inbox, self.output)
        (snapshot / "orbit.tle").write_bytes(b"changed\n")
        with self.assertRaisesRegex(ValueError, "rejected"):
            ingest_inbox("tle", self.inbox, self.output)
        with self.assertRaisesRegex(ValueError, "blocked"):
            current_pair(self.output, "tle")

    def test_n05_uses_authenticated_exchange_client(self):
        class Settings:
            exchange_bin = Path("/usr/local/bin/gs_fds_exchange")
            n05_host = "dgs-net"
            n05_port = 2222
            n05_user = "fds-read"
            n05_key = Path("/run/secrets/n05.key")
            n05_host_key_sha256 = "a" * 64
            n05_remote_dir = "/outbox/to_fds"
            n05_filename = "tle.tle"

        def fake_download(command, **kwargs):
            self.assertEqual(command[1:5], ["pull-n05", "dgs-net", "2222", "fds-read"])
            self.assertEqual(Path(command[5]).name, "n05.key")
            self.assertEqual(command[6], "a" * 64)
            pair(Path(command[-1]), "tle.tle", b"n05\n", "tle")

        with patch("tools.receive_fds_inputs.subprocess.run", side_effect=fake_download):
            pull_n05(Settings, self.output)
        self.assertEqual(verified_pair(*current_pair(self.output, "tle"), "tle"), b"n05\n")


if __name__ == "__main__":
    unittest.main()
