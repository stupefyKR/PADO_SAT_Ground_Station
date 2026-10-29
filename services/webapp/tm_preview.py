"""Validate a bounded, read-only ROS test snapshot before web publication."""

from __future__ import annotations

import json
import math
import os
import re
import stat
from datetime import datetime
from pathlib import Path


MAX_SNAPSHOT_BYTES = 1024 * 1024
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_ID = re.compile(r"[A-Za-z0-9._-]{1,48}")
_FRAME = re.compile(r"[A-Za-z0-9._-]{1,32}")


class PreviewError(ValueError):
    pass


def _pairs(items: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise PreviewError("duplicate_snapshot_field")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise PreviewError("non_finite_snapshot_value")


def _utc(value: object) -> datetime:
    if not isinstance(value, str) or not _UTC.fullmatch(value):
        raise PreviewError("invalid_snapshot_time")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PreviewError("invalid_snapshot_time") from error


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def validate_snapshot(raw: bytes) -> dict:
    if not 1 <= len(raw) <= MAX_SNAPSHOT_BYTES:
        raise PreviewError("snapshot_size_invalid")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_reject_constant)
    except (UnicodeError, ValueError) as error:
        raise PreviewError("snapshot_json_invalid") from error
    if (not isinstance(value, dict) or
            set(value) != {"type", "non_operational", "received_utc", "sensors", "attitude"} or
            value["type"] != "TEST_ONLY_TM_SNAPSHOT" or
            value["non_operational"] is not True or
            not isinstance(value["sensors"], list) or
            not 1 <= len(value["sensors"]) <= 64):
        raise PreviewError("snapshot_contract_invalid")
    received = _utc(value["received_utc"])
    seen = set()
    for sensor in value["sensors"]:
        if (not isinstance(sensor, dict) or
                set(sensor) != {"id", "name", "group", "unit", "lower", "upper", "samples"} or
                not isinstance(sensor["id"], str) or not _ID.fullmatch(sensor["id"]) or
                sensor["id"] in seen or
                any(not isinstance(sensor[key], str) or not 1 <= len(sensor[key]) <= maximum
                    for key, maximum in (("name", 64), ("group", 64), ("unit", 12))) or
                not _number(sensor["lower"]) or not _number(sensor["upper"]) or
                sensor["lower"] >= sensor["upper"] or
                not isinstance(sensor["samples"], list) or
                not 1 <= len(sensor["samples"]) <= 3600):
            raise PreviewError("snapshot_sensor_invalid")
        seen.add(sensor["id"])
        previous = None
        for sample in sensor["samples"]:
            if (not isinstance(sample, dict) or set(sample) != {"utc", "value"} or
                    not _number(sample["value"])):
                raise PreviewError("snapshot_sample_invalid")
            stamp = _utc(sample["utc"])
            if stamp > received or (previous is not None and stamp <= previous):
                raise PreviewError("snapshot_sample_time_invalid")
            previous = stamp
    attitude = value["attitude"]
    if attitude is not None:
        if (not isinstance(attitude, dict) or
                set(attitude) != {"utc", "frame", "quaternion_wxyz"} or
                not isinstance(attitude["frame"], str) or
                not _FRAME.fullmatch(attitude["frame"]) or
                not isinstance(attitude["quaternion_wxyz"], list) or
                len(attitude["quaternion_wxyz"]) != 4 or
                any(not _number(item) for item in attitude["quaternion_wxyz"]) or
                abs(math.hypot(*attitude["quaternion_wxyz"]) - 1.0) > 0.02 or
                _utc(attitude["utc"]) > received):
            raise PreviewError("snapshot_attitude_invalid")
    return value


def load_snapshot(path: Path | None) -> dict:
    if path is None or path.name != "test_tm_snapshot.json":
        raise PreviewError("snapshot_not_configured")
    if path.is_symlink():
        raise PreviewError("snapshot_file_invalid")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= MAX_SNAPSHOT_BYTES:
                raise PreviewError("snapshot_file_invalid")
            raw = source.read(MAX_SNAPSHOT_BYTES + 1)
    except OSError as error:
        raise PreviewError("snapshot_unavailable") from error
    return validate_snapshot(raw)


def load_beacon(snapshot_path: Path | None) -> dict:
    """Read the separate bounded beacon status; never treat an old file as link health."""
    if snapshot_path is None or snapshot_path.name != "test_tm_snapshot.json":
        raise PreviewError("beacon_not_configured")
    path = snapshot_path.with_name("test_beacon.json")
    if path.is_symlink():
        raise PreviewError("beacon_file_invalid")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= 4096:
                raise PreviewError("beacon_file_invalid")
            raw = source.read(4097)
        if len(raw) > 4096:
            raise PreviewError("beacon_file_invalid")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_reject_constant)
    except (OSError, UnicodeError, ValueError) as error:
        raise PreviewError("beacon_unavailable") from error
    bounds = {"mode": 255, "beacon_counter": 0xffffffff, "beacon_period_s": 65535,
              "boot_count": 65535, "reset_cause": 255, "fsw_image": 255,
              "crash_count": 65535, "watchdog_state": 255, "nor_usage_pct": 100,
              "fdir_level": 255, "fault_bitmap": 0xffffffff}
    if (not isinstance(value, dict) or set(value) != {"type", "non_operational",
            "sampled_utc", "received_utc", "sequence", "fields"} or
            value["type"] != "TEST_ONLY_BEACON" or value["non_operational"] is not True or
            type(value["sequence"]) is not int or not 0 <= value["sequence"] <= 65535 or
            not isinstance(value["fields"], dict) or set(value["fields"]) != set(bounds) or
            any(type(value["fields"][key]) is not int or
                not 0 <= value["fields"][key] <= limit for key, limit in bounds.items()) or
            value["fields"]["beacon_period_s"] == 0 or
            _utc(value["sampled_utc"]) > _utc(value["received_utc"])):
        raise PreviewError("beacon_contract_invalid")
    return value
