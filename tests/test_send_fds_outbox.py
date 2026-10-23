"""Retrying N13/N15 transport keeps local success distinct from receiver ACK."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.send_fds_outbox import cycle, load_peers
from tools.stage_fds_outbox import FILES, ROOT, stage


class SenderTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "out").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "out")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "fds-20261023T100000Z-1234abcd"
        self.run.mkdir()
        (self.run / "run.properties").write_text(
            "schema=fds.run.v1\nquality=PRELIMINARY_NOT_FOR_OPERATIONS\n", encoding="ascii")
        for name, kind, recipient, _, header in FILES:
            body = header + b"test\n"
            (self.run / name).write_bytes(body)
            (self.run / (name + ".manifest.json")).write_text(json.dumps({
                "file": name, "type": kind, "from": "fds", "to": recipient,
                "version": 1, "created": "2026-10-23T10:00:00Z",
                "sha256": hashlib.sha256(body).hexdigest()}), encoding="ascii")
        self.outbox = self.root / "outbox"
        self.receipts = self.root / "receipts"
        self.staged = stage(self.run, self.outbox)
        self.peers = {recipient: {"host": recipient, "port": 2222,
                                 "user": "fds", "private_key": "test-key",
                                 "host_key_sha256": "a" * 64,
                                 "remote_dir": "/inbox"}
                      for recipient in ("mps", "dgs-archive")}

    def test_three_pairs_upload_once_and_receipts_are_transport_only(self):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            self.assertTrue(kwargs["check"])
            self.assertEqual(kwargs["timeout"], 60)
        self.assertEqual(cycle(self.outbox, self.receipts, self.peers,
                               Path("gs_fds_exchange"), runner=runner), 3)
        self.assertEqual([command[1] for command in calls],
                         ["push-n13", "push-n15", "push-n15"])
        self.assertEqual(cycle(self.outbox, self.receipts, self.peers,
                               Path("gs_fds_exchange"), runner=runner), 0)
        self.assertEqual(len(calls), 3)
        markers = list((self.receipts / self.run.name).glob("*.transport.json"))
        self.assertEqual(len(markers), 3)
        self.assertTrue(all(json.loads(path.read_text())["application_ack"] is False
                            for path in markers))
        changed = {key: dict(value) for key, value in self.peers.items()}
        changed["mps"]["host"] = "different-peer"
        with self.assertRaisesRegex(ValueError, "receipt differs"):
            cycle(self.outbox, self.receipts, changed,
                  Path("gs_fds_exchange"), runner=runner)

    def test_failed_transfer_retries_and_tamper_is_blocked(self):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            if len(calls) == 2:
                raise subprocess.CalledProcessError(1, command)
        with self.assertRaises(subprocess.CalledProcessError):
            cycle(self.outbox, self.receipts, self.peers,
                  Path("gs_fds_exchange"), runner=runner)
        self.assertEqual(cycle(self.outbox, self.receipts, self.peers,
                               Path("gs_fds_exchange"), runner=runner), 2)
        self.assertEqual(len(calls), 4)
        body = next(self.staged.glob("contact_schedule-*.csv"))
        body.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "changed or has wrong route"):
            cycle(self.outbox, self.receipts, self.peers,
                  Path("gs_fds_exchange"), runner=runner)

    def test_private_peer_configuration_requires_real_key_file(self):
        key = self.root / "test-private-key"
        key.write_text("test fixture only", encoding="ascii")
        settings = {name: {**peer, "private_key": str(key)}
                    for name, peer in self.peers.items()}
        source = self.root / "peers.json"
        source.write_text(json.dumps(settings), encoding="utf-8")
        self.assertEqual(set(load_peers(source)), {"mps", "dgs-archive"})
        settings["mps"]["host_key_sha256"] = "bad"
        source.write_text(json.dumps(settings), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid SFTP"):
            load_peers(source)


if __name__ == "__main__":
    unittest.main()
