"""Bounded read-only Unix-socket adapter to the resident C core readers."""

from __future__ import annotations

import json
import socket
import struct
import threading
from pathlib import Path


class CoreUnavailable(Exception):
    pass


_core_slots = threading.BoundedSemaphore(4)
_MAX_RESPONSE = 1024


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
