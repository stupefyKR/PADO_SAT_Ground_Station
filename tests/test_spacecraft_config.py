"""The checked-in template is one validated source for hardware and monitoring."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.spacecraft_config import (ROOT, SettingsError, get_power_profile,
    get_sensor_limits, load_settings, resolve_settings_path, settings_sha256,
    validate_settings)
from services.sim_sat.power import PowerModel
from tests.test_sim_power import sample


class SpacecraftSettingsTests(unittest.TestCase):
    def setUp(self):
        output = ROOT / "out"
        output.mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=output)
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "spacecraft.json"
        self.document = load_settings(ROOT / "config/spacecraft.example.json")

    def write(self):
        self.path.write_text(json.dumps(self.document), encoding="utf-8")

    def test_one_dsp_rating_updates_both_banks_and_generated_power(self):
        before = sample(PowerModel(get_power_profile(self.document)))
        self.document["power"]["solar"]["cell_types"]["DSP"]["voltage_v"] = 2.5
        profile = get_power_profile(validate_settings(self.document))
        self.assertEqual([bank["cell_voltage_v"] for bank in profile["solar"]["banks"]],
                         [2.5, 2.5, 2.18])
        after = sample(PowerModel(profile))
        self.assertAlmostEqual(after["solar_raw_w"]-before["solar_raw_w"],24*(2.5-2.32)*.49)
        self.assertAlmostEqual(after["solar_available_w"],after["solar_raw_w"]*.8)

    def test_helpers_return_copies_and_stable_semantic_hash(self):
        digest = settings_sha256(self.document)
        reordered = dict(reversed(list(self.document.items())))
        self.assertEqual(settings_sha256(reordered),digest)
        limits = get_sensor_limits(self.document)
        limits["sensors"][next(iter(limits["sensors"]))]["lower"] = -123
        self.assertEqual(settings_sha256(self.document),digest)
        profile = get_power_profile(self.document)
        profile["battery"]["capacity_wh"] = 10
        self.assertEqual(self.document["power"]["battery"]["capacity_wh"],100)

    def test_explicit_and_environment_missing_paths_do_not_fallback(self):
        with self.assertRaises(SettingsError):
            load_settings(self.path)
        with patch.dict(os.environ,{"PADO_SPACECRAFT_CONFIG":str(self.path)}):
            self.assertEqual(resolve_settings_path(),self.path)
            with self.assertRaises(SettingsError):
                load_settings()
        self.write()
        with patch.dict(os.environ,{"PADO_SPACECRAFT_CONFIG":str(self.path)}):
            self.assertEqual(load_settings(),self.document)
            self.assertEqual(resolve_settings_path(writable=True),self.path)

    def test_rejects_duplicate_fields_nonfinite_and_bad_ranges(self):
        for raw in ('{"schema":1,"schema":2}', '{"power":NaN}', '[]'):
            self.path.write_text(raw,encoding="utf-8")
            with self.subTest(raw=raw), self.assertRaises(SettingsError):
                load_settings(self.path)
        key = next(iter(self.document["sensor_limits"]))
        self.document["sensor_limits"][key]["upper"] = self.document["sensor_limits"][key]["lower"]
        with self.assertRaises(SettingsError):
            validate_settings(self.document)

    def test_rejects_unknown_scalar_fields_and_duplicated_cell_ratings(self):
        bank = self.document["power"]["solar"]["banks"][0]
        bank["cell_voltage_v"] = 2.32
        with self.assertRaises(SettingsError):
            validate_settings(self.document)
        del bank["cell_voltage_v"]
        key = next(iter(self.document["sensor_limits"]))
        self.document["sensor_limits"]["EPS_PDU1.missing"] = self.document["sensor_limits"].pop(key)
        with self.assertRaises(SettingsError):
            validate_settings(self.document)

    def test_malformed_physical_objects_fail_with_settings_error(self):
        for field in ("devices","battery","telemetry"):
            with self.subTest(field=field):
                document = load_settings(ROOT / "config/spacecraft.example.json")
                document["power"][field] = []
                with self.assertRaises(SettingsError):
                    validate_settings(document)


if __name__ == "__main__":
    unittest.main()
