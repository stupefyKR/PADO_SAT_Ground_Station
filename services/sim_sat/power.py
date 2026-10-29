"""Stateful, test-only PADO-SAT energy balance; no network or orbit propagator.

Solar power uses supplied cell MPP values, attitude, finite-Sun Earth shadow and
one DC conversion efficiency. A configurable OCV(SOC) + series resistance
battery accepts CC/CV-limited charging or supplies voltage/current-limited
loads. SOC denotes usable energy / configured Wh, not a calibrated BPX gauge.
Positive battery current is charging. All geometry is ITRF, meters; quaternion
order is [w,x,y,z] and transforms BODY vectors into ITRF.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Mapping, Sequence

from services.spacecraft_config import get_power_profile, load_settings, validate_power_profile


AU_M = 149597870700.0
MODES = {1: "detumbling", 2: "standby", 3: "communication",
         4: "crest", 5: "siren", 6: "safehold"}
STATE_SCHEMA = "pado-sim-power-state-v1"


def load_profile(path: str | Path | None = None) -> dict:
    """Read shared settings, while retaining explicit legacy test profile inputs."""
    if path is None:
        return get_power_profile()
    source = Path(path)
    document = json.loads(source.read_text(encoding="utf-8-sig"))
    if document.get("schema") == "pado.spacecraft-settings.v1":
        return get_power_profile(load_settings(source))
    # Explicit legacy profiles remain useful for isolated physical tests.
    if "telemetry" not in document:
        example = Path(__file__).resolve().parents[2] / "config/spacecraft.example.json"
        document["telemetry"] = get_power_profile(load_settings(example))["telemetry"]
    validate_power_profile(document)
    return document


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _vector(value: Sequence[float], length: int, label: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"{label} must contain {length} numbers")
    return tuple(_finite(x, label) for x in value)


def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(v) -> float:
    return math.sqrt(_dot(v, v))


def _unit(v) -> tuple[float, ...]:
    magnitude = _norm(v)
    if magnitude == 0:
        raise ValueError("zero direction vector")
    return tuple(x / magnitude for x in v)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _rotate(q, v) -> tuple[float, float, float]:
    w, x, y, z = q
    vx, vy, vz = v
    return ((1 - 2 * (y*y + z*z)) * vx + 2 * (x*y - w*z) * vy + 2 * (x*z + w*y) * vz,
            2 * (x*y + w*z) * vx + (1 - 2 * (x*x + z*z)) * vy + 2 * (y*z - w*x) * vz,
            2 * (x*z - w*y) * vx + 2 * (y*z + w*x) * vy + (1 - 2 * (x*x + y*y)) * vz)


def _slerp(a, b, f):
    cosine = _dot(a, b)
    if cosine < 0:
        b, cosine = tuple(-x for x in b), -cosine
    if cosine > 0.9995:
        return _unit(tuple(x + f * (y - x) for x, y in zip(a, b)))
    angle = math.acos(_clamp(cosine, -1, 1))
    scale = math.sin(angle)
    return tuple((math.sin((1-f)*angle)*x + math.sin(f*angle)*y) / scale for x, y in zip(a, b))


def illumination_fraction(position_itrf_m, sun_itrf_m, earth_radius_m=6378137.0,
                          sun_radius_m=695700000.0) -> float:
    """Visible fraction of the Sun's apparent disk, including penumbra.

    The circle-overlap construction is the usual angular-disk approximation;
    there is no atmospheric refraction or terrain/spacecraft self-shadowing.
    """
    to_sun = tuple(s - p for s, p in zip(sun_itrf_m, position_itrf_m))
    to_earth = tuple(-p for p in position_itrf_m)
    earth_distance, sun_distance = _norm(to_earth), _norm(to_sun)
    if earth_distance <= earth_radius_m or sun_distance <= sun_radius_m:
        raise ValueError("EPS geometry must be outside Earth and Sun")
    earth = math.asin(earth_radius_m / earth_distance)
    sun = math.asin(sun_radius_m / sun_distance)
    separation = math.acos(_clamp(_dot(_unit(to_earth), _unit(to_sun)), -1, 1))
    if separation >= earth + sun:
        return 1.0
    if separation <= abs(earth - sun):
        return 0.0 if earth >= sun else 1.0 - (earth / sun) ** 2
    e_arg = _clamp((separation**2 + earth**2 - sun**2) / (2 * separation * earth), -1, 1)
    s_arg = _clamp((separation**2 + sun**2 - earth**2) / (2 * separation * sun), -1, 1)
    radicand = (-separation+earth+sun)*(separation+earth-sun)*(separation-earth+sun)*(separation+earth+sun)
    overlap = earth**2 * math.acos(e_arg) + sun**2 * math.acos(s_arg) - 0.5 * math.sqrt(max(0.0, radicand))
    return _clamp(1.0 - overlap / (math.pi * sun**2))


def _mode(value: str | int) -> tuple[str, int]:
    if isinstance(value, bool):
        raise ValueError("invalid power mode")
    if isinstance(value, int) and value in MODES:
        return MODES[value], value
    if isinstance(value, str):
        slug = value.lower().replace("mission(", "").replace(")", "")
        slug = "detumbling" if slug == "initial" else slug
        for code, name in MODES.items():
            if name == slug:
                return slug, code
    raise ValueError("unknown power mode")


class PowerModel:
    """Restartable battery state and deterministic <=5 s numerical integration.

    ``step`` evaluates an instantaneous sample and integrates the preceding
    interval using interpolated geometry. Changed device modes take effect at
    their timestamp, not retroactively. Gaps beyond max_catchup_s keep the last
    energy and are explicitly reported as unmodelled; use ``reanchor`` when an
    upstream ephemeris gap is already known. No SOC reset occurs on FDS refresh.
    """

    def __init__(self, profile: dict | None = None, state: dict | None = None):
        self.profile = copy.deepcopy(load_profile() if profile is None else profile)
        self._validate_profile()
        self._fingerprint = hashlib.sha256(json.dumps(self.profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        battery = self.profile["battery"]
        self._state = {
            "schema": STATE_SCHEMA, "profile_sha256": self._fingerprint,
            "energy_wh": battery["capacity_wh"] * battery["initial_soc"],
            "temperature_c": battery["initial_temperature_c"],
            "last_utc_s": None, "previous_input": None,
            "charge_throughput_ah": 0.0, "discharge_throughput_ah": 0.0,
            "unmodelled_seconds": 0.0,
            "cumulative_solar_wh": 0.0, "cumulative_served_load_wh": 0.0,
            "cumulative_losses_wh": 0.0, "cumulative_curtailed_wh": 0.0,
            "cumulative_unserved_wh": 0.0,
        }
        if state is not None:
            self._restore(state)

    def _validate_profile(self) -> None:
        validate_power_profile(self.profile)

    @classmethod
    def migrate_state(cls, profile: dict, state: dict, *, skip_unknown_interval: bool = False):
        """Explicitly adopt new ratings without resetting stored battery energy.

        The caller owns the application boundary. A running simulator first
        integrates the old model up to that boundary, then changes ratings.
        Legacy states without their old profile cannot model an unknown past
        interval and keep their energy until the first new sample.
        """
        candidate = cls(profile)
        previous = copy.deepcopy(state)
        old_fingerprint = previous.get("profile_sha256")
        old_energy = _finite(previous.get("energy_wh"), "stored battery energy")
        previous["profile_sha256"] = candidate._fingerprint
        previous["energy_wh"] = min(old_energy, profile["battery"]["capacity_wh"])
        if skip_unknown_interval:
            previous["previous_input"] = None
        candidate._restore(previous)
        candidate.configuration_change = {
            "previous_profile_sha256": old_fingerprint,
            "profile_sha256": candidate._fingerprint,
            "clamped_energy_wh": old_energy - previous["energy_wh"],
            "past_interval_available": not skip_unknown_interval,
        }
        return candidate

    def _restore(self, state: dict) -> None:
        restored = copy.deepcopy(state)
        if restored.get("schema") != STATE_SCHEMA or restored.get("profile_sha256") != self._fingerprint:
            raise ValueError("EPS state belongs to another profile; explicit migration required")
        if set(restored) != set(self._state):
            raise ValueError("invalid EPS state fields")
        for key in self._state:
            if key not in ("schema", "profile_sha256", "last_utc_s", "previous_input"):
                value = _finite(restored[key], key)
                if key != "temperature_c" and value < 0:
                    raise ValueError("negative EPS state")
        if restored["energy_wh"] > self.profile["battery"]["capacity_wh"]:
            raise ValueError("battery energy exceeds capacity")
        last = restored["last_utc_s"]
        if last is not None:
            _finite(last, "last UTC")
        previous = restored["previous_input"]
        if previous is not None:
            validated = self._input(previous["utc_s"], previous["position_itrf_m"], previous["sun_itrf_m"],
                                    previous["quaternion_wxyz"], previous["mode"], previous["device_overrides"])
            if validated["utc_s"] != last:
                raise ValueError("EPS state timestamp mismatch")
            restored["previous_input"] = validated
        self._state = restored

    def snapshot(self) -> dict:
        """JSON-compatible state. Save atomically alongside generated telemetry."""
        return copy.deepcopy(self._state)

    def reanchor(self, utc_s: int | float) -> None:
        """Preserve energy while explicitly skipping unavailable orbital history."""
        utc = _finite(utc_s, "UTC seconds")
        previous = self._state["last_utc_s"]
        if previous is not None and utc < previous:
            raise ValueError("EPS clock cannot move backwards")
        self._state["unmodelled_seconds"] += 0 if previous is None else utc - previous
        self._state["last_utc_s"], self._state["previous_input"] = utc, None

    def _input(self, utc_s, position, sun, quaternion, mode, overrides) -> dict:
        utc = _finite(utc_s, "UTC seconds")
        position = _vector(position, 3, "ITRF position")
        sun = _vector(sun, 3, "ITRF Sun position")
        if _norm(position) <= self.profile["solar"]["earth_radius_m"] or _norm(sun) < 1e10:
            raise ValueError("EPS expects satellite position and Earth-to-Sun position in meters")
        illumination_fraction(position, sun, self.profile["solar"]["earth_radius_m"],
                              self.profile["solar"]["sun_radius_m"])
        q = _vector(quaternion, 4, "quaternion")
        if abs(_norm(q) - 1) > 0.02:
            raise ValueError("EPS quaternion is not normalized")
        slug, _ = _mode(mode)
        overrides = {} if overrides is None else dict(overrides)
        for key, value in overrides.items():
            if key not in self.profile["devices"] or not isinstance(value, bool):
                raise ValueError("device overrides must be known device on/off booleans")
        return {"utc_s": utc, "position_itrf_m": position, "sun_itrf_m": sun,
                "quaternion_wxyz": _unit(q), "mode": slug, "device_overrides": overrides}

    def _ocv(self, soc: float) -> float:
        curve = self.profile["battery"]["ocv_curve"]
        for (s0, v0), (s1, v1) in zip(curve, curve[1:]):
            if soc <= s1:
                return v0 + (v1 - v0) * (soc - s0) / (s1 - s0)
        return curve[-1][1]

    def _evaluate(self, data: dict, dt_s: float = 0) -> dict:
        b, solar = self.profile["battery"], self.profile["solar"]
        soc = _clamp(self._state["energy_wh"] / b["capacity_wh"])
        ocv, resistance = self._ocv(soc), b["internal_resistance_ohm"]
        position, sun = data["position_itrf_m"], data["sun_itrf_m"]
        visibility = illumination_fraction(position, sun, solar["earth_radius_m"], solar["sun_radius_m"])
        to_sun = tuple(s-p for s, p in zip(sun, position))
        direction = _unit(to_sun)
        distance_factor = (AU_M / _norm(to_sun)) ** 2 if solar.get("distance_scaling") else 1.0
        banks = []
        for bank in solar["banks"]:
            incidence = max(0.0, _dot(_rotate(data["quaternion_wxyz"], bank["normal_body"]), direction))
            irradiance = incidence * visibility * distance_factor
            voltage = bank["series"] * bank["cell_voltage_v"] if irradiance > 1e-12 else 0.0
            current = bank["parallel"] * bank["cell_current_a"] * irradiance
            banks.append({"id": bank["id"], "channel": bank["channel"], "kind": bank["kind"],
                          "voltage_v": voltage, "current_a": current, "power_w": voltage * current,
                          "incidence_cosine": incidence})
        raw = sum(bank["power_w"] for bank in banks)
        available = raw * solar["conversion_efficiency"]
        mode = data["mode"]
        loads = {}
        for name, device in self.profile["devices"].items():
            configured = device["power_w_by_mode"][mode]
            override = data["device_overrides"].get(name)
            # Explicit ON uses that equipment's peak if its mode allocation is zero.
            loads[name] = 0.0 if override is False else (max(device["power_w_by_mode"].values()) if override is True else configured)
        load = sum(loads.values())
        surplus = available - load
        charge_i = discharge_i = unserved = curtailed = 0.0
        charge_w = discharge_w = chemical_in = chemical_out = 0.0
        protection = "none"
        if surplus >= 0:
            ideal = surplus / ocv if resistance == 0 else 2 * surplus / (ocv + math.sqrt(ocv*ocv + 4*resistance*surplus))
            limit = b["max_charge_current_a"]
            if resistance > 0:
                limit = min(limit, max(0.0, (b["maximum_voltage_v"] - ocv) / resistance))
            if dt_s > 0:
                limit = min(limit, max(0.0, (b["capacity_wh"] - self._state["energy_wh"]) * 3600 / (dt_s * ocv * b["charge_efficiency"])))
            charge_i = min(ideal, limit) if soc < 1 else 0.0
            charge_w = (ocv + resistance*charge_i) * charge_i
            chemical_in = ocv * charge_i * b["charge_efficiency"]
            curtailed = max(0.0, surplus - charge_w)
            if curtailed > 1e-9:
                protection = "charge_limited"
        else:
            deficit = -surplus
            limit = b["max_discharge_current_a"]
            if resistance > 0:
                limit = min(limit, max(0.0, (ocv - b["minimum_voltage_v"]) / resistance), ocv/(2*resistance))
            if dt_s > 0:
                limit = min(limit, self._state["energy_wh"] * 3600 * b["discharge_efficiency"] / (dt_s * ocv))
            maximum_power = max(0.0, (ocv - resistance*limit) * limit)
            delivered = min(deficit, maximum_power) if soc > 0 else 0.0
            discriminant = max(0.0, ocv*ocv - 4*resistance*delivered)
            discharge_i = delivered / ocv if resistance == 0 else 2*delivered / (ocv + math.sqrt(discriminant))
            discharge_w = (ocv - resistance*discharge_i) * discharge_i
            chemical_out = ocv * discharge_i / b["discharge_efficiency"]
            unserved = max(0.0, deficit - discharge_w)
            if unserved > 1e-9:
                protection = "discharge_limited"
        current = charge_i - discharge_i
        voltage = ocv + resistance * current
        internal_loss = resistance * current * current
        battery_loss = max(0.0, charge_w - chemical_in + chemical_out - discharge_w)
        served = load - unserved
        solar_loss = raw - available
        device_currents = {name: watts / (self.profile["devices"][name]["voltage_v"] or voltage) for name, watts in loads.items()}
        telemetry = self.profile["telemetry"]
        thresholds = telemetry["battery_mode_soc"]
        battery_mode = 1 if soc < thresholds["critical"] else 2 if soc < thresholds["safe"] else \
            4 if soc >= thresholds["full"] else 3
        return {
            "utc_s": data["utc_s"], "mode": mode, "mode_code": _mode(mode)[1],
            "profile_id": self.profile["profile_id"], "test_only": True,
            "power_profile_sha256": self._fingerprint,
            "regulated_bus_voltage_v": telemetry["regulated_bus_voltage_v"],
            "logic_supply_voltage_v": telemetry["logic_supply_voltage_v"],
            "battery_mode": battery_mode,
            "battery_temperature_offsets_c": telemetry["battery_temperature_offsets_c"],
            "srs_internal_rails_v": telemetry["srs_internal_rails_v"],
            "device_voltages_v": {name: device["voltage_v"] or voltage
                                  for name, device in self.profile["devices"].items()},
            "battery_voltage_v": voltage, "battery_ocv_v": ocv,
            "battery_current_a": current, "charge_current_a": charge_i, "discharge_current_a": discharge_i,
            "soc": soc, "soc_pct": soc*100, "stored_energy_wh": self._state["energy_wh"],
            "battery_energy_wh": self._state["energy_wh"], "battery_capacity_wh": b["capacity_wh"],
            "battery_temperature_c": self._state["temperature_c"],
            "charge_state": "charging" if current > 1e-9 else "discharging" if current < -1e-9 else "idle",
            "solar_raw_w": raw, "solar_available_w": available,
            "solar_conversion_loss_w": solar_loss, "solar_bus_w": available,
            "load_w": load, "served_load_w": served, "device_loads_w": loads,
            "device_currents_a": device_currents, "unserved_load_w": unserved,
            "curtailed_power_w": curtailed, "battery_charge_power_w": charge_w,
            "battery_discharge_power_w": discharge_w, "net_battery_w": charge_w-discharge_w,
            "battery_storage_power_w": chemical_in-chemical_out,
            "battery_internal_loss_w": internal_loss, "battery_conversion_loss_w": battery_loss,
            "power_balance_error_w": raw-solar_loss-served-curtailed-chemical_in+chemical_out-battery_loss,
            "eclipse": visibility < 1-1e-9, "illumination_fraction": visibility,
            "banks": banks, "protection": protection,
        }

    def step(self, utc_s: int | float, position_itrf_m: Sequence[float], sun_itrf_m: Sequence[float],
             quaternion_wxyz: Sequence[float], mode: str | int = "standby",
             device_overrides: Mapping[str, bool] | None = None) -> dict:
        data = self._input(utc_s, position_itrf_m, sun_itrf_m, quaternion_wxyz, mode, device_overrides)
        previous, last = self._state["previous_input"], self._state["last_utc_s"]
        elapsed = 0.0 if last is None else data["utc_s"]-last
        if elapsed < 0:
            raise ValueError("EPS clock cannot move backwards")
        quality, integrated = "initial" if last is None else "continuous", 0.0
        if elapsed > self.profile["max_catchup_s"] or (elapsed > 0 and previous is None):
            self._state["unmodelled_seconds"] += elapsed
            quality = "gap_not_integrated"
        elif elapsed > 0:
            count = math.ceil(elapsed / self.profile["max_step_s"])
            dt = elapsed/count
            interval_samples = []
            for index in range(count):
                fraction = (index + 0.5) / count
                sample = dict(previous)
                sample["utc_s"] = last + (index+0.5)*dt
                for name in ("position_itrf_m", "sun_itrf_m"):
                    sample[name] = tuple(a+fraction*(b-a) for a, b in zip(previous[name], data[name]))
                sample["quaternion_wxyz"] = _slerp(previous["quaternion_wxyz"], data["quaternion_wxyz"], fraction)
                # Reject the entire interval before advancing any state. A chord
                # between exceptionally low/invalid orbit endpoints can cross
                # Earth even when each endpoint itself is outside the surface.
                illumination_fraction(sample["position_itrf_m"], sample["sun_itrf_m"],
                                      self.profile["solar"]["earth_radius_m"],
                                      self.profile["solar"]["sun_radius_m"])
                interval_samples.append(sample)
            for sample in interval_samples:
                balance = self._evaluate(sample, dt)
                hours = dt/3600
                self._state["energy_wh"] = _clamp(self._state["energy_wh"] + balance["battery_storage_power_w"]*hours,
                                                  0.0, self.profile["battery"]["capacity_wh"])
                self._state["charge_throughput_ah"] += balance["charge_current_a"]*hours
                self._state["discharge_throughput_ah"] += balance["discharge_current_a"]*hours
                self._state["cumulative_solar_wh"] += balance["solar_raw_w"]*hours
                self._state["cumulative_served_load_wh"] += balance["served_load_w"]*hours
                self._state["cumulative_losses_wh"] += (balance["solar_conversion_loss_w"]+balance["battery_conversion_loss_w"])*hours
                self._state["cumulative_curtailed_wh"] += balance["curtailed_power_w"]*hours
                self._state["cumulative_unserved_wh"] += balance["unserved_load_w"]*hours
                b = self.profile["battery"]
                equilibrium = b["eclipse_equilibrium_c"] + (b["sunlit_equilibrium_c"]-b["eclipse_equilibrium_c"])*balance["illumination_fraction"]
                heated_equilibrium = equilibrium + balance["battery_conversion_loss_w"]*b["thermal_time_constant_s"]/b["thermal_capacity_j_k"]
                self._state["temperature_c"] = heated_equilibrium + (self._state["temperature_c"]-heated_equilibrium)*math.exp(-dt/b["thermal_time_constant_s"])
            integrated = elapsed
        elif last is not None:
            quality = "same_timestamp"
        self._state["last_utc_s"], self._state["previous_input"] = data["utc_s"], data
        result = self._evaluate(data)
        result.update({"integration_quality": quality, "integrated_seconds": integrated,
                       "unmodelled_seconds": self._state["unmodelled_seconds"],
                       "charge_throughput_ah": self._state["charge_throughput_ah"],
                       "discharge_throughput_ah": self._state["discharge_throughput_ah"],
                       "cumulative_solar_wh": self._state["cumulative_solar_wh"],
                       "cumulative_served_load_wh": self._state["cumulative_served_load_wh"],
                       "cumulative_losses_wh": self._state["cumulative_losses_wh"],
                       "cumulative_curtailed_wh": self._state["cumulative_curtailed_wh"],
                       "cumulative_unserved_wh": self._state["cumulative_unserved_wh"]})
        return result
