"""Bounded read-only adapter to the non-operational C core probe."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path


class CoreUnavailable(Exception):
    pass


_probe_slots = threading.BoundedSemaphore(4)


def _probe(binary: Path, command: str, source: Path) -> dict:
    if not _probe_slots.acquire(blocking=False):
        raise CoreUnavailable("core probe busy")
    try:
        result = subprocess.run(
            [str(binary), command, str(source)],
            capture_output=True, timeout=2, check=False,
        )
        if result.returncode != 0 or len(result.stdout) > 1024:
            raise CoreUnavailable("core probe failed")
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise CoreUnavailable("invalid core response")
        return value
    except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError) as exc:
        raise CoreUnavailable("core probe unavailable") from exc
    finally:
        _probe_slots.release()


def ros_summary(binary: Path, record: Path) -> dict:
    result = _probe(binary, "ros-summary", record)
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


def mps_catalog(binary: Path, command_db: Path) -> dict:
    result = _probe(binary, "mps-catalog", command_db)
    version = result.get("version")
    count = result.get("command_count")
    if (type(version) is not int or version < 1 or version > 0xffffffff or
            type(count) is not int or count != 0 or
            result.get("operational") is not False):
        raise CoreUnavailable("invalid MPS response")
    return {"version": version, "command_count": count}
