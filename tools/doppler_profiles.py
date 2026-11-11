"""Validated, current-only Doppler products derived from completed FDS runs.

The CSV remains an FDS output. This module checks and publishes it; it does not
propagate an orbit or calculate a replacement profile in the web service.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from .stage_fds_outbox import FILES, _bytes, _pair, _unique_pairs
else:
    from stage_fds_outbox import FILES, _bytes, _pair, _unique_pairs


BUNDLE_NAME = "doppler_profiles.json"
MAX_BUNDLE_BYTES = 12 * 1024 * 1024
MAX_PROFILE_BYTES = 8 * 1024 * 1024
MAX_VIEW_BYTES = 4 * 1024 * 1024
LIGHT_SPEED_MPS = 299792458.0
PASS_ID = re.compile(r"(?:yonsei|kari)-[A-Za-z0-9-]{1,54}")
DIGEST = re.compile(r"[0-9a-f]{64}")
UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")
STATIONS = {"yonsei": "UHF", "kari": "S_BAND"}
SCHEDULE_HEADER = ("schema,station_id,band,pass_id,aos_utc,los_utc,peak_utc,"
                   "max_elevation_deg,minimum_elevation_deg,clipped_start,clipped_end")
PROFILE_HEADER = ("schema,station_id,band,pass_id,utc,azimuth_deg,elevation_deg,"
                  "range_m,range_rate_mps,carrier_hz,doppler_shift_hz,received_hz,frequency_example")


class DopplerError(ValueError):
    def __init__(self, code: str = "doppler_unavailable"):
        super().__init__(code)
        self.code = code


def _stamp(value: object) -> datetime:
    if not isinstance(value, str) or not UTC.fullmatch(value):
        raise ValueError("UTC timestamp required")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _number(value: str, low: float, high: float) -> float:
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError("Doppler numeric field outside range")
    return number


def _boolean(value: str) -> bool:
    if value not in ("true", "false"):
        raise ValueError("Doppler boolean required")
    return value == "true"


def _csv(data: bytes, header: str, maximum_rows: int) -> tuple[list[list[str]], list[bytes]]:
    text = data.decode("ascii")
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise ValueError("invalid Doppler CSV") from error
    lines = data.splitlines(keepends=True)
    if (not rows or ",".join(rows[0]) != header or
            len(rows) != len(lines) or len(rows) - 1 > maximum_rows or
            any(len(row) != len(rows[0]) for row in rows) or
            any(not line.endswith(b"\n") for line in lines)):
        raise ValueError("invalid Doppler CSV structure")
    return rows[1:], lines


def _provenance(orbit: dict) -> dict:
    if not isinstance(orbit, dict):
        raise ValueError("FDS orbit object required")
    required = ("norad_id", "source", "input_epoch_utc", "input_sha256", "start_utc", "end_utc")
    if any(key not in orbit for key in (*required, "contact_schedule_sha256")):
        raise ValueError("missing Doppler provenance")
    result = {key: orbit[key] for key in required}
    result["schedule_sha256"] = orbit["contact_schedule_sha256"]
    if (orbit.get("schema") != "fds.orbit-view.v1" or
            orbit.get("quality") != "PRELIMINARY_NOT_FOR_OPERATIONS" or
            type(result["norad_id"]) is not int or not 1 <= result["norad_id"] <= 99999 or
            result["source"] not in ("TLE", "GNSS") or
            any(not isinstance(result[key], str) or not DIGEST.fullmatch(result[key])
                for key in ("schedule_sha256", "input_sha256"))):
        raise ValueError("invalid Doppler provenance")
    start, end, epoch = (_stamp(result[key]) for key in ("start_utc", "end_utc", "input_epoch_utc"))
    if not epoch <= start < end or (end - start).total_seconds() > 72 * 3600 or \
            (start - epoch).total_seconds() >= 72 * 3600:
        raise ValueError("invalid Doppler forecast window")
    return result


def _schedule(data: bytes, provenance: dict) -> dict[str, dict]:
    rows, _ = _csv(data, SCHEDULE_HEADER, 64)
    passes = {}
    start, end = (_stamp(provenance[key]) for key in ("start_utc", "end_utc"))
    for row in rows:
        schema, station, band, identifier, aos, los, peak, high, low, clipped_aos, clipped_los = row
        if (schema != "fds.contact-schedule.v1" or STATIONS.get(station) != band or
                not PASS_ID.fullmatch(identifier) or not identifier.startswith(station + "-") or
                identifier in passes or not start <= _stamp(aos) < _stamp(peak) < _stamp(los) <= end):
            raise ValueError("invalid Doppler schedule identity or times")
        minimum, maximum = _number(low, 0, 90), _number(high, 0, 90)
        if minimum > maximum:
            raise ValueError("invalid Doppler schedule elevation")
        passes[identifier] = {"pass_id": identifier, "station_id": station, "band": band,
            "aos_utc": aos, "los_utc": los, "peak_utc": peak,
            "minimum_elevation_deg": minimum, "max_elevation_deg": maximum,
            "clipped_start": _boolean(clipped_aos), "clipped_end": _boolean(clipped_los)}
    return passes


def _orbit_passes(orbit: dict, provenance: dict) -> set[tuple]:
    stations = orbit.get("stations")
    if (not isinstance(stations, list) or len(stations) != 2 or
            {station.get("id") for station in stations if isinstance(station, dict)} != set(STATIONS)):
        raise ValueError("Doppler station set mismatch")
    start, end = (_stamp(provenance[key]) for key in ("start_utc", "end_utc"))
    result = set()
    for station in stations:
        if station.get("band") != STATIONS[station["id"]] or not isinstance(station.get("passes"), list):
            raise ValueError("invalid Doppler orbit station")
        for item in station["passes"]:
            aos, los = _stamp(item["aos_utc"]), _stamp(item["los_utc"])
            key = station["id"], aos, los
            if not start <= aos < los <= end or key in result:
                raise ValueError("invalid Doppler orbit PASS")
            result.add(key)
        if len(result) > 64:
            raise ValueError("Doppler orbit PASS count exceeds limit")
    return result


def _schedule_matches_orbit(passes: dict, orbit: dict, provenance: dict) -> None:
    windows = {(item["station_id"], _stamp(item["aos_utc"]), _stamp(item["los_utc"]))
               for item in passes.values()}
    if windows != _orbit_passes(orbit, provenance) or len(windows) != len(passes):
        raise ValueError("Doppler schedule and orbit PASS windows differ")


def _profile(data: bytes, expected: dict, step: int) -> dict:
    rows, _ = _csv(data, PROFILE_HEADER, 50000)
    if len(rows) < 2:
        raise ValueError("Doppler profile needs AOS and LOS samples")
    start, end = _stamp(expected["aos_utc"]), _stamp(expected["los_utc"])
    samples = []
    carrier = None
    example = None
    previous = None
    for index, row in enumerate(rows):
        schema, station, band, identifier, utc, azimuth, elevation, distance, rate, frequency, shift, received, flag = row
        if (schema != "fds.pass-information.v1" or station != expected["station_id"] or
                band != expected["band"] or identifier != expected["pass_id"]):
            raise ValueError("Doppler profile belongs to another PASS")
        stamp = _stamp(utc)
        if not start <= stamp <= end or (previous is not None and stamp <= previous):
            raise ValueError("Doppler samples are outside the PASS or unordered")
        if previous is not None:
            difference = (stamp - previous).total_seconds()
            if difference > step + .002 or (index < len(rows) - 1 and abs(difference - step) > .002):
                raise ValueError("Doppler sample cadence mismatch")
        previous = stamp
        _number(azimuth, 0, 360)
        _number(elevation, -90, 90)
        _number(distance, 1, 1e10)
        range_rate = _number(rate, -100000, 100000)
        hz = _number(frequency, 1e6, 20e9)
        delta = _number(shift, -1e8, 1e8)
        rx = _number(received, 1, 21e9)
        is_example = _boolean(flag)
        if carrier is not None and (hz != carrier or is_example != example):
            raise ValueError("Doppler carrier changes within PASS")
        # The CSV carrier has three decimals, while received/shift retain six
        # and were calculated using the full configured carrier precision.
        if (not math.isclose(delta, -hz * range_rate / LIGHT_SPEED_MPS, rel_tol=0, abs_tol=.0001) or
                not math.isclose(rx, hz + delta, rel_tol=0, abs_tol=.001)):
            raise ValueError("Doppler profile frequency relationship mismatch")
        carrier, example = hz, is_example
        samples.append([utc, range_rate, delta, rx])
    if _stamp(samples[0][0]) != start or _stamp(samples[-1][0]) != end:
        raise ValueError("Doppler AOS/LOS endpoints missing")
    return {"pass_id": expected["pass_id"], "station_id": expected["station_id"],
        "band": expected["band"], "aos_utc": expected["aos_utc"], "los_utc": expected["los_utc"],
        "carrier_hz": carrier, "frequency_example": example, "sample_step_s": step,
        "profile_sha256": hashlib.sha256(data).hexdigest(), "samples": samples}


def build_bundle(run: Path, orbit: dict) -> bytes | None:
    """Check every per-pass file against the complete, hashed FDS products."""
    directory = run / "doppler_profiles"
    advertised = isinstance(orbit, dict) and "contact_schedule_sha256" in orbit
    if not advertised and not directory.exists() and not directory.is_symlink():
        return None
    if run.is_symlink() or directory.is_symlink() or not directory.is_dir():
        raise ValueError("regular FDS Doppler output directory required")
    provenance = _provenance(orbit)
    schedule, _ = _pair(run, *FILES[0])
    aggregate, _ = _pair(run, *FILES[1])
    if hashlib.sha256(schedule).hexdigest() != provenance["schedule_sha256"]:
        raise ValueError("Doppler contact schedule hash mismatch")
    properties = dict(line.split("=", 1) for line in _bytes(run / "run.properties", 8192)
                      .decode("ascii").splitlines() if "=" in line)
    step = int(properties.get("profile_step_s", ""))
    if not 1 <= step <= 60:
        raise ValueError("invalid Doppler sample cadence")
    passes = _schedule(schedule, provenance)
    _schedule_matches_orbit(passes, orbit, provenance)
    rows, lines = _csv(aggregate, PROFILE_HEADER, 50000)
    grouped = {identifier: [] for identifier in passes}
    for row, line in zip(rows, lines[1:]):
        identifier = row[3]
        if identifier not in grouped:
            raise ValueError("profile row has no N13 PASS")
        grouped[identifier].append(line)
    if {path.name for path in directory.iterdir()} != {identifier + ".csv" for identifier in passes}:
        raise ValueError("Doppler file set does not match N13")
    profiles = {}
    for identifier, expected in passes.items():
        raw = _bytes(directory / (identifier + ".csv"), MAX_PROFILE_BYTES)
        if raw != lines[0] + b"".join(grouped[identifier]):
            raise ValueError("Doppler file differs from FDS aggregate")
        metadata = _profile(raw, expected, step)
        profiles[identifier] = {"pass": expected, "profile_sha256": metadata["profile_sha256"],
                                "csv": raw.decode("ascii")}
    bundle = {"schema": "fds.doppler-bundle.v1", "provenance": provenance,
              "sample_step_s": step, "schedule_csv": schedule.decode("ascii"), "profiles": profiles}
    encoded = (json.dumps(bundle, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
    if len(encoded) > MAX_BUNDLE_BYTES:
        raise ValueError("Doppler publication exceeds size limit")
    return encoded


def select_profile(view: Path, pass_id: str, schedule_sha256: str,
                   now_s: float | None = None) -> tuple[dict, bytes]:
    """Read a coherent publication; never select files using a client path."""
    if (not isinstance(pass_id, str) or not PASS_ID.fullmatch(pass_id) or
            not isinstance(schedule_sha256, str) or not DIGEST.fullmatch(schedule_sha256)):
        raise DopplerError("invalid_query")
    try:
        if view.name != "orbit_view.json" or view.parent.is_symlink() or not view.parent.is_dir():
            raise ValueError("regular FDS publication root required")
        orbit = json.loads(_bytes(view, MAX_VIEW_BYTES), object_pairs_hook=_unique_pairs)
        provenance = _provenance(orbit)
        if schedule_sha256 != provenance["schedule_sha256"]:
            raise DopplerError("schedule_changed")
        now = datetime.fromtimestamp(now_s, timezone.utc) if now_s is not None else datetime.now(timezone.utc)
        if (not _stamp(provenance["start_utc"]) <= now <= _stamp(provenance["end_utc"]) or
                (now - _stamp(provenance["input_epoch_utc"])).total_seconds() >= 72 * 3600):
            raise ValueError("Doppler prediction no longer current")
        bundle = json.loads(_bytes(view.with_name(BUNDLE_NAME), MAX_BUNDLE_BYTES),
                            object_pairs_hook=_unique_pairs)
        if (not isinstance(bundle, dict) or set(bundle) != {"schema", "provenance", "sample_step_s", "schedule_csv", "profiles"} or
                bundle["schema"] != "fds.doppler-bundle.v1" or bundle["provenance"] != provenance or
                type(bundle["sample_step_s"]) is not int or not 1 <= bundle["sample_step_s"] <= 60 or
                not isinstance(bundle["schedule_csv"], str) or
                not isinstance(bundle["profiles"], dict) or len(bundle["profiles"]) > 64):
            raise ValueError("Doppler publication provenance mismatch")
        schedule = bundle["schedule_csv"].encode("ascii")
        if not 0 < len(schedule) <= FILES[0][3] or hashlib.sha256(schedule).hexdigest() != schedule_sha256:
            raise ValueError("Doppler publication schedule hash mismatch")
        passes = _schedule(schedule, provenance)
        _schedule_matches_orbit(passes, orbit, provenance)
        if set(bundle["profiles"]) != set(passes):
            raise ValueError("Doppler publication PASS set mismatch")
        entry = bundle["profiles"].get(pass_id)
        if entry is None:
            raise DopplerError("pass_not_found")
        if (not isinstance(entry, dict) or set(entry) != {"pass", "profile_sha256", "csv"} or
                not isinstance(entry["pass"], dict) or not isinstance(entry["csv"], str) or
                entry["pass"] != passes[pass_id] or
                entry["pass"].get("pass_id") != pass_id or
                STATIONS.get(entry["pass"].get("station_id")) != entry["pass"].get("band") or
                not pass_id.startswith(entry["pass"]["station_id"] + "-")):
            raise ValueError("invalid Doppler profile entry")
        raw = entry["csv"].encode("ascii")
        if not 0 < len(raw) <= MAX_PROFILE_BYTES:
            raise ValueError("Doppler profile exceeds size limit")
        payload = _profile(raw, entry["pass"], bundle["sample_step_s"])
        if payload["profile_sha256"] != entry["profile_sha256"]:
            raise ValueError("Doppler profile hash mismatch")
        payload.update(schema="fds.doppler-profile.v1", schedule_sha256=schedule_sha256,
                       norad_id=provenance["norad_id"], source=provenance["source"],
                       input_epoch_utc=provenance["input_epoch_utc"])
        return payload, raw
    except DopplerError:
        raise
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
        raise DopplerError() from error
