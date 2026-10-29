"""A delayed contact preserves sensor sampling time through PDU, archive and UI JSON."""

import json
import struct
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from services.sim_ground.archive import list_packets, packet_detail
from services.sim_ground.receiver import TmReceiver, load_limits
from services.sim_sat.emulator import TmSpool, parse_orbit_view
from tests.test_sim_sat_dispatcher import Gate
from tests.test_sim_sat_emulator import START, _view_document
from tools.pado_packet_codec import decode


ROOT = Path(__file__).resolve().parents[1]


class MeasurementTimeTests(unittest.TestCase):
    def test_stored_downlink_keeps_measurement_utc_separate_from_reception(self):
        (ROOT / "out").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="sensor-time-", dir=ROOT / "out") as temporary:
            folder = Path(temporary)
            inbox = folder / "n11"
            inbox.mkdir()
            view = parse_orbit_view(json.dumps(_view_document()).encode())
            spool = TmSpool(folder / "spool.sqlite3", full_housekeeping=True)
            try:
                for offset in (0, 5):
                    spool.queue_tick(view, START + timedelta(seconds=offset))
                packets = [packet for _, packet in spool.iter_pending()]
            finally:
                spool.close()
            measured = [START, START + timedelta(seconds=5)]
            expected_ms = {int(stamp.timestamp()) * 1000 for stamp in measured}
            self.assertEqual({decode(packet)["utc_ms"] for packet in packets}, expected_ms)

            class StoredDownlink:
                def __init__(self):
                    self.data = b"".join(struct.pack(">I", len(packet)) + packet for packet in packets)
                    self.receipts = []

                def recv(self, size):
                    result, self.data = self.data[:size], self.data[size:]
                    return result

                def sendall(self, payload):
                    self.receipts.append(payload)

            target = folder / "test_tm_snapshot.json"
            receiver = TmReceiver(target, inbox, load_limits(
                ROOT / "config/spacecraft.example.json"))
            receiver.contact_gate = Gate()
            reception = START + timedelta(minutes=15)
            wire = StoredDownlink()
            with patch("services.sim_ground.receiver.time.time", return_value=reception.timestamp()):
                self.assertEqual(receiver.process_connection(wire), len(packets))
            self.assertEqual(len(wire.receipts), len(packets))

            snapshot = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["received_utc"], reception.strftime("%Y-%m-%dT%H:%M:%SZ"))
            expected_utc = [stamp.strftime("%Y-%m-%dT%H:%M:%SZ") for stamp in measured]
            self.assertEqual(len(snapshot["sensors"]), 20)
            for sensor in snapshot["sensors"]:
                self.assertEqual([sample["utc"] for sample in sensor["samples"]], expected_utc)
                self.assertNotEqual(sensor["samples"][-1]["utc"], snapshot["received_utc"])

            archive = folder / "received_tm.sqlite3"
            records = list_packets(archive, name="EPS_POWER")["items"]
            self.assertEqual(len(records), 2)
            for record in records:
                detail = packet_detail(archive, record["id"])
                self.assertIn(detail["header"]["utc_ms"], expected_ms)
                sampled = datetime.fromisoformat(detail["sampled_utc"].replace("Z", "+00:00"))
                received = datetime.fromisoformat(detail["received_utc"].replace("Z", "+00:00"))
                self.assertIn(sampled, measured)
                self.assertEqual(received, reception.astimezone(timezone.utc))
                self.assertLess(sampled, received)


if __name__ == "__main__":
    unittest.main()
