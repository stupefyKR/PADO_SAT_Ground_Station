"""EPS state follows durable sample generation and one shared telemetry budget."""

import hashlib
import io
import json
import os
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
from services.spacecraft_config import ROOT, get_power_profile, load_settings
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

    def shared_settings(self):
        path = Path(self.folder.name)/"spacecraft.json"
        document = load_settings(ROOT/"config/spacecraft.example.json")
        path.write_text(json.dumps(document),encoding="utf-8")
        patcher = patch.dict(os.environ,{"PADO_SPACECRAFT_CONFIG":str(path)})
        patcher.start()
        self.addCleanup(patcher.stop)
        return path,document

    def test_physical_edit_applies_next_tick_without_resetting_or_rewriting_tm(self):
        path,document = self.shared_settings()
        spool,control = self.spool(),self.spool("control",power_profile=get_power_profile(document))
        for current in (spool,control):
            current.queue_tick(self.view,START)
        old_packets = list(spool.iter_pending())
        before_hash = spool.power_sample["power_profile_sha256"]
        document["power"]["solar"]["cell_types"]["DSP"]["voltage_v"] = 2.5
        document["power"]["battery"]["initial_soc"] = .1
        path.write_text(json.dumps(document),encoding="utf-8")
        for current in (spool,control):
            current.queue_tick(self.view,START+timedelta(seconds=5))
        self.assertAlmostEqual(spool.power_sample["stored_energy_wh"],
                               control.power_sample["stored_energy_wh"],places=10)
        self.assertGreater(spool.power_sample["solar_raw_w"],control.power_sample["solar_raw_w"])
        self.assertEqual([bank["voltage_v"] for bank in spool.power_sample["banks"]],[10.,10.,8.72])
        self.assertNotEqual(spool.power_sample["power_profile_sha256"],before_hash)
        change = spool.power_sample["configuration_change"]
        self.assertEqual(change["clamped_energy_wh"],0.)
        self.assertEqual(change["applied_utc_s"],int(START.timestamp())+5)
        self.assertEqual(list(spool.iter_pending())[:len(old_packets)],old_packets)

    def test_limits_only_edit_keeps_battery_profile_and_continuity(self):
        path,document = self.shared_settings()
        spool = self.spool()
        spool.queue_tick(self.view,START)
        fingerprint = spool.power_sample["power_profile_sha256"]
        key = next(iter(document["sensor_limits"]))
        document["sensor_limits"][key]["upper"] += 1
        path.write_text(json.dumps(document),encoding="utf-8")
        spool.queue_tick(self.view,START+timedelta(seconds=5))
        self.assertEqual(spool.power_sample["power_profile_sha256"],fingerprint)
        self.assertNotIn("configuration_change",spool.power_sample)
        self.assertEqual(spool.power_sample["integration_quality"],"continuous")

    def test_invalid_live_edit_keeps_last_valid_settings_and_logs_once(self):
        path,_ = self.shared_settings()
        spool = self.spool()
        spool.queue_tick(self.view,START)
        fingerprint = spool.power_sample["power_profile_sha256"]
        path.write_text('{"power":',encoding="utf-8")
        errors = io.StringIO()
        with patch("services.sim_sat.emulator.sys.stderr",errors):
            for offset in (5,10):
                self.assertTrue(spool.queue_tick(self.view,START+timedelta(seconds=offset)))
        self.assertEqual(spool.power_sample["power_profile_sha256"],fingerprint)
        self.assertEqual(errors.getvalue().count("using last valid"),1)
        self.assertGreater(spool.power_sample["stored_energy_wh"],70)
        with self.assertRaisesRegex(ValueError,"spacecraft_config"):
            TmSpool(Path(self.folder.name)/"invalid.sqlite3",full_housekeeping=True)

    def test_restart_adopts_changed_settings_at_boundary_preserving_energy_and_counters(self):
        path,document = self.shared_settings()
        spool = self.spool()
        control = self.spool("control",power_profile=get_power_profile(document))
        for offset in (0,5):
            for current in (spool,control):
                current.queue_tick(self.view,START+timedelta(seconds=offset))
        state = spool.power_model.snapshot()
        spool.close()
        document["power"]["devices"]["battery_auxiliary"]["power_w_by_mode"]["standby"] = 1.
        document["power"]["battery"]["initial_soc"] = .2
        path.write_text(json.dumps(document),encoding="utf-8")
        reopened = TmSpool(spool.path,full_housekeeping=True)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.power_model.snapshot(),state)
        for current in (reopened,control):
            current.queue_tick(self.view,START+timedelta(seconds=10))
        self.assertEqual(reopened.power_sample["stored_energy_wh"],control.power_sample["stored_energy_wh"])
        self.assertEqual(reopened.power_sample["charge_throughput_ah"],control.power_sample["charge_throughput_ah"])
        self.assertAlmostEqual(reopened.power_sample["load_w"],control.power_sample["load_w"]-5)

    def test_capacity_reduction_clamps_energy_and_exposes_the_change(self):
        path,document = self.shared_settings()
        spool = self.spool()
        spool.queue_tick(self.view,START)
        document["power"]["battery"]["capacity_wh"] = 50
        path.write_text(json.dumps(document),encoding="utf-8")
        spool.queue_tick(self.view,START+timedelta(seconds=5))
        self.assertEqual(spool.power_sample["stored_energy_wh"],50)
        self.assertGreater(spool.power_sample["configuration_change"]["clamped_energy_wh"],20)
        self.assertEqual(spool.power_sample["soc"],1.)

    def test_legacy_state_without_saved_profile_migrates_without_inventing_past_energy(self):
        path,document = self.shared_settings()
        spool = self.spool()
        spool.queue_tick(self.view,START)
        saved = json.loads(spool.connection.execute("SELECT document FROM power_state").fetchone()[0])
        old_state = saved["state"]
        # Pre-settings releases saved state+sample without the physical profile
        # needed to integrate its previous interval after a configuration edit.
        del saved["power_profile"]
        old_state["profile_sha256"] = "0"*64
        spool.connection.execute("UPDATE power_state SET document=?",(json.dumps(saved),))
        spool.connection.commit()
        spool.close()
        document["power"]["battery"]["initial_soc"] = .1
        path.write_text(json.dumps(document),encoding="utf-8")
        reopened = TmSpool(spool.path,full_housekeeping=True)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.power_model.snapshot()["energy_wh"],old_state["energy_wh"])
        self.assertEqual(reopened.power_model.snapshot()["temperature_c"],old_state["temperature_c"])
        reopened.queue_tick(self.view,START+timedelta(seconds=5))
        self.assertEqual(reopened.power_sample["stored_energy_wh"],old_state["energy_wh"])
        self.assertEqual(reopened.power_sample["integration_quality"],"gap_not_integrated")
        self.assertFalse(reopened.power_sample["configuration_change"]["past_interval_available"])
        self.assertEqual(reopened.power_sample["unmodelled_seconds"],5.)

    def test_live_edit_incompatible_with_orbit_keeps_sampling_previous_model(self):
        path,document = self.shared_settings()
        spool = self.spool()
        spool.queue_tick(self.view,START)
        fingerprint = spool.power_sample["power_profile_sha256"]
        document["power"]["solar"]["earth_radius_m"] = 7000000.
        path.write_text(json.dumps(document),encoding="utf-8")
        errors = io.StringIO()
        with patch("services.sim_sat.emulator.sys.stderr",errors):
            for offset in (5,10):
                self.assertTrue(spool.queue_tick(self.view,START+timedelta(seconds=offset)))
        self.assertEqual(spool.power_sample["power_profile_sha256"],fingerprint)
        self.assertEqual(errors.getvalue().count("using last valid"),1)
        self.assertNotIn("configuration_change",spool.power_sample)

    def test_configured_device_and_bus_voltages_reach_scalar_full_and_beacon_tm(self):
        path,document = self.shared_settings()
        telemetry = document["power"]["telemetry"]
        telemetry["regulated_bus_voltage_v"] = 5.2
        telemetry["logic_supply_voltage_v"] = 3.2
        telemetry["battery_mode_soc"]["safe"] = .75
        document["power"]["devices"]["crest"]["voltage_v"] = 11.
        document["power"]["devices"]["gnss_receiver"]["voltage_v"] = 3.1
        path.write_text(json.dumps(document),encoding="utf-8")
        spool = self.spool(power_mode="crest")
        spool.queue_tick(self.view,START)
        decoded = {decode(packet)["name"]:decode(packet)["fields"] for _,packet in spool.iter_pending()}
        self.assertAlmostEqual(decoded["EPS_PDU1"]["bus_voltage_v"],5.2,places=5)
        self.assertAlmostEqual(decoded["EPS_PDU2"]["bus_voltage_v"],5.2,places=5)
        self.assertAlmostEqual(decoded["CREST_HEALTH"]["voltage_v"],11.)
        self.assertAlmostEqual(decoded["CREST_HEALTH"]["current_a"],2.5/11.,places=6)
        self.assertEqual(decoded["EPS_P60"]["battery_mode"],2)
        chunks = [packet for _,packet in spool.iter_pending() if decode(packet)["name"]=="FULL_HK_CHUNK"]
        equipment = json.loads(join_full_hk(chunks))["equipment"]
        self.assertEqual(equipment["P60_Dock"]["v_out"][1],11.)
        self.assertEqual(equipment["OEM719A"]["HWMONITOR"]["supply_voltage_v"],3.1)
        self.assertEqual(equipment["PDU_200_1"]["vcc"],3.2)
        wire = _Wire()
        SatelliteSession(spool).send_beacon(wire,Gate(),now_ms=int(START.timestamp())*1000,view=self.view)
        beacon = next(decode(packet) for packet in wire.frames if decode(packet)["name"]=="MODE_BEACON")
        self.assertEqual(beacon["fields"]["p60_battery_mode"],2)

    def test_old_thirteen_family_backlog_is_receipted_before_new_power_arrives(self):
        root = Path(self.folder.name)
        inbox = root/"n11"
        inbox.mkdir()
        target = root/"test_tm_snapshot.json"
        limits = load_limits(Path(__file__).resolve().parents[1]/
                             "config/spacecraft.example.json")
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
