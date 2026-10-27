"""Beacon-only link -> ground TC -> accepted snapshot -> ROS/N11 publication."""

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from services.sim_ground.receiver import TmReceiver, load_limits
from services.sim_sat.contact import ContactGate
from services.sim_sat.emulator import (SatelliteSession, TmSpool, parse_orbit_view,
                                       read_test_tc)
from services.webapp.tm_preview import load_snapshot
from tests.test_sim_sat_emulator import START, _view_document
from tools.pado_packet_codec import decode, encode_tc


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name == "nt", "Real socketpair exercised by Linux CI")
class BeaconDumpRoundtripTests(unittest.TestCase):
    def test_beacon_wait_command_dump_new_samples_wait_and_los(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            document = _view_document()
            utc = lambda value: value.isoformat().replace("+00:00", "Z")
            document["stations"] = [{"id": "yonsei", "band": "UHF", "passes": [{
                "aos_utc": utc(START + timedelta(seconds=120)),
                "los_utc": utc(START + timedelta(seconds=540)),
            }]}]
            raw = json.dumps(document).encode()
            truth = root / "truth.json"
            truth.write_bytes(raw)
            view = parse_orbit_view(raw)
            gate = ContactGate(truth)
            now_ms = int((START + timedelta(seconds=180)).timestamp()) * 1000
            spool = TmSpool(root / "spool.sqlite3")
            for offset in (125, 130, 135):
                spool.queue_tick(view, START + timedelta(seconds=offset))
            session = SatelliteSession(spool)
            receiver = TmReceiver(root / "test_tm_snapshot.json", root / "n11",
                                  load_limits(ROOT / "services/sim_ground/test_limits.json"),
                                  truth)
            satellite, ground = socket.socketpair()
            satellite.settimeout(3)
            ground.settimeout(3)
            errors = []

            def receive():
                try:
                    receiver.process_connection(ground)
                except Exception as error:
                    errors.append(error)
                finally:
                    ground.close()

            def wait_for(predicate):
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if predicate():
                        return
                    time.sleep(0.01)
                self.fail("duplex workflow did not progress")

            worker = threading.Thread(target=receive, daemon=True)
            with patch("services.sim_ground.receiver.time.time", return_value=now_ms / 1000):
                worker.start()
                try:
                    wait_for(lambda: receiver._satellite_connected)
                    session.send_beacon(satellite, gate, now_ms=now_ms)
                    wait_for(lambda: (root / "test_beacon.json").exists())
                    self.assertFalse(receiver.snapshot.exists())
                    self.assertFalse((root / "n11").exists())
                    self.assertEqual(session.flush_requested(
                        satellite, gate, clock_ms=lambda: now_ms), 0)
                    self.assertEqual(spool.pending_count(), 9)

                    command = encode_tc("hk_dump", 19, now_ms,
                                        {"start_utc_ms": 0, "end_utc_ms": 0})
                    response = {}

                    def request():
                        try:
                            response["ack"] = receiver.submit_tc(command, timeout_s=3,
                                                                 now_ms=now_ms)
                        except Exception as error:
                            response["error"] = error

                    submitter = threading.Thread(target=request, daemon=True)
                    submitter.start()
                    self.assertEqual(read_test_tc(satellite), command)
                    session.handle_tc(command, satellite, gate, now_ms=now_ms)
                    submitter.join(timeout=3)
                    self.assertFalse(submitter.is_alive())
                    self.assertNotIn("error", response)
                    self.assertEqual(decode(response["ack"])["fields"]["result"], 0)
                    # ACK is accepted before any stored sensor TM arrives.
                    self.assertFalse(receiver.snapshot.exists())

                    # A new sample after command receipt is not part of that dump.
                    spool.queue_tick(view, START + timedelta(seconds=185))
                    self.assertEqual(session.flush_requested(
                        satellite, gate, clock_ms=lambda: now_ms), 9)
                    self.assertEqual(spool.pending_count(), 12)
                    for _ in range(9):
                        receipt = read_test_tc(satellite)
                        self.assertTrue(receipt.startswith(b"PSR1"))
                        session.handle_uplink(receipt, satellite, gate, now_ms=now_ms)
                    wait_for(lambda: receiver.snapshot.exists())
                    snapshot = load_snapshot(receiver.snapshot)
                    self.assertEqual(len(snapshot["sensors"][0]["samples"]), 3)
                    self.assertEqual(snapshot["attitude"]["frame"], "SIM_BODY_TO_ITRF_NADIR")
                    self.assertEqual(len(list((root / "n11").glob("*.manifest.json"))), 1)
                    self.assertEqual(spool.pending_count(), 3)
                    self.assertEqual(session.flush_requested(
                        satellite, gate, clock_ms=lambda: now_ms), 0)

                    # Losing contact cancels authorization without deleting TM.
                    after_los_ms = int((START + timedelta(seconds=540)).timestamp()) * 1000
                    self.assertFalse(session.send_beacon(satellite, gate,
                                                         now_ms=after_los_ms))
                    self.assertIsNone(session.dump_window)
                    self.assertEqual(spool.pending_count(), 3)
                finally:
                    satellite.close()
                    worker.join(timeout=3)
                    spool.close()
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
