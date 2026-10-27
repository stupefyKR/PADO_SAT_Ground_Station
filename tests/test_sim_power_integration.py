"""EPS state follows durable sample generation and one shared telemetry budget."""

import hashlib
import json
import sqlite3
import struct
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from services.sim_sat.emulator import (SatelliteSession, TmSpool,
    generate_housekeeping_batch, parse_orbit_view)
from services.sim_sat.housekeeping import equipment_snapshot
from services.sim_ground.receiver import TmReceiver, load_limits
from tests.test_sim_sat_dispatcher import Gate
from tests.test_sim_sat_emulator import START, _view_document
from tests.test_sim_sat_store_forward import _Wire
from tools.pado_packet_codec import decode, join_full_hk


class PowerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.view = parse_orbit_view(json.dumps(_view_document()).encode())
        output = Path(__file__).resolve().parents[1]/"out"
        output.mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=output)
        self.addCleanup(self.folder.cleanup)

    def spool(self, name="spool", **kwargs):
        value = TmSpool(Path(self.folder.name)/(name+".sqlite3"),
                        full_housekeeping=True, **kwargs)
        self.addCleanup(value.close)
        return value

    def test_scalar_full_housekeeping_and_beacon_share_committed_power(self):
        spool = self.spool()
        spool.queue_tick(self.view, START)
        power = spool.power_sample
        decoded = {decode(packet)["name"]: decode(packet)
                   for _,packet in spool.iter_pending()}
        self.assertEqual(decoded["EPS_POWER"]["fields"]["mode"], 2)
        self.assertEqual(decoded["EPS_PDU1"]["fields"]["bus_voltage_v"], 5.)
        self.assertAlmostEqual(decoded["EPS_P60"]["fields"]["battery_voltage_v"],
                               power["battery_voltage_v"], places=5)
        self.assertAlmostEqual(decoded["EPS_POWER"]["fields"]["battery_soc_pct"],
                               power["soc_pct"], places=5)
        chunks = [packet for _,packet in spool.iter_pending()
                  if decode(packet)["name"] == "FULL_HK_CHUNK"]
        snapshot = json.loads(join_full_hk(chunks))
        self.assertEqual(snapshot["power_budget"], power)
        self.assertAlmostEqual(sum(snapshot["equipment"]["ACU_200"]["power"]),
                               power["solar_raw_w"], places=7)
        self.assertEqual(snapshot["equipment"]["BPX"]["vbatt"], power["battery_voltage_v"])
        self.assertEqual(snapshot["equipment"]["BPX"]["batt_temp1"],power["battery_temperature_c"])
        self.assertEqual(snapshot["equipment"]["P60_Dock"]["batt_c"], power["battery_current_a"])
        before = spool.power_model.snapshot()
        wire = _Wire()
        session = SatelliteSession(spool)
        for offset in (1,2):
            session.send_beacon(wire, Gate(), now_ms=int(START.timestamp()*1000)+offset*1000,
                                view=self.view)
        self.assertEqual(spool.power_model.snapshot(), before)
        beacons = [decode(packet) for packet in wire.frames if decode(packet)["name"]=="MODE_BEACON"]
        self.assertEqual(len(beacons),2)
        for beacon in beacons:
            self.assertEqual(beacon["fields"]["mode"], 2)
            self.assertAlmostEqual(beacon["fields"]["battery_voltage_v"],
                                   power["battery_voltage_v"], places=5)
            self.assertAlmostEqual(beacon["fields"]["battery_charge_current_a"],
                                   power["charge_current_a"], places=5)

    def test_restart_restores_state_even_after_all_stored_tm_is_delivered(self):
        spool = self.spool()
        control = self.spool("control")
        for offset in (0,5,10):
            for current in (spool,control):
                current.queue_tick(self.view, START+timedelta(seconds=offset))
        spool.mark_sent([row_id for row_id,_ in spool.iter_pending()])
        self.assertEqual(spool.pending_count(),0)
        path, state = spool.path, spool.power_model.snapshot()
        spool.close()
        reopened = TmSpool(path, full_housekeeping=True)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.power_model.snapshot(),state)
        for current in (reopened,control):
            current.queue_tick(self.view, START+timedelta(seconds=15))
        self.assertEqual(reopened.power_model.snapshot(),control.power_model.snapshot())
        self.assertEqual(reopened.power_sample,control.power_sample)

    def test_sqlite_commit_failure_rolls_back_tm_cursor_and_battery(self):
        spool = self.spool()
        spool.queue_tick(self.view, START)
        count = spool.pending_count()
        before = spool.power_model.snapshot()
        saved = spool.connection.execute("SELECT document FROM power_state").fetchone()[0]
        spool.connection.execute("CREATE TRIGGER power_failure BEFORE UPDATE ON power_state "
            "BEGIN SELECT RAISE(ABORT,'test failed battery commit'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            spool.queue_tick(self.view, START+timedelta(seconds=5))
        self.assertEqual(spool.pending_count(),count)
        self.assertEqual(spool.last_generated_s,int(START.timestamp()))
        self.assertEqual(spool.power_model.snapshot(),before)
        self.assertEqual(spool.connection.execute("SELECT document FROM power_state").fetchone()[0],saved)
        spool.connection.execute("DROP TRIGGER power_failure")
        self.assertTrue(spool.queue_tick(self.view, START+timedelta(seconds=5)))
        self.assertFalse(spool.queue_tick(self.view, START+timedelta(seconds=5)))

    def test_packet_encoding_failure_does_not_consume_battery_energy(self):
        spool = self.spool()
        spool.queue_tick(self.view, START)
        before = spool.power_model.snapshot()
        with patch("services.sim_sat.emulator.additional_packets", side_effect=ValueError("bad packet")):
            with self.assertRaises(ValueError):
                spool.queue_tick(self.view, START+timedelta(seconds=5))
        self.assertEqual(spool.power_model.snapshot(),before)
        self.assertEqual(spool.last_generated_s,int(START.timestamp()))

    def test_refresh_of_fds_truth_does_not_reset_battery(self):
        spool, control = self.spool(), self.spool("control")
        for offset in range(0,60,5):
            for current in (spool,control):
                current.queue_tick(self.view, START+timedelta(seconds=offset))
        fresh = replace(self.view, input_epoch_s=START.timestamp()+60,
                        start_s=int(START.timestamp())+60,
                        samples=self.view.samples[1:], times_s=self.view.times_s[1:])
        spool.queue_tick(fresh,START+timedelta(seconds=60))
        control.queue_tick(self.view,START+timedelta(seconds=60))
        # Interpolation at the edge can differ slightly; the persistent energy
        # remains continuous rather than jumping back to initial SOC.
        self.assertAlmostEqual(spool.power_sample["soc"],control.power_sample["soc"],places=7)
        self.assertNotEqual(spool.power_sample["soc"],.7)

    def test_missing_truth_history_keeps_energy_and_marks_the_unmodelled_gap(self):
        spool = self.spool()
        spool.queue_tick(self.view,START)
        before = spool.power_sample["stored_energy_wh"]
        fresh = replace(self.view,input_epoch_s=START.timestamp()+60,
                        start_s=int(START.timestamp())+60,
                        samples=self.view.samples[1:],times_s=self.view.times_s[1:])
        spool.queue_tick(fresh,START+timedelta(seconds=60))
        self.assertEqual(spool.power_sample["stored_energy_wh"],before)
        self.assertEqual(spool.power_model.snapshot()["unmodelled_seconds"],60.)

    def test_explicit_payload_mode_changes_loads_and_rails(self):
        spool = self.spool(power_mode="crest")
        spool.queue_tick(self.view,START)
        sample = spool.power_sample
        self.assertEqual(sample["device_loads_w"]["crest"],2.5)
        self.assertEqual(sample["device_loads_w"]["s_band"],0.)
        core = spool.latest_core()
        snapshot = equipment_snapshot(core,power_sample=sample)
        self.assertEqual(snapshot["mode"],4)
        self.assertEqual(snapshot["equipment"]["CREST"]["voltage_v"],12.)
        self.assertAlmostEqual(snapshot["equipment"]["CREST"]["current_a"],2.5/12)
        self.assertEqual(snapshot["equipment"]["SIREN"]["hardware_state"],0)
        self.assertTrue(all(len(packet)<=200 for _,packet in spool.iter_pending()))

    def test_old_thirteen_family_backlog_is_receipted_before_new_power_arrives(self):
        root = Path(self.folder.name)
        inbox = root/"n11"
        inbox.mkdir()
        target = root/"test_tm_snapshot.json"
        limits = load_limits(Path(__file__).resolve().parents[1]/
                             "services/sim_ground/full_test_limits.json")
        old_batch = tuple(packet for packet in generate_housekeeping_batch(self.view,START,0)[0]
                          if decode(packet)["name"] != "EPS_POWER")
        new_batch = generate_housekeeping_batch(self.view,START+timedelta(seconds=5),256)[0]
        test = self

        class StagedStream:
            def __init__(self):
                self.stage = 0
                self.data = b"".join(struct.pack(">I",len(p))+p for p in old_batch)
                self.receipts = []

            def recv(self,size):
                if not self.data and self.stage == 0:
                    test.assertFalse(target.exists())
                    test.assertEqual(len(self.receipts),len(old_batch))
                    self.data = b"".join(struct.pack(">I",len(p))+p for p in new_batch)
                    self.stage = 1
                result,self.data = self.data[:size],self.data[size:]
                return result

            def sendall(self,data):
                self.receipts.append(data[4:])

        receiver = TmReceiver(target,inbox,limits)
        receiver.contact_gate = Gate()
        wire = StagedStream()
        with patch("services.sim_ground.receiver.time.time",return_value=START.timestamp()+20):
            self.assertEqual(receiver.process_connection(wire),len(old_batch)+len(new_batch))
        self.assertTrue(target.exists())
        expected = [b"PSR1"+hashlib.sha256(p).digest() for p in (*old_batch,*new_batch)]
        self.assertEqual(wire.receipts,expected)


if __name__ == "__main__":
    unittest.main()
