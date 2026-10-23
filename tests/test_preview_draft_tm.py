"""Draft TM preview requires explicit ranges and bounded, valid samples."""

import unittest
import tempfile
from pathlib import Path

from tools.pado_packet_codec import encode_tm
from tools.preview_draft_tm import build_snapshot, publish_snapshot
from services.webapp.tm_preview import load_snapshot
from tools.stage_fds_outbox import ROOT


STAMP = 1_800_000_000_000
RULES = {"sensors": {"EPS_PDU1.bus_voltage_v": {
    "id": "bus_voltage", "name": "버스 전압 시험", "group": "EPS", "unit": "V",
    "lower": 4.5, "upper": 5.5}}}
FIELDS = {"channel_enabled_bits": 3, "latchup_bits": 0,
          "bus_voltage_v": 5.1, "temperature_c": 22.0, "uptime_s": 10}


class DraftPreviewTests(unittest.TestCase):
    def test_explicit_limits_produce_ros_test_contract(self):
        packets = [encode_tm("EPS_PDU1", 1, STAMP, FIELDS),
                   encode_tm("EPS_PDU1", 2, STAMP + 1000,
                             {**FIELDS, "bus_voltage_v": 5.7})]
        snapshot = build_snapshot(packets, RULES, received_ms=STAMP + 2000)
        self.assertTrue(snapshot["non_operational"])
        self.assertEqual(len(snapshot["sensors"][0]["samples"]), 2)
        self.assertGreater(snapshot["sensors"][0]["samples"][1]["value"],
                           snapshot["sensors"][0]["upper"])
        self.assertIsNone(snapshot["attitude"])

    def test_absent_limits_or_duplicate_sample_are_rejected(self):
        packet = encode_tm("EPS_PDU1", 1, STAMP, FIELDS)
        with self.assertRaises(ValueError):
            build_snapshot([packet], {"sensors": {}}, received_ms=STAMP)
        with self.assertRaises(ValueError):
            build_snapshot([packet, packet], RULES, received_ms=STAMP)
        with self.assertRaises(ValueError):
            build_snapshot([packet], RULES, received_ms=STAMP - 1000)

    def test_published_snapshot_is_ros_readable_and_replaced(self):
        packet = encode_tm("EPS_PDU1", 1, STAMP, FIELDS)
        first = build_snapshot([packet], RULES, received_ms=STAMP + 1000)
        (ROOT / "out").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "out") as folder:
            destination = Path(folder) / "test_tm_snapshot.json"
            publish_snapshot(first, destination)
            self.assertEqual(load_snapshot(destination)["sensors"][0]["samples"][0]["value"],
                             first["sensors"][0]["samples"][0]["value"])
            second = build_snapshot([encode_tm("EPS_PDU1", 2, STAMP + 2000,
                                               {**FIELDS, "bus_voltage_v": 5.7})],
                                    RULES, received_ms=STAMP + 3000)
            publish_snapshot(second, destination)
            self.assertEqual(load_snapshot(destination)["sensors"][0]["samples"][0]["value"],
                             second["sensors"][0]["samples"][0]["value"])
            self.assertEqual(list(Path(folder).iterdir()), [destination])


if __name__ == "__main__":
    unittest.main()
