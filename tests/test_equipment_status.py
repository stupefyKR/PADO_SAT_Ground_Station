"""Equipment callouts reflect real archived measurement time and limits."""

from contextlib import closing
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from services.sim_ground.archive import PacketArchive
from services.sim_sat.housekeeping import additional_packets, equipment_snapshot
from services.sim_sat.power import AU_M, PowerModel
from services.spacecraft_config import get_sensor_limits
from services.webapp.equipment import EquipmentError, equipment_status, load_layout
from services.webapp.settings import SettingsStore
from tools.pado_packet_codec import decode, encode_tm, split_full_hk


ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "config/equipment-layout.example.json"
NOW = 1_800_000_000_000
EPS = {"channel_enabled_bits": 3, "latchup_bits": 0, "bus_voltage_v": 5.0,
       "temperature_c": 22.0, "uptime_s": 10}


class EquipmentStatusTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "out").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="equipment-status-", dir=ROOT / "out")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "received_tm.sqlite3"
        self.archive = PacketArchive(self.path)
        self.addCleanup(self.archive.close)
        self.settings = json.loads((ROOT / "config/spacecraft.example.json").read_text(encoding="utf-8"))
        self.limits = get_sensor_limits(self.settings)
        self.base = json.loads(LAYOUT.read_text(encoding="utf-8"))
        self.sequence = 0
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop("PADO_EQUIPMENT_LAYOUT", None)

    def add_eps(self, *, sampled_ms=NOW - 5000, received_ms=NOW, **values):
        self.sequence += 1
        raw = encode_tm("EPS_PDU1", self.sequence, sampled_ms, {**EPS, **values})
        result = self.archive.append(raw, decode(raw), received_ms)
        return result, raw

    def add_full(self, *, sampled_ms=NOW - 600_000, received_ms=NOW,
                 temperature=28., record_id=1, incomplete=False, document=None):
        document = document or {"schema": "pado.sim-full-hk.v1", "non_operational": True,
            "sampled_utc_ms": sampled_ms, "equipment": {"OEM719A": {
                "HWMONITOR": {"temperature_c": temperature, "supply_voltage_v": 3.3},
                "BESTPOS": {"solution_status": 1}}}, "padding": "x" * 400}
        record = json.dumps(document, separators=(",", ":")).encode("utf-8")
        chunks = split_full_hk(record, record_id, sampled_ms, self.sequence)
        self.sequence += len(chunks)
        self.assertGreater(len(chunks), 1)
        for packet in chunks[:-1] if incomplete else chunks:
            self.archive.append(packet, decode(packet), received_ms)
        return chunks

    def status(self, *, now_ms=NOW, limits=None, **options):
        return equipment_status(self.path, layout_path=LAYOUT,
            limits=self.limits if limits is None else limits, now_s=now_ms / 1000, **options)

    @staticmethod
    def component(payload, identifier):
        return next(item for item in payload["equipment"] if item["id"] == identifier)

    @classmethod
    def reading(cls, payload, identifier, reading):
        return next(item for item in cls.component(payload, identifier)["readings"] if item["id"] == reading)

    def test_missing_packets_and_unmapped_parts_never_become_healthy(self):
        payload = self.status()
        self.assertEqual(payload["schema"], "pado.equipment-status.v1")
        self.assertEqual(payload["summary"]["alarms"], 0)
        self.assertTrue(all(item["status"] == "MISSING" for item in payload["equipment"]))
        for identifier in ("ifb", "satnogs", "uhf_antenna"):
            item = self.component(payload, identifier)
            self.assertEqual(item["readings"], [])
            self.assertEqual(item["telemetry_status"], "UNMAPPED")
        self.assertIsNone(self.reading(payload, "pdu1", "temperature")["value"])

    def test_delayed_older_packet_cannot_override_newer_measurement_or_raise_alarm(self):
        latest, _ = self.add_eps(temperature_c=24.)
        delayed, _ = self.add_eps(sampled_ms=NOW - 60_000, received_ms=NOW + 1000,
                                  temperature_c=85.)
        self.assertGreater(delayed["id"], latest["id"])
        payload = self.status()
        reading = self.reading(payload, "pdu1", "temperature")
        self.assertEqual(reading["value"], 24.)
        self.assertEqual(reading["source"]["id"], latest["id"])
        self.assertEqual(reading["freshness"], "CURRENT")
        self.assertEqual(reading["limit_state"], "IN_RANGE")
        self.assertEqual(payload["summary"]["alarms"], 0)

    def test_only_fresh_out_of_range_data_alarms_and_stale_values_keep_their_time(self):
        self.add_eps(temperature_c=80.)
        fresh = self.status()
        current = self.reading(fresh, "pdu1", "temperature")
        self.assertEqual(current["limit_state"], "OUT_OF_RANGE")
        self.assertEqual(self.component(fresh, "pdu1")["status"], "ALARM")
        stale = self.status(now_ms=NOW + 200_000)
        held = self.reading(stale, "pdu1", "temperature")
        self.assertEqual(held["value"], 80.)
        self.assertEqual(held["sampled_utc"], current["sampled_utc"])
        self.assertEqual(held["received_utc"], current["received_utc"])
        self.assertEqual(held["freshness"], "STALE")
        self.assertEqual(held["limit_state"], "INDETERMINATE")
        self.assertEqual(self.component(stale, "pdu1")["status"], "STALE")
        self.assertEqual(stale["summary"]["alarms"], 0)

    def test_fresh_values_without_a_matching_limit_are_unconfigured(self):
        self.add_eps()
        for limits in ({}, {"sensors": {"EPS_PDU1.temperature_c": {
                "lower": 0, "upper": 1, "unit": "V"}}}):
            reading = self.reading(self.status(limits=limits), "pdu1", "temperature")
            self.assertEqual(reading["freshness"], "CURRENT")
            self.assertEqual(reading["limit_state"], "UNCONFIGURED")
            self.assertIsNone(reading["lower"])
            self.assertIsNone(reading["upper"])

    def test_complete_full_hk_fallback_survives_a_newer_incomplete_record(self):
        self.add_full()
        # A series of interrupted downlinks must not hide the last complete snapshot.
        for index in range(9):
            self.add_full(sampled_ms=NOW - 9000 + index * 1000, temperature=90.,
                          record_id=2 + index, incomplete=True)
        payload = self.status()
        reading = self.reading(payload, "oem719a", "temperature")
        self.assertEqual(reading["value"], 28.)
        self.assertEqual(reading["source"]["packet_name"], "FULL_HK_CHUNK")
        self.assertEqual(reading["freshness"], "CURRENT")
        self.assertEqual(reading["max_age_s"], 1200)
        self.assertEqual(self.reading(payload, "oem719a", "voltage")["value"], 3.3)
        expired = self.reading(self.status(now_ms=NOW + 610_000), "oem719a", "temperature")
        self.assertEqual(expired["value"], 28.)
        self.assertEqual(expired["freshness"], "STALE")
        self.assertEqual(expired["limit_state"], "INDETERMINATE")

    def test_invalid_or_mistimed_full_hk_does_not_replace_a_previous_complete_record(self):
        self.add_full()
        for index, changes in enumerate(({"schema": "other.schema"}, {"non_operational": False},
                                         {"sampled_utc_ms": NOW - 999_000})):
            sampled_ms = NOW - 10_000 + index * 1000
            document = {"schema": "pado.sim-full-hk.v1", "non_operational": True,
                "sampled_utc_ms": sampled_ms, "equipment": {"OEM719A": {"HWMONITOR": {
                    "temperature_c": 99., "supply_voltage_v": 9.}}}, "padding": "x" * 300,
                **changes}
            self.add_full(sampled_ms=sampled_ms, record_id=2 + index, document=document)
        self.assertEqual(self.reading(self.status(), "oem719a", "temperature")["value"], 28.)

    def test_all_layout_readings_match_real_simulator_scalar_and_complete_hk(self):
        # Use the actual simulator generator across powered/off payload modes.
        # Channel voltages must never masquerade as the common bus voltage.
        modes = ("detumbling", "standby", "communication", "crest", "siren", "safehold")
        for index, mode in enumerate(modes):
            sampled_ms = NOW - (len(modes) - index) * 1000
            power = PowerModel().step(sampled_ms // 1000, (6878137., 0., 0.),
                (AU_M, 0., 0.), (1., 0., 0., 0.), mode=mode)
            _, eps_raw = self.add_eps(sampled_ms=sampled_ms,
                bus_voltage_v=power["regulated_bus_voltage_v"])
            start = self.sequence + 1
            # Encode only the three genuine input families needed by the HK generator.
            core = (eps_raw,
                encode_tm("AOCS_STATE", start, sampled_ms, {
                    "adcs_state": 1, "control_mode": 1, "boot_index": 1,
                    "sram_latchup_count": 0, "edac_error_count": 0,
                    "q_w": 1., "q_x": 0., "q_y": 0., "q_z": 0.,
                    "body_rate_x_rad_s": 0., "body_rate_y_rad_s": 0.,
                    "body_rate_z_rad_s": .001}),
                encode_tm("GNSS_PVT", start + 1, sampled_ms, {
                    "norad_id": 99999, "solution_status": 1, "receiver_age_ms": 0,
                    "x_m": 6878137., "y_m": 0., "z_m": 0.,
                    "vx_mps": 0., "vy_mps": 7500., "vz_mps": 0.}))
            extra = additional_packets(core, start + 2, include_full=True,
                                       power_sample=power)
            for raw in core[1:] + extra:
                self.archive.append(raw, decode(raw), NOW)
            self.sequence = start + len(extra) + 2
            scalars = {p["name"]: p["fields"] for p in map(decode, core + extra)
                       if p["name"] != "FULL_HK_CHUNK"}
            full = equipment_snapshot(core, power_sample=power)
            payload = self.status()
            for equipment in self.base["equipment"]:
                for configured in equipment["readings"]:
                    with self.subTest(mode=mode, component=equipment["id"], reading=configured["id"]):
                        actual = self.reading(payload, equipment["id"], configured["id"])
                        expected = full
                        path = configured.get("full_hk_path")
                        if path:
                            for token in path:
                                expected = expected[token]
                        selector = configured.get("tm")
                        if selector:
                            scalar = scalars[selector["packet"]][selector["field"]]
                            if path:
                                self.assertAlmostEqual(scalar, expected, places=5)
                            expected = scalar
                        self.assertAlmostEqual(actual["value"], expected, places=5)
                        self.assertEqual(actual["freshness"], "CURRENT")
                        key = configured.get("sensor_limit_key")
                        if key:
                            self.assertEqual(configured["unit"], self.limits["sensors"][key]["unit"])

    def test_local_layout_and_ros_limit_changes_apply_without_rewriting_measurements(self):
        self.add_eps(temperature_c=50.)
        before = self.status()
        self.assertEqual(self.component(before, "pdu1")["status"], "ALARM")
        settings = self.root / "spacecraft.json"
        settings.write_text(json.dumps(self.settings), encoding="utf-8")
        override = copy.deepcopy(self.base)
        item = self.component(override, "pdu1")
        item["anchor_body_mm"] = [12., -8., -24.]
        item["anchor_kind"] = "MEASURED_SENSOR"
        (self.root / "equipment-layout.json").write_text(json.dumps(override), encoding="utf-8")
        store = SettingsStore(settings)
        store.save_limits(store.view()["sha256"], {"EPS_PDU1.temperature_c": {"lower": 5., "upper": 60.}})
        after = equipment_status(self.path, settings_path=settings, limits=store.limits(), now_s=NOW / 1000)
        self.assertEqual(self.component(after, "pdu1")["anchor_body_mm"], [12., -8., -24.])
        self.assertNotEqual(after["layout_sha256"], before["layout_sha256"])
        self.assertEqual(self.reading(after, "pdu1", "temperature")["limit_state"], "IN_RANGE")
        self.assertEqual(self.reading(after, "pdu1", "temperature")["sampled_utc"],
                         self.reading(before, "pdu1", "temperature")["sampled_utc"])

    def test_corrupted_crc_parsed_values_or_database_are_not_displayed(self):
        _, raw = self.add_eps()
        with closing(sqlite3.connect(self.path)) as connection:
            original = connection.execute("SELECT sha256,fields_json FROM packets WHERE id=1").fetchone()
        damaged = raw[:-1] + bytes([raw[-1] ^ 1])
        bad_fields = json.loads(original[1]); bad_fields["temperature_c"] = 99.
        for changed_raw, digest, fields in ((damaged, hashlib.sha256(damaged).hexdigest(), original[1]),
                                           (raw, original[0], json.dumps(bad_fields))):
            with closing(sqlite3.connect(self.path)) as connection:
                connection.execute("UPDATE packets SET raw=?,sha256=?,fields_json=? WHERE id=1",
                                   (changed_raw, digest, fields))
                connection.commit()
            with self.assertRaises(EquipmentError):
                self.status()
        self.path.write_bytes(b"not a SQLite archive")
        with self.assertRaises(EquipmentError):
            self.status()

    def test_bad_layouts_are_rejected_instead_of_silently_using_the_template(self):
        path = self.root / "equipment-layout.json"
        variants = []
        bad = copy.deepcopy(self.base); bad["equipment"][1]["id"] = bad["equipment"][0]["id"]; variants.append(bad)
        bad = copy.deepcopy(self.base); bad["equipment"][0]["anchor_body_mm"][0] = float("nan"); variants.append(bad)
        bad = copy.deepcopy(self.base); bad["equipment"][0]["readings"][0]["tm"]["field"] = "__class__"; variants.append(bad)
        bad = copy.deepcopy(self.base); bad["equipment"][0]["readings"][0]["full_hk_max_age_s"] = 86401; variants.append(bad)
        for document in variants:
            with self.subTest(document=document):
                path.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaises(EquipmentError):
                    load_layout(settings_path=self.root / "spacecraft.json")

    def test_optional_callout_defaults_are_included_in_the_api_layout(self):
        document = copy.deepcopy(self.base)
        document["equipment"][0].pop("callout")
        document["equipment"][1]["callout"] = {"side": "right"}
        path = self.root / "equipment-layout.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        payload = equipment_status(None, layout_path=path, now_s=NOW / 1000)
        self.assertEqual(payload["equipment"][0]["callout"], {"side": "left", "order": 0})
        self.assertEqual(payload["equipment"][1]["callout"], {"side": "right", "order": 0})

    @unittest.skipUnless(os.name == "posix", "layout symlink fixtures need POSIX")
    def test_layout_file_and_parent_symlinks_are_rejected(self):
        link = self.root / "equipment-layout.json"
        link.symlink_to(LAYOUT)
        with self.assertRaises(EquipmentError):
            load_layout(settings_path=self.root / "spacecraft.json")
        link.unlink()
        directory = self.root / "linked"
        directory.symlink_to(LAYOUT.parent, target_is_directory=True)
        with self.assertRaises(EquipmentError):
            load_layout(layout_path=directory / LAYOUT.name)


if __name__ == "__main__":
    unittest.main()
