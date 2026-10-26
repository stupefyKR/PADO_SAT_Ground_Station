"""Shared TEST_ONLY Yonsei UHF contact gate for the virtual link.

The satellite uses this to schedule a stored downlink. The ground receiver
checks the same truth view independently before accepting any packet.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class ContactError(ValueError):
    """The virtual FDS contact view is invalid."""


def _unique_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContactError("duplicate FDS contact-view field")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ContactError(f"non-finite FDS contact field: {value}")


def _contact_utc_ms(value: Any) -> int:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ContactError("FDS contact time must be UTC")
    try:
        instant = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ContactError("invalid FDS contact time") from error
    if instant.microsecond % 1000:
        raise ContactError("FDS contact time must have millisecond precision")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = instant - epoch
    return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000


class ContactGate:
    """Gate virtual UHF traffic to FDS truth PASS windows, failing closed."""

    def __init__(self, view_path: Path):
        self.view_path = view_path
        self._lock = Lock()
        self._signature: tuple[int, int, int] | None = None
        self._coverage: tuple[int, int, float] | None = None
        self._windows: tuple[tuple[int, int], ...] = ()

    def _reload(self) -> None:
        # Imported only while evaluating the gate: emulator imports ContactGate.
        from .emulator import MAX_VIEW_BYTES, parse_orbit_view

        path = self.view_path
        if path.is_symlink() or not path.is_file():
            raise ContactError("regular FDS truth contact view required")
        metadata = path.stat()
        if not 1 <= metadata.st_size <= MAX_VIEW_BYTES:
            raise ContactError("FDS truth contact view size is invalid")
        signature = (metadata.st_ino, metadata.st_mtime_ns, metadata.st_size)
        if signature == self._signature:
            return
        raw = path.read_bytes()
        view = parse_orbit_view(raw)
        try:
            document = json.loads(raw.decode("utf-8"),
                                  object_pairs_hook=_unique_json_pairs,
                                  parse_constant=_reject_json_constant)
        except (UnicodeError, ValueError) as error:
            raise ContactError("invalid FDS truth contact view") from error
        stations = document.get("stations")
        if not isinstance(stations, list) or not 1 <= len(stations) <= 8:
            raise ContactError("FDS truth stations missing or unbounded")
        matching = [station for station in stations if isinstance(station, dict) and
                    station.get("id") == "yonsei" and station.get("band") == "UHF"]
        if len(matching) != 1:
            raise ContactError("one Yonsei UHF truth station required")
        passes = matching[0].get("passes")
        if not isinstance(passes, list) or len(passes) > 256:
            raise ContactError("Yonsei UHF truth passes invalid or unbounded")
        windows = []
        previous_los = view.start_s * 1000
        for contact in passes:
            if not isinstance(contact, dict):
                raise ContactError("invalid Yonsei UHF truth pass")
            aos = _contact_utc_ms(contact.get("aos_utc"))
            los = _contact_utc_ms(contact.get("los_utc"))
            if not (previous_los <= aos < los <= view.end_s * 1000 and
                    los - aos <= 2 * 60 * 60 * 1000):
                raise ContactError("Yonsei UHF truth PASS boundaries invalid")
            windows.append((aos, los))
            previous_los = los
        self._coverage = (view.start_s * 1000, view.end_s * 1000,
                          view.input_epoch_s * 1000)
        self._windows = tuple(windows)
        self._signature = signature

    def active(self, now_ms: int) -> bool:
        if type(now_ms) is not int or now_ms <= 0:
            return False
        with self._lock:
            try:
                self._reload()
            except (OSError, ValueError):
                self._signature = None
                self._coverage = None
                self._windows = ()
                return False
            start, end, epoch = self._coverage
            return (start <= now_ms <= end and
                    0 <= now_ms - epoch < 72 * 60 * 60 * 1000 and
                    any(aos <= now_ms < los for aos, los in self._windows))
