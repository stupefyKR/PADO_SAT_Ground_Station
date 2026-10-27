"""Physics invariants for the virtual spacecraft's restartable EPS model."""

import json
import math
import unittest

from services.sim_sat.power import AU_M, PowerModel, illumination_fraction, load_profile


DAY = (6878137.0, 0.0, 0.0)
NIGHT = (-6878137.0, 0.0, 0.0)
SUN = (AU_M, 0.0, 0.0)
IDENTITY = (1.0, 0.0, 0.0, 0.0)


def sample(model, utc=0, position=DAY, quaternion=IDENTITY, mode="standby", overrides=None):
    return model.step(utc, position, SUN, quaternion, mode, overrides)


class SolarGeometryTests(unittest.TestCase):
    def test_user_cell_ratings_and_single_eighty_percent_conversion(self):
        result = sample(PowerModel())
        self.assertAlmostEqual(result["solar_raw_w"], 30.9456, places=9)
        self.assertAlmostEqual(result["solar_available_w"], 24.75648, places=9)
        self.assertAlmostEqual(result["solar_conversion_loss_w"], 6.18912, places=9)
        self.assertEqual([bank["voltage_v"] for bank in result["banks"]], [9.28, 9.28, 8.72])
        self.assertEqual([bank["channel"] for bank in result["banks"]], [0, 1, 2])
        self.assertAlmostEqual(sum(bank["power_w"] for bank in result["banks"]), result["solar_raw_w"])

    def test_incidence_angle_uses_body_to_itrf_quaternion(self):
        for angle, fraction in ((0, 1), (60, .5), (90, 0), (120, 0), (180, 0)):
            with self.subTest(angle=angle):
                radians = math.radians(angle)
                quaternion = (math.cos(radians/2), 0, 0, math.sin(radians/2))
                result = sample(PowerModel(), quaternion=quaternion)
                self.assertAlmostEqual(result["solar_raw_w"], 30.9456 * fraction, places=9)

    def test_earth_umbra_and_finite_sun_penumbra(self):
        self.assertEqual(illumination_fraction(DAY, SUN), 1.0)
        self.assertEqual(illumination_fraction(NIGHT, SUN), 0.0)
        radius = 6378137.0
        limb_position = (-math.sqrt(DAY[0]**2-radius**2), radius, 0)
        penumbra = illumination_fraction(limb_position, SUN)
        self.assertGreater(penumbra, 0)
        self.assertLess(penumbra, 1)
        result = sample(PowerModel(), position=NIGHT)
        self.assertTrue(result["eclipse"])
        self.assertEqual(result["solar_raw_w"], 0)

    def test_wiring_changes_volts_and_amps_but_preserves_watts(self):
        profile = load_profile()
        profile["solar"]["banks"][0].update(series=12, parallel=1)
        result = sample(PowerModel(profile))
        self.assertAlmostEqual(result["solar_raw_w"], 30.9456)
        self.assertAlmostEqual(result["banks"][0]["voltage_v"], 27.84)
        self.assertAlmostEqual(result["banks"][0]["current_a"], .49)


class DeviceLoadTests(unittest.TestCase):
    def test_mode_totals_match_supplied_peak_budget_including_six_watt_maximum(self):
        expected = {"detumbling": 15.54, "standby": 16.49, "communication": 27.29,
                    "crest": 18.99, "siren": 20.99, "safehold": 16.49}
        for index, (mode, watts) in enumerate(expected.items(), 1):
            with self.subTest(mode=mode):
                result = sample(PowerModel(), mode=mode)
                self.assertAlmostEqual(result["load_w"], watts)
                self.assertEqual(result["mode_code"], index)
                self.assertAlmostEqual(sum(result["device_loads_w"].values()), watts)

    def test_device_override_enables_payload_without_changing_mode_table(self):
        result = sample(PowerModel(), overrides={"crest": True, "siren": True, "reaction_wheel": False})
        self.assertAlmostEqual(result["load_w"], 16.49 + 2.5 + 4.5 - 2.6)
        self.assertAlmostEqual(result["device_currents_a"]["crest"], 2.5/12)
        self.assertAlmostEqual(result["device_currents_a"]["siren"], 4.5/5)
        self.assertEqual(result["device_loads_w"]["reaction_wheel"], 0)

    def test_six_watt_allocation_can_be_replaced_by_measured_duty_cycle(self):
        profile = load_profile()
        profile["devices"]["battery_auxiliary"]["power_w_by_mode"]["standby"] = .5
        result = sample(PowerModel(profile))
        self.assertAlmostEqual(result["load_w"], 10.99)


