"""Bounded read-only Unix-socket adapter to the resident C core readers."""

from __future__ import annotations

import json
import math
import re
import socket
import struct
import threading
from pathlib import Path


class CoreUnavailable(Exception):
    pass


_core_slots = threading.BoundedSemaphore(4)
_MAX_RESPONSE = 60000


def _read_exact(connection: socket.socket, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        part = connection.recv(length - len(result))
        if not part:
            raise CoreUnavailable("core connection closed")
        result.extend(part)
    return bytes(result)


def _query(socket_path: Path, operation: str) -> dict:
    if not _core_slots.acquire(blocking=False):
        raise CoreUnavailable("core reader busy")
    try:
        request = ('{"op":"' + operation + '"}').encode("ascii")
        with socket.socket(getattr(socket, "AF_UNIX", -1), socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(socket_path))
            connection.sendall(struct.pack(">I", len(request)) + request)
            length = struct.unpack(">I", _read_exact(connection, 4))[0]
            if not 0 < length <= _MAX_RESPONSE:
                raise CoreUnavailable("invalid core response size")
            response = _read_exact(connection, length)
        value = json.loads(response)
        if not isinstance(value, dict) or "error" in value:
            raise CoreUnavailable("core reader failed")
        return value
    except (OSError, UnicodeError, ValueError, struct.error) as exc:
        raise CoreUnavailable("core reader unavailable") from exc
    finally:
        _core_slots.release()


def ros_summary(socket_path: Path) -> dict:
    result = _query(socket_path, "overview")
    count = result.get("sim_frame_count")
    last = result.get("last_sequence")
    state = result.get("record_state")
    if (type(count) is not int or count < 0 or count > 4096 or
            (last is not None and (type(last) is not int or last < 0 or last > 0xffffffff)) or
            state not in ("absent", "empty", "validated") or
            (count == 0) != (last is None) or
            (count > 0) != (state == "validated")):
        raise CoreUnavailable("invalid ROS response")
    return {"sim_frame_count": count, "last_sequence": last,
            "record_state": state}


def mps_catalog(socket_path: Path) -> dict:
    result = _query(socket_path, "catalog")
    version = result.get("version")
    count = result.get("command_count")
    if (type(version) is not int or version < 1 or version > 0xffffffff or
            type(count) is not int or count != 0 or
            result.get("operational") is not False):
        raise CoreUnavailable("invalid MPS response")
    return {"version": version, "command_count": count}


def mps_passes(socket_path: Path) -> dict:
    result = _query(socket_path, "passes")
    state = result.get("schedule_state")
    digest = result.get("schedule_sha256")
    passes = result.get("passes")
    if (state not in ("absent", "validated") or result.get("operational") is not False or
            not isinstance(passes, list) or len(passes) > 64 or
            (state == "absent" and (digest is not None or passes)) or
            (state == "validated" and
             (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)))):
        raise CoreUnavailable("invalid MPS schedule response")
    previous = None
    seen = set()
    for item in passes:
        if not isinstance(item, dict) or set(item) != {
                "pass_id", "station_id", "band", "aos_utc_s", "los_utc_s",
                "peak_utc_s", "max_elevation_deg", "minimum_elevation_deg",
                "clipped_start", "clipped_end"}:
            raise CoreUnavailable("invalid MPS pass")
        identifier = item["pass_id"]
        station = item["station_id"]
        band = item["band"]
        aos, peak, los = (item[key] for key in ("aos_utc_s", "peak_utc_s", "los_utc_s"))
        low, high = item["minimum_elevation_deg"], item["max_elevation_deg"]
        if (not isinstance(identifier, str) or len(identifier) > 63 or
                not re.fullmatch(r"[A-Za-z0-9-]+", identifier) or identifier in seen or
                (station, band) not in (("yonsei", "UHF"), ("kari", "S_BAND")) or
                not identifier.startswith(station + "-") or
                any(type(value) is not int for value in (aos, peak, los)) or
                not 0 < aos < peak < los or los - aos > 72 * 3600 + 2 or
                any(type(value) not in (int, float) for value in (low, high)) or
                not 0 <= low <= high <= 90 or
                not all(math.isfinite(value) for value in (low, high)) or
                type(item["clipped_start"]) is not bool or
                type(item["clipped_end"]) is not bool or
                (previous is not None and aos < previous)):
            raise CoreUnavailable("invalid MPS pass")
        seen.add(identifier)
        previous = aos
    return {"passes": passes, "schedule_state": state,
            "schedule_sha256": digest, "operational": False}
