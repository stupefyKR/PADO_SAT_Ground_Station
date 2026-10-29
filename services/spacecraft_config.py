"""One validated spacecraft settings file shared by simulation and monitoring.

The checked-in example seeds config/spacecraft.json. PADO_SPACECRAFT_CONFIG
selects the same mounted file in containers; no process writes these defaults
as a side effect of reading them.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_SETTINGS_BYTES = 128 * 1024
MODES = ("detumbling", "standby", "communication", "crest", "siren", "safehold")
REQUIRED_DEVICES = frozenset({"eps_board", "battery_auxiliary", "s_band", "crest",
    "gnss_receiver", "gnss_antenna", "uhf", "obc", "fine_sun_sensor", "siren",
    "adcs_board", "reaction_wheel", "magnetorquer", "magnetometer"})


class SettingsError(ValueError):
    pass


def resolve_settings_path(path: str | Path | None = None, *, writable: bool = False) -> Path:
    if path is not None:
        return Path(path)
    configured = os.environ.get("PADO_SPACECRAFT_CONFIG")
    if configured is not None:
        if not configured:
            raise SettingsError("spacecraft_config_path_empty")
        return Path(configured)
    actual = ROOT / "config/spacecraft.json"
    return actual if writable or actual.exists() or actual.is_symlink() else ROOT / "config/spacecraft.example.json"


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise SettingsError("duplicate_spacecraft_setting")
        result[key] = value
    return result


def _finite(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise SettingsError(f"{label} must be finite")
    return float(value)


def _vector(value, length, label):
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise SettingsError(f"invalid {label}")
    return tuple(_finite(entry, label) for entry in value)


def _norm(values):
    return math.hypot(*values)


def settings_sha256(settings: dict) -> str:
    try:
        return hashlib.sha256(json.dumps(settings, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    except (TypeError, ValueError) as error:
        raise SettingsError("invalid_spacecraft_settings") from error


def get_power_profile(settings: dict | None = None) -> dict:
    """Expand each bank from one cell-type rating without duplicated constants."""
    document = load_settings() if settings is None else settings
    profile = copy.deepcopy(document["power"])
    cells = profile["solar"].pop("cell_types")
    for bank in profile["solar"]["banks"]:
        cell = cells[bank["kind"]]
        bank["cell_voltage_v"] = cell["voltage_v"]
        bank["cell_current_a"] = cell["current_a"]
    return profile


def get_sensor_limits(settings: dict | None = None) -> dict:
    document = load_settings() if settings is None else settings
    return {"sensors": copy.deepcopy(document["sensor_limits"])}


def load_settings(path: str | Path | None = None) -> dict:
    source = resolve_settings_path(path)
    try:
        if source.is_symlink() or not source.is_file() or not 1 <= source.stat().st_size <= MAX_SETTINGS_BYTES:
            raise SettingsError("regular_bounded_spacecraft_config_required")
        raw = source.read_bytes()
        if len(raw) > MAX_SETTINGS_BYTES:
            raise SettingsError("spacecraft_config_too_large")
        document = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(SettingsError("nonfinite_spacecraft_setting")))
        return validate_settings(document)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SettingsError("spacecraft_config_unavailable_or_invalid") from error


def validate_settings(document: object) -> dict:
    """Validate the entire edit before any writer publishes the new file."""
    try:
        if not isinstance(document, dict) or set(document) != {"schema", "power", "sensor_limits"} or \
                document["schema"] != "pado.spacecraft-settings.v1":
            raise SettingsError("unsupported_spacecraft_settings")
        profile = document["power"]
        if not isinstance(profile, dict) or not isinstance(profile["solar"], dict):
            raise SettingsError("invalid_power_settings")
        cells = profile["solar"]["cell_types"]
        if not isinstance(cells, dict) or not 1 <= len(cells) <= 16:
            raise SettingsError("invalid_solar_cell_types")
        for kind, cell in cells.items():
            if not isinstance(kind, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", kind) or \
                    not isinstance(cell, dict) or set(cell) != {"voltage_v", "current_a"}:
                raise SettingsError("invalid_solar_cell_type")
            if any(not 0 < _finite(cell[key], key) <= 1000 for key in ("voltage_v", "current_a")):
                raise SettingsError("invalid_solar_cell_rating")
        for bank in profile["solar"]["banks"]:
            if bank.get("kind") not in cells or "cell_voltage_v" in bank or "cell_current_a" in bank:
                raise SettingsError("solar_bank_must_reference_one_cell_type")
        validate_power_profile(get_power_profile(document))
        limits = document["sensor_limits"]
        if not isinstance(limits, dict) or not 1 <= len(limits) <= 64:
            raise SettingsError("invalid_sensor_limits")
        from tools.pado_packet_codec import load_catalog
        fields = {entry["name"]: entry["fields"] for entry in load_catalog()["telemetry"]}
        ids = set()
        for key, rule in limits.items():
            if not isinstance(key, str) or key.count(".") != 1:
                raise SettingsError("invalid_sensor_key")
            family, field = key.split(".")
            if (fields.get(family) or {}).get(field) not in ("u8", "u16", "u32", "u64", "i32", "f32", "f64") or \
                    not isinstance(rule, dict) or set(rule) != {"id", "name", "group", "unit", "lower", "upper"}:
                raise SettingsError("invalid_sensor_limit")
            if not isinstance(rule["id"], str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,48}", rule["id"]) or rule["id"] in ids:
                raise SettingsError("invalid_sensor_id")
            ids.add(rule["id"])
            for name, maximum in (("name", 64), ("group", 64), ("unit", 12)):
                if not isinstance(rule[name], str) or not 1 <= len(rule[name]) <= maximum:
                    raise SettingsError("invalid_sensor_metadata")
            if _finite(rule["lower"], "sensor lower") >= _finite(rule["upper"], "sensor upper"):
                raise SettingsError("invalid_sensor_range")
        result = copy.deepcopy(document)
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_SETTINGS_BYTES:
            raise SettingsError("spacecraft_config_too_large")
        return result
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError, ValueError) as error:
        if isinstance(error, SettingsError):
            raise
        raise SettingsError("invalid_spacecraft_settings") from error


def validate_power_profile(profile: dict) -> None:
    """Physical schema validation is shared without importing the simulator."""
    # The complete battery/geometry checks below also protect legacy direct
    # PowerModel inputs. Numerical algorithm constants remain in the model.
    p = profile
    if p.get("schema") != "pado-sim-power-profile-v1" or p.get("test_only") is not True:
        raise ValueError("unsupported power profile")
    for key, upper in (("max_step_s", 5.0), ("max_catchup_s", 86400.0)):
        if not 0 < _finite(p[key], key) <= upper:
            raise ValueError(f"invalid {key}")
    b, s = p["battery"], p["solar"]
    for key in ("capacity_wh", "nominal_voltage_v", "minimum_voltage_v", "maximum_voltage_v",
                "max_charge_current_a", "max_discharge_current_a", "thermal_capacity_j_k", "thermal_time_constant_s"):
        if _finite(b[key], key) <= 0:
            raise ValueError(f"invalid {key}")
    if _finite(b["internal_resistance_ohm"], "resistance") < 0:
        raise ValueError("negative battery resistance")
    if not b["minimum_voltage_v"] < b["maximum_voltage_v"]:
        raise ValueError("invalid voltage bounds")
    for key in ("charge_efficiency", "discharge_efficiency"):
        if not 0 < _finite(b[key], key) <= 1:
            raise ValueError(f"invalid {key}")
    if not 0 <= _finite(b["initial_soc"], "initial SOC") <= 1:
        raise ValueError("invalid initial SOC")
    for key in ("initial_temperature_c", "sunlit_equilibrium_c", "eclipse_equilibrium_c"):
        _finite(b[key], key)
    curve = b["ocv_curve"]
    if len(curve) < 2 or curve[0][0] != 0 or curve[-1][0] != 1:
        raise ValueError("OCV curve must span SOC zero to one")
    for index, point in enumerate(curve):
        soc, voltage = _vector(point, 2, "OCV point")
        if not 0 <= soc <= 1 or not b["minimum_voltage_v"] <= voltage <= b["maximum_voltage_v"]:
            raise ValueError("OCV point outside configured bounds")
        if index and (soc <= curve[index-1][0] or voltage < curve[index-1][1]):
            raise ValueError("OCV curve must be monotonic")
    if not 0 < _finite(s["conversion_efficiency"], "solar efficiency") <= 1:
        raise ValueError("invalid solar efficiency")
    for key in ("earth_radius_m", "sun_radius_m"):
        if _finite(s[key], key) <= 0:
            raise ValueError("invalid body radius")
    if not s["banks"] or len(s["banks"]) > 6:
        raise ValueError("one to six solar banks required")
    ids, channels = set(), set()
    for bank in s["banks"]:
        if bank["id"] in ids or bank["channel"] in channels:
            raise ValueError("duplicate solar bank")
        ids.add(bank["id"])
        channels.add(bank["channel"])
        for key in ("cell_count", "series", "parallel"):
            if isinstance(bank[key], bool) or not isinstance(bank[key], int) or bank[key] <= 0:
                raise ValueError("invalid solar wiring")
        if bank["series"] * bank["parallel"] != bank["cell_count"]:
            raise ValueError("solar wiring must account for all cells")
        for key in ("cell_voltage_v", "cell_current_a"):
            if _finite(bank[key], key) <= 0:
                raise ValueError("invalid cell rating")
        if abs(_norm(_vector(bank["normal_body"], 3, "panel normal")) - 1) > 1e-6:
            raise ValueError("panel normal must be a unit vector")
    if not p["devices"]:
        raise ValueError("device loads required")
    for device in p["devices"].values():
        if device["voltage_v"] is not None and _finite(device["voltage_v"], "device voltage") <= 0:
            raise ValueError("invalid device voltage")
        for mode in MODES:
            if _finite(device["power_w_by_mode"][mode], "device power") < 0:
                raise ValueError("negative device load")

    if not REQUIRED_DEVICES <= set(p["devices"]):
        raise SettingsError("required power devices missing")
    for bank in s["banks"]:
        if type(bank["channel"]) is not int or not 0 <= bank["channel"] < 6:
            raise SettingsError("invalid solar bank channel")
    telemetry = p["telemetry"]
    if not 0 < _finite(telemetry["regulated_bus_voltage_v"], "regulated bus voltage") <= 100:
        raise SettingsError("invalid regulated bus voltage")
    if not 0 < _finite(telemetry["logic_supply_voltage_v"], "logic supply voltage") <= 100:
        raise SettingsError("invalid logic supply voltage")
    levels = telemetry["battery_mode_soc"]
    values = [_finite(levels[key], "battery SOC mode") for key in ("critical", "safe", "full")]
    if not 0 <= values[0] < values[1] < values[2] <= 1:
        raise SettingsError("invalid battery SOC mode thresholds")
    _vector(telemetry["battery_temperature_offsets_c"], 4, "battery temperature offsets")
    rails = telemetry["srs_internal_rails_v"]
    if set(rails) != {"3v3", "1v8", "1v0", "pa"} or any(not 0 < _finite(value, "SRS rail voltage") <= 100 for value in rails.values()):
        raise SettingsError("invalid SRS rail voltages")
