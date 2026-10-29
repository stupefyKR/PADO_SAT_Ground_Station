"""Bounded FDS ephemeris history shared by its publisher and read-only portal.

This stores predictions, never spacecraft attitude. A fixed one-minute grid
prevents overlapping 72-hour products from growing with every refresh.
"""

from __future__ import annotations

import bisect
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


HISTORY_NAME = "orbit_history.sqlite3"
WINDOW_S = 72 * 3600
MAX_ROWS = 5000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_DATABASE_BYTES = 32 * 1024 * 1024


class OrbitHistoryError(ValueError):
    pass


def _stamp(value: str) -> int:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise OrbitHistoryError("invalid_orbit_history")
    return round(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def _utc(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def _number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _path(path: Path, *, existing=False) -> None:
    if (not isinstance(path, Path) or path.name != HISTORY_NAME or path.is_symlink() or
            path.parent.is_symlink() or not path.parent.is_dir() or
            (existing and not path.is_file()) or (path.exists() and not path.is_file())):
        raise OrbitHistoryError("orbit_history_unavailable")
    if path.exists() and not 1 <= path.stat().st_size <= MAX_DATABASE_BYTES:
        raise OrbitHistoryError("invalid_orbit_history")


def record_history(payload: dict, destination: Path, *, now_s: int | None = None) -> None:
    """Save a validated FDS product with one atomic SQLite transaction."""
    now_s = int(datetime.now(timezone.utc).timestamp()) if now_s is None else now_s
    path = destination / HISTORY_NAME
    _path(path)
    try:
        epoch = _stamp(payload["input_epoch_utc"])
        norad = payload["norad_id"]
        source = payload["source"]
        rows = payload["samples"]
        if (payload.get("schema") != "fds.orbit-view.v1" or
                payload.get("quality") != "PRELIMINARY_NOT_FOR_OPERATIONS" or
                type(norad) is not int or not 1 <= norad <= 99999 or source not in ("TLE", "GNSS") or
                not isinstance(rows, list) or not 2 <= len(rows) <= 5000):
            raise OrbitHistoryError("invalid_orbit_history")
        times = []
        for row in rows:
            if (not isinstance(row, list) or len(row) != 10 or
                    any(not _number(v) for v in row[1:])):
                raise OrbitHistoryError("invalid_orbit_history")
            instant = _stamp(row[0])
            if (instant < epoch or instant - epoch > WINDOW_S * 1000 or
                    not 6.3e6 <= math.hypot(*row[1:4]) <= 8e6 or
                    not .9 <= math.hypot(*row[7:10]) <= 1.1 or
                    (times and not 0 < instant-times[-1] <= 120000)):
                raise OrbitHistoryError("invalid_orbit_history")
            times.append(instant)
        connection = sqlite3.connect(path, timeout=5)
        try:
            os.chmod(path, 0o644)
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA trusted_schema=OFF")
            size = connection.execute("PRAGMA page_size").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count={MAX_DATABASE_BYTES // size}")
            if connection.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
                raise OrbitHistoryError("invalid_orbit_history")
            with connection:
                # The connection context does not begin a transaction for
                # SELECT/DDL. Serialize revision reads with the ensuing data
                # replacement so independent publishers cannot reuse a revision.
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE IF NOT EXISTS samples ("
                    "norad INTEGER NOT NULL,utc_ms INTEGER NOT NULL,x REAL NOT NULL,y REAL NOT NULL,"
                    "z REAL NOT NULL,sx REAL NOT NULL,sy REAL NOT NULL,sz REAL NOT NULL,"
                    "source TEXT NOT NULL,epoch_ms INTEGER NOT NULL,revision INTEGER NOT NULL,"
                    "edge INTEGER NOT NULL,PRIMARY KEY(norad,utc_ms))")
                connection.execute("CREATE TABLE IF NOT EXISTS meta ("
                    "id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,norad INTEGER NOT NULL)")
                connection.execute("PRAGMA user_version=1")
                old = connection.execute("SELECT revision FROM meta WHERE id=1").fetchone()
                revision = (old[0] if old else 0) + 1
                previous = connection.execute("SELECT MAX(utc_ms) FROM samples "
                    "WHERE norad=? AND utc_ms<=?", (norad, times[0])).fetchone()[0]
                grid = list(range((times[0]+59999)//60000*60000, times[-1]+1, 60000))
                # A new start anchor is needed only when existing predictions
                # cannot bracket it; repeated refreshes do not add dense edges.
                if previous is None or times[0]-previous > 120000:
                    grid.append(times[0])
                grid.append(times[-1])
                entries = []
                for instant in sorted(set(grid)):
                    if instant-epoch >= WINDOW_S*1000:
                        continue
                    right = min(len(times)-1, bisect.bisect_right(times, instant))
                    left = max(0, right-1)
                    fraction = (instant-times[left])/(times[right]-times[left])
                    values = [rows[left][i] + fraction*(rows[right][i]-rows[left][i])
                              for i in (1,2,3,7,8,9)]
                    entries.append((norad, instant, *values, source, epoch, revision,
                                    int(instant % 60000 != 0)))
                connection.executemany("INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(norad,utc_ms) DO UPDATE SET x=excluded.x,y=excluded.y,z=excluded.z,"
                    "sx=excluded.sx,sy=excluded.sy,sz=excluded.sz,source=excluded.source,"
                    "epoch_ms=excluded.epoch_ms,revision=excluded.revision,edge=excluded.edge", entries)
                connection.execute("DELETE FROM samples WHERE utc_ms<? OR utc_ms>?",
                    ((now_s-WINDOW_S)*1000-120000, (now_s+WINDOW_S)*1000+120000))
                # Only the latest far edge is needed while it remains future.
                connection.execute("DELETE FROM samples WHERE norad=? AND edge=1 "
                    "AND utc_ms>? AND utc_ms!=?", (norad, now_s*1000, times[-1]))
                connection.execute("INSERT INTO meta VALUES (1,?,?) ON CONFLICT(id) "
                    "DO UPDATE SET revision=excluded.revision,norad=excluded.norad", (revision,norad))
        finally:
            connection.close()
    except (KeyError, TypeError, ValueError, OSError, sqlite3.Error) as error:
        if isinstance(error, OrbitHistoryError):
            raise
        raise OrbitHistoryError("invalid_orbit_history") from error


def read_history(path: Path | None, *, start_utc_s: int, end_utc_s: int,
                 norad_id: int | None = None, now_s: int | None = None) -> dict:
    now_s = int(datetime.now(timezone.utc).timestamp()) if now_s is None else now_s
    result = {"schema":"pado.orbit-history.v1", "state":"WAITING", "revision":0,
        "server_utc_s":now_s, "start_utc_s":start_utc_s, "end_utc_s":end_utc_s,
        "sample_step_s":60, "max_sample_gap_s":120, "norad_id":norad_id,
        "quality":"PRELIMINARY_NOT_FOR_OPERATIONS", "non_operational":True, "samples":[]}
    if path is None or not path.exists():
        return result
    _path(path, existing=True)
    try:
        connection = sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=3)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("BEGIN")
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise OrbitHistoryError("invalid_orbit_history")
            meta = connection.execute("SELECT revision,norad FROM meta WHERE id=1").fetchone()
            if meta is None:
                return result
            norad = meta[1] if norad_id is None else norad_id
            first = max(start_utc_s, now_s-WINDOW_S)*1000
            last = min(end_utc_s, now_s)*1000
            # End brackets are FDS predictions, not future measured telemetry.
            rows = connection.execute("SELECT utc_ms,x,y,z,sx,sy,sz,source,epoch_ms FROM samples "
                "WHERE norad=? AND utc_ms>=? AND utc_ms<=? ORDER BY utc_ms LIMIT ?",
                (norad,first-120000,last+120000,MAX_ROWS+1)).fetchall()
            if len(rows)>MAX_ROWS:
                raise OrbitHistoryError("orbit_history_too_large")
            result.update(state="AVAILABLE" if rows else "WAITING",revision=meta[0],norad_id=norad)
            for row in rows:
                instant,x,y,z,sx,sy,sz,source,epoch = row
                if (type(instant) is not int or type(epoch) is not int or source not in ("TLE","GNSS") or
                        not 0 <= instant-epoch < WINDOW_S*1000 or
                        any(not _number(v) for v in row[1:7]) or
                        not 6.3e6 <= math.hypot(x,y,z) <= 8e6 or not .9 <= math.hypot(sx,sy,sz) <= 1.1):
                    raise OrbitHistoryError("invalid_orbit_history")
                result["samples"].append([_utc(instant),x,y,z,sx,sy,sz,source,_utc(epoch)])
        finally:
            connection.close()
        if len(json.dumps(result,separators=(",",":")).encode())>MAX_RESPONSE_BYTES:
            raise OrbitHistoryError("orbit_history_too_large")
        return result
    except (sqlite3.Error, OSError, TypeError, ValueError) as error:
        if isinstance(error, OrbitHistoryError):
            raise
        raise OrbitHistoryError("orbit_history_unavailable") from error
