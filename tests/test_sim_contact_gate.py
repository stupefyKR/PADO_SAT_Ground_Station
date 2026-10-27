"""The virtual ground receiver only publishes TM during a Yonsei UHF PASS."""

import json
import math
import struct
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from services.sim_ground.receiver import ContactGate, TmReceiver, ReceiverError, load_limits
from tools.pado_packet_codec import encode_tc, encode_tm
from tests.test_sim_ground_receiver import BEACON


START = datetime(2027, 1, 15, 8, 0, tzinfo=timezone.utc)
START_MS = int(START.timestamp() * 1000)
ROOT = Path(__file__).resolve().parents[1]
LIMITS = ROOT / "services/sim_ground/test_limits.json"
EPS = {"channel_enabled_bits": 3, "latchup_bits": 0,
       "bus_voltage_v": 5.1, "temperature_c": 22.0, "uptime_s": 10}


def _utc(when: datetime) -> str:
    return when.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _truth_view() -> dict:
    samples = []
    for index in range(4):
        when = START + timedelta(seconds=index * 60)
        phase = index * 0.06
        samples.append([_utc(when), 6_878_000 * math.cos(phase),
                        6_878_000 * math.sin(phase), 0.0,
                        0.0, 0.0, 500_000.0, 1.0, 0.0, 0.0])
    return {"schema": "fds.orbit-view.v1",
            "quality": "PRELIMINARY_NOT_FOR_OPERATIONS", "source": "TLE",
            "norad_id": 99999, "input_epoch_utc": _utc(START),
            "start_utc": _utc(START), "end_utc": _utc(START + timedelta(minutes=3)),
            "sample_step_s": 60,
            "stations": [{"id": "yonsei", "band": "UHF", "passes": [
                {"aos_utc": _utc(START + timedelta(minutes=1)),
                 "los_utc": _utc(START + timedelta(minutes=2))}]},
                {"id": "kari", "band": "S_BAND", "passes": [
                    {"aos_utc": _utc(START + timedelta(seconds=10)),
                     "los_utc": _utc(START + timedelta(seconds=50))}]}],
            "samples": samples}


class _SocketBytes:
    def __init__(self, packet: bytes):
        self.raw = struct.pack(">I", len(packet)) + packet

    def recv(self, size: int) -> bytes:
        chunk = self.raw[:size]
        self.raw = self.raw[size:]
        return chunk


class SimContactGateTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "out").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "out")
        self.root = Path(self.temp.name)
        self.view = self.root / "orbit_view.json"
        self.view.write_text(json.dumps(_truth_view()), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_only_yonsei_uhf_aos_to_los_opens_downlink(self):
        gate = ContactGate(self.view)
        for instant, expected in ((START_MS + 10_000, False),
                                  (START_MS + 59_999, False),
                                  (START_MS + 60_000, True),
                                  (START_MS + 119_999, True),
                                  (START_MS + 120_000, False)):
            with self.subTest(instant=instant):
                self.assertEqual(gate.active(instant), expected)

    def test_missing_malformed_and_changed_truth_fail_closed(self):
        gate = ContactGate(self.root / "missing.json")
        self.assertFalse(gate.active(START_MS + 90_000))
        gate = ContactGate(self.view)
        self.assertTrue(gate.active(START_MS + 90_000))
        wrong = _truth_view()
        wrong["stations"][0]["band"] = "S_BAND"
        self.view.write_text(json.dumps(wrong), encoding="utf-8")
        self.assertFalse(gate.active(START_MS + 90_000))
        wrong = _truth_view()
        wrong["stations"][0]["passes"][0]["los_utc"] = _utc(
            START + timedelta(minutes=4))
        self.view.write_text(json.dumps(wrong), encoding="utf-8")
        self.assertFalse(gate.active(START_MS + 90_000))
        self.view.write_text(json.dumps(_truth_view()), encoding="utf-8")
        self.assertTrue(gate.active(START_MS + 90_000))
        self.view.unlink()
        self.assertFalse(gate.active(START_MS + 90_000))

    def test_receiver_drops_out_of_contact_tm_and_blocks_tc(self):
        receiver = TmReceiver(self.root / "test_tm_snapshot.json", self.root / "n11",
                              load_limits(LIMITS), self.view)
        packet = encode_tm("EPS_PDU1", 0, START_MS + 30_000, EPS)
        with patch("services.sim_ground.receiver.time.time",
                   return_value=(START_MS + 30_000) / 1000):
            self.assertEqual(receiver.process_connection(_SocketBytes(packet)), 0)
        self.assertFalse(receiver.snapshot.exists())
        beacon = encode_tm("BEACON_CORE", 0, START_MS + 30_000, BEACON)
        with patch("services.sim_ground.receiver.time.time",
                   return_value=(START_MS + 30_000) / 1000):
            self.assertEqual(receiver.process_connection(_SocketBytes(beacon)), 0)
        self.assertFalse(receiver.snapshot.with_name("test_beacon.json").exists())
        tc = encode_tc("hk_get", 1, START_MS + 30_000, {})
        with self.assertRaisesRegex(ReceiverError, "outside Yonsei UHF contact"):
            receiver.submit_tc(tc, now_ms=START_MS + 30_000)
        packet = encode_tm("EPS_PDU1", 0, START_MS + 90_000, EPS)
        with patch("services.sim_ground.receiver.time.time",
                   return_value=(START_MS + 90_000) / 1000):
            self.assertEqual(receiver.process_connection(_SocketBytes(packet)), 1)
        self.assertEqual(receiver.last_stamp_ms, START_MS + 90_000)


if __name__ == "__main__":
    unittest.main()
