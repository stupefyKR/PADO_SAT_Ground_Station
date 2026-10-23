"""Draft TM to verified N11 inbox contract, without a real uplink/downlink."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools.bridge_draft_gnss import bridge
from tools.pado_packet_codec import PacketError, encode_tm
from tools.receive_fds_inputs import ingest_inbox
from tools.run_fds_pipeline import current_pair, verified_pair
from tools.stage_fds_outbox import ROOT


STAMP = 1_800_000_000_000
FIELDS = {"norad_id": 25544, "solution_status": 1, "receiver_age_ms": 125,
          "x_m": 6_800_000.0, "y_m": 0.0, "z_m": 0.0,
          "vx_mps": 0.0, "vy_mps": 7_500.0, "vz_mps": 0.0}


class DraftBridgeTests(unittest.TestCase):
    def test_commits_a_manifest_backed_n11_pair(self):
        packet = encode_tm("GNSS_PVT", 3, STAMP, FIELDS)
        (ROOT / "out").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "out") as folder:
            inbox = Path(folder) / "inbox"
            target = Path(folder) / "current"
            body, manifest = bridge(packet, inbox, created=datetime(2026, 10, 23, tzinfo=timezone.utc))
            self.assertIn("norad_id=25544", verified_pair(body, manifest, "gnss").decode("ascii"))
            self.assertEqual(bridge(packet, inbox), (body, manifest))
            ingest_inbox("gnss", inbox, target)
            self.assertEqual(verified_pair(*current_pair(target, "gnss"), "gnss"),
                             body.read_bytes())

    def test_rejects_corrupt_or_bad_quality_without_committing(self):
        (ROOT / "out").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "out") as folder:
            inbox = Path(folder) / "inbox"
            packet = bytearray(encode_tm("GNSS_PVT", 3, STAMP, FIELDS))
            packet[22] ^= 1
            with self.assertRaises(PacketError):
                bridge(bytes(packet), inbox)
            bad = encode_tm("GNSS_PVT", 3, STAMP, {**FIELDS, "solution_status": 0})
            with self.assertRaises(PacketError):
                bridge(bad, inbox)
            self.assertFalse(inbox.exists())


if __name__ == "__main__":
    unittest.main()
