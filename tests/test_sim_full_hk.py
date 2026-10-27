"""All synthetic equipment groups, packet bounds, and durable downlink receipts."""

import hashlib
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from services.sim_sat.emulator import (SatelliteSession, TmSpool, generate_housekeeping_batch,
                                       generate_packet_batch, parse_orbit_view)
from services.sim_sat.housekeeping import (SCALAR_FAMILIES, equipment_snapshot,
                                          mode_beacon_packets)
from tests.test_sim_sat_dispatcher import Gate
from tests.test_sim_sat_emulator import START, _view_document
from tests.test_sim_sat_store_forward import _Wire
from tools.pado_packet_codec import decode, encode_tc, join_full_hk, load_catalog


class FullHousekeepingTests(unittest.TestCase):
    def setUp(self):
        self.view = parse_orbit_view(json.dumps(_view_document()).encode())
        self.stamp = int(START.timestamp()) * 1000

    def test_all_equipment_packets_and_complete_fragmented_json(self):
        packets, next_sequence = generate_housekeeping_batch(self.view, START, 65530,
                                                            include_full=True)
        decoded = [decode(packet) for packet in packets]
        expected = {entry["name"] for entry in load_catalog()["telemetry"]} - {
            "BEACON_CORE", "MODE_BEACON", "BEACON_INFO", "COMMAND_RESULT"}
        self.assertEqual({item["name"] for item in decoded}, expected)
        self.assertEqual(next_sequence, (65530+len(packets))&0xffff)
        self.assertTrue(all(len(packet) <= 200 for packet in packets))
        chunks = [packet for packet,item in zip(packets,decoded) if item["name"] == "FULL_HK_CHUNK"]
        document = json.loads(join_full_hk(chunks))
        self.assertEqual(document["schema"], "pado.sim-full-hk.v1")
        self.assertTrue(document["non_operational"])
        e = document["equipment"]
        self.assertEqual(set(e), {"P60_Dock","PDU_200_1","PDU_200_2","ACU_200","BPX",
                                 "CubeADCS","OEM719A","A3200","AX100","SRS_4","CREST","SIREN"})
        self.assertEqual(len(e["P60_Dock"]["c_out"]), 13)
        self.assertEqual(len(e["PDU_200_2"]["c_out"]), 9)
        self.assertEqual(len(e["ACU_200"]["c_in"]), 6)
        self.assertEqual(len(e["CubeADCS"]["t3_estimation_data"]), 42)
        self.assertFalse(e["OEM719A"]["vendor_binary_available"])
        self.assertFalse(e["SRS_4"]["payload_rx_enabled"])

    def test_normal_beacon_compact_status_and_identity_are_separate(self):
        core, _ = generate_packet_batch(self.view, START, 0)
        packets = mode_beacon_packets(core, 65535)
        mode, info = map(decode, packets)
        self.assertEqual([len(packet) for packet in packets], [197,93])
        self.assertEqual(mode["name"], "MODE_BEACON")
        self.assertEqual(mode["fields"]["mode"], 3)
        self.assertEqual(mode["fields"]["format_version"], 1)
        self.assertEqual(mode["sequence"], 65535)
        self.assertEqual(info["sequence"], 0)
        self.assertEqual(mode["fields"]["x_m"], decode(core[2])["fields"]["x_m"])
        self.assertIn("SIM_PADO_SAT", info["fields"]["identity"])
        self.assertIn("pado@khu.ac.kr", info["fields"]["identity"])

    def test_equipment_analog_values_move_smoothly(self):
        values = []
        for seconds in (0,5):
            core, _ = generate_packet_batch(self.view, START+timedelta(seconds=seconds), 0)
            values.append(equipment_snapshot(core)["equipment"])
        self.assertLess(abs(values[0]["P60_Dock"]["batt_v"]-values[1]["P60_Dock"]["batt_v"]), .01)
        self.assertLess(abs(values[0]["ACU_200"]["c_in"][2]-values[1]["ACU_200"]["c_in"][2]), .01)
        self.assertLess(abs(values[0]["PDU_200_2"]["temp"]-values[1]["PDU_200_2"]["temp"]), .1)

    def test_spool_upgrade_preserves_old_samples_and_full_profile_persists(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)/"spool.sqlite3"
            legacy = TmSpool(path)
            legacy.queue_tick(self.view, START)
            old_packets = [packet for _,packet in legacy.iter_pending()]
            legacy.close()
            full = TmSpool(path, full_housekeeping=True)
            full.queue_tick(self.view, START+timedelta(seconds=5))
            self.assertEqual([p for _,p in full.iter_pending(3)], old_packets)
            full_count = full.pending_count()
            self.assertGreater(full_count, 3+len(SCALAR_FAMILIES))
            self.assertFalse(full.queue_tick(self.view, START+timedelta(seconds=5)))
            full.queue_tick(self.view, START+timedelta(seconds=10))
            self.assertEqual(full.pending_count(), full_count+len(SCALAR_FAMILIES))
            full.close()
            reopened = TmSpool(path)
            self.assertTrue(reopened.full_housekeeping)
            self.assertEqual(reopened.last_full_hk_s, int(START.timestamp())+5)
            reopened.close()

    def test_only_durable_matching_receipts_release_snapshot(self):
        with tempfile.TemporaryDirectory() as name:
            spool = TmSpool(Path(name)/"spool.sqlite3")
            try:
                spool.queue_tick(self.view, START)
                session = SatelliteSession(spool)
                wire, gate = _Wire(), Gate()
                command = encode_tc("hk_dump", 1, self.stamp+20_000,
                                    {"start_utc_ms":0,"end_utc_ms":0})
                session.handle_tc(command,wire,gate,now_ms=self.stamp+20_000)
                self.assertEqual(session.flush_requested(wire,gate,clock_ms=lambda:self.stamp+20_000),3)
                self.assertEqual(spool.pending_count(),3)
                self.assertEqual(session.flush_requested(wire,gate,clock_ms=lambda:self.stamp+20_000),0)
                session.handle_uplink(b"PSR1"+b"?"*32,wire,gate,now_ms=self.stamp+20_000)
                self.assertEqual(spool.pending_count(),3)
                for packet in wire.frames[1:]:
                    session.handle_uplink(b"PSR1"+hashlib.sha256(packet).digest(),wire,gate,
                                          now_ms=self.stamp+20_000)
                self.assertEqual(spool.pending_count(),0)
                self.assertIsNone(session.dump_window)
            finally:
                spool.close()

    def test_disconnect_preserves_unacknowledged_pdus_for_new_request(self):
        with tempfile.TemporaryDirectory() as name:
            spool = TmSpool(Path(name)/"spool.sqlite3")
            try:
                spool.queue_tick(self.view, START)
                session = SatelliteSession(spool)
                wire, gate = _Wire(), Gate()
                for sequence in (1,2):
                    session.handle_tc(encode_tc("hk_dump",sequence,self.stamp+20_000,
                        {"start_utc_ms":0,"end_utc_ms":0}),wire,gate,now_ms=self.stamp+20_000)
                    self.assertEqual(session.flush_requested(wire,gate,clock_ms=lambda:self.stamp+20_000),3)
                    session.disconnect()
                    self.assertEqual(spool.pending_count(),3)
                    self.assertEqual(session.inflight,{})
                self.assertEqual(len(wire.frames),8)
            finally:
                spool.close()


if __name__ == "__main__":
    unittest.main()