class BatteryPhysicsTests(unittest.TestCase):
    def test_sunlight_charges_and_eclipse_discharges_smoothly(self):
        day, night = PowerModel(), PowerModel()
        first_day, first_night = sample(day), sample(night, position=NIGHT)
        for utc in range(5, 301, 5):
            day_result = sample(day, utc)
            night_result = sample(night, utc, position=NIGHT)
        self.assertGreater(day_result["soc"], first_day["soc"])
        self.assertLess(night_result["soc"], first_night["soc"])
        self.assertGreater(day_result["battery_current_a"], 0)
        self.assertLess(night_result["battery_current_a"], 0)
        self.assertGreater(day_result["battery_voltage_v"], day_result["battery_ocv_v"])
        self.assertLess(night_result["battery_voltage_v"], night_result["battery_ocv_v"])
        self.assertLess(abs(day_result["battery_voltage_v"]-first_day["battery_voltage_v"]), .1)
        self.assertGreater(day_result["charge_throughput_ah"], 0)
        self.assertGreater(night_result["discharge_throughput_ah"], 0)

    def test_communication_load_exceeds_full_sunlight_and_discharges(self):
        result = sample(PowerModel(), mode="communication")
        self.assertLess(result["battery_current_a"], 0)
        self.assertAlmostEqual(result["load_w"]-result["solar_available_w"], result["battery_discharge_power_w"])

    def test_constant_current_limit_and_constant_voltage_taper(self):
        profile = load_profile()
        profile["battery"]["max_charge_current_a"] = .1
        limited = sample(PowerModel(profile))
        self.assertAlmostEqual(limited["charge_current_a"], .1)
        self.assertGreater(limited["curtailed_power_w"], 0)
        profile = load_profile()
        profile["battery"]["initial_soc"] = .99
        tapered = sample(PowerModel(profile))
        self.assertAlmostEqual(tapered["battery_voltage_v"], 16.0)
        self.assertLess(tapered["charge_current_a"], .5)
        self.assertGreater(tapered["charge_current_a"], 0)

    def test_full_battery_curtails_empty_battery_reports_unserved_load(self):
        profile = load_profile()
        profile["battery"]["initial_soc"] = 1
        full = PowerModel(profile)
        sample(full)
        charged = sample(full, 300)
        self.assertEqual(charged["soc"], 1)
        self.assertEqual(charged["charge_current_a"], 0)
        self.assertAlmostEqual(charged["curtailed_power_w"], 24.75648-16.49)
        profile["battery"]["initial_soc"] = 0
        empty = PowerModel(profile)
        sample(empty, position=NIGHT)
        discharged = sample(empty, 300, position=NIGHT)
        self.assertEqual(discharged["soc"], 0)
        self.assertEqual(discharged["discharge_current_a"], 0)
        self.assertAlmostEqual(discharged["unserved_load_w"], 16.49)
        self.assertEqual(discharged["served_load_w"], 0)

    def test_undervoltage_and_discharge_current_limits_are_explicit(self):
        profile = load_profile()
        profile["battery"]["initial_soc"] = .001
        low = sample(PowerModel(profile), position=NIGHT)
        self.assertAlmostEqual(low["battery_voltage_v"], 11.8)
        self.assertGreater(low["unserved_load_w"], 10)
        profile["battery"].update(initial_soc=.7, max_discharge_current_a=.1)
        limited = sample(PowerModel(profile), position=NIGHT)
        self.assertAlmostEqual(limited["discharge_current_a"], .1)
        self.assertGreater(limited["unserved_load_w"], 10)

    def test_instantaneous_power_and_integrated_energy_are_conserved(self):
        model = PowerModel()
        initial_energy = sample(model)["stored_energy_wh"]
        # Smooth rotation into eclipse-like, dark-panel conditions. Modes change
        # at timestamps; power allocation must not invent or destroy energy.
        for utc in range(5, 3601, 5):
            angle = utc * math.pi/1800
            result = sample(model, utc, quaternion=(math.cos(angle/2), 0, 0, math.sin(angle/2)),
                            mode="crest" if utc < 1800 else "communication")
            self.assertAlmostEqual(result["power_balance_error_w"], 0, places=10)
            self.assertAlmostEqual(result["net_battery_w"], result["battery_voltage_v"]*result["battery_current_a"], places=10)
            self.assertAlmostEqual(result["solar_available_w"] + result["battery_discharge_power_w"],
                                   result["served_load_w"] + result["battery_charge_power_w"] + result["curtailed_power_w"], places=10)
        expected_delta = (result["cumulative_solar_wh"]-result["cumulative_served_load_wh"]
                          -result["cumulative_losses_wh"]-result["cumulative_curtailed_wh"])
        self.assertAlmostEqual(result["stored_energy_wh"]-initial_energy, expected_delta, places=9)

    def test_constant_ocv_current_and_efficiency_have_analytic_energy_solution(self):
        profile = load_profile()
        profile["battery"].update(ocv_curve=[[0,14.4],[1,14.4]], internal_resistance_ohm=0)
        model = PowerModel(profile)
        before = sample(model)
        after = sample(model, 300)
        expected = before["stored_energy_wh"] + (24.75648-16.49)*.97*300/3600
        self.assertAlmostEqual(after["stored_energy_wh"], expected, places=9)
        self.assertAlmostEqual(after["charge_current_a"], (24.75648-16.49)/14.4)


class PowerPersistenceTests(unittest.TestCase):
    def test_json_round_trip_restart_preserves_future_energy_and_voltage(self):
        original = PowerModel()
        sample(original)
        sample(original, 45)
        restored = PowerModel(state=json.loads(json.dumps(original.snapshot())))
        for utc in (45, 50, 100, 130):
            a, b = sample(original, utc), sample(restored, utc)
            self.assertEqual(a, b)

    def test_quaternion_sign_flip_does_not_rotate_panel_or_reset_soc(self):
        first, second = PowerModel(), PowerModel()
        sample(first)
        sample(second)
        a, b = sample(first, 100), sample(second, 100, quaternion=(-1,0,0,0))
        self.assertAlmostEqual(a["stored_energy_wh"], b["stored_energy_wh"])
        self.assertAlmostEqual(a["solar_raw_w"], b["solar_raw_w"])

    def test_repeated_timestamps_do_not_charge_twice_and_modes_are_not_retroactive(self):
        first, second = PowerModel(), PowerModel()
        sample(first)
        sample(second)
        result = sample(first, 100)
        changed_mode = sample(first, 100, mode="communication")
        changed_at_end = sample(second, 100, mode="communication")
        self.assertEqual(changed_mode["stored_energy_wh"], result["stored_energy_wh"])
        self.assertEqual(changed_mode["stored_energy_wh"], changed_at_end["stored_energy_wh"])
        self.assertLess(changed_mode["battery_current_a"], 0)

    def test_long_ephemeris_gap_preserves_energy_and_reports_skipped_seconds(self):
        model = PowerModel()
        initial = sample(model)
        result = sample(model, 3600)
        self.assertEqual(initial["stored_energy_wh"], result["stored_energy_wh"])
        self.assertEqual(result["integration_quality"], "gap_not_integrated")
        self.assertEqual(result["unmodelled_seconds"], 3600)
        after = sample(model, 3605)
        self.assertGreater(after["stored_energy_wh"], result["stored_energy_wh"])

    def test_explicit_reanchor_preserves_energy_and_does_not_double_count_gap(self):
        model = PowerModel()
        initial = sample(model)
        model.reanchor(1800)
        result = sample(model, 1800)
        self.assertEqual(result["stored_energy_wh"], initial["stored_energy_wh"])
        self.assertEqual(result["unmodelled_seconds"], 1800)
        self.assertGreater(sample(model, 1805)["soc"], initial["soc"])

    def test_invalid_input_cannot_reset_or_advance_battery(self):
        model = PowerModel()
        sample(model)
        state = model.snapshot()
        for options in ({"utc": -1}, {"quaternion": (0,0,0,0)},
                        {"position": (0,0,0)}, {"mode": "unknown"},
                        {"overrides": {"crest": 99}}):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    sample(model, **options)
                self.assertEqual(model.snapshot(), state)

    def test_changed_profile_requires_explicit_state_migration(self):
        model = PowerModel()
        sample(model)
        profile = load_profile()
        profile["battery"]["capacity_wh"] = 50
        with self.assertRaisesRegex(ValueError, "migration"):
            PowerModel(profile=profile, state=model.snapshot())

    def test_rejected_interpolated_geometry_is_atomic(self):
        model = PowerModel()
        sample(model)
        before = model.snapshot()
        with self.assertRaisesRegex(ValueError, "outside Earth"):
            sample(model, 300, position=NIGHT)
        self.assertEqual(model.snapshot(), before)

    def test_invalid_sun_geometry_does_not_poison_initial_state(self):
        model = PowerModel()
        before = model.snapshot()
        with self.assertRaisesRegex(ValueError, "outside Earth and Sun"):
            model.step(0, SUN, SUN, IDENTITY)
        self.assertEqual(model.snapshot(), before)

    def test_bad_wiring_or_nonfinite_profile_is_rejected(self):
        for mutate in (lambda p: p["solar"]["banks"][0].update(series=3),
                       lambda p: p["battery"].update(capacity_wh=float("nan")),
                       lambda p: p.update(max_step_s=6)):
            profile = load_profile()
            mutate(profile)
            with self.assertRaises(ValueError):
                PowerModel(profile)


if __name__ == "__main__":
    unittest.main()
