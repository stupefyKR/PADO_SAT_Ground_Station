"""Durable TEST_ONLY received application PDUs, with bounded read-only queries.

This archive keeps the exact bytes before the virtual link sends a delivery
receipt. It does not expire old packets silently. A full archive stops ingress
instead, leaving unacknowledged TM on the virtual spacecraft.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from tools.pado_packet_codec import decode, join_full_hk


ARCHIVE_FILENAME = "received_tm.sqlite3"
ARCHIVE_VERSION = 1
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
RECEIPT_MAGIC = b"PSR1"
RECEIPT_BYTES = 36
_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_HEADER = struct.Struct(">2sBBHHQHBB")


class ArchiveError(ValueError):
    pass


def _utc(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def _json_pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate JSON field")
        result[name] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-finite JSON value")


def _finite_record(value, depth=0):
    if depth > 16:
        raise ValueError("full HK nesting is too deep")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite full HK parameter")
    if isinstance(value, dict):
        for item in value.values():
            _finite_record(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _finite_record(item, depth + 1)


def json_fields(value):
    """Preserve byte fields explicitly instead of guessing a text encoding."""
    if isinstance(value, bytes):
        return {"encoding": "hex", "value": value.hex()}
    if isinstance(value, dict):
        return {key: json_fields(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_fields(item) for item in value]
    return value


def delivery_receipt(packet: bytes) -> bytes:
    if not isinstance(packet, bytes) or not 24 <= len(packet) <= 200:
        raise ArchiveError("invalid_packet")
    return RECEIPT_MAGIC + hashlib.sha256(packet).digest()


def _path(path: Path, *, existing: bool) -> None:
    if (not isinstance(path, Path) or path.name != ARCHIVE_FILENAME or
            path.is_symlink() or path.parent.is_symlink() or not path.parent.is_dir() or
            (existing and not path.is_file()) or (path.exists() and not path.is_file())):
        raise ArchiveError("archive_unavailable")
    if path.exists() and not 1 <= path.stat().st_size <= MAX_ARCHIVE_BYTES:
        raise ArchiveError("archive_invalid")


def _connect(path: Path) -> sqlite3.Connection:
    _path(path, existing=True)
    connection = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                                     timeout=3)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        if connection.execute("PRAGMA user_version").fetchone()[0] != ARCHIVE_VERSION:
            connection.close()
            raise ArchiveError("archive_invalid")
        return connection
    except sqlite3.Error as error:
        if connection is not None:
            connection.close()
        raise ArchiveError("archive_unavailable") from error


class PacketArchive:
    """One locked SQLite writer, usable by the receiver's connection thread."""

    def __init__(self, path: Path, *, max_bytes: int = MAX_ARCHIVE_BYTES):
        _path(path, existing=False)
        if type(max_bytes) is not int or not 32768 <= max_bytes <= MAX_ARCHIVE_BYTES:
            raise ArchiveError("invalid_archive_limit")
        self.path = path
        self.max_bytes = max_bytes
        self.lock = Lock()
        self.connection = sqlite3.connect(path, timeout=5, check_same_thread=False)
        try:
            os.chmod(path, 0o600)
            self.connection.execute("PRAGMA journal_mode=DELETE")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA trusted_schema=OFF")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, ARCHIVE_VERSION):
                raise ArchiveError("archive_invalid")
            page_size = self.connection.execute("PRAGMA page_size").fetchone()[0]
            self.max_pages = max_bytes // page_size
            self.connection.execute(f"PRAGMA max_page_count={self.max_pages}")
            with self.connection:
                self.connection.execute("CREATE TABLE IF NOT EXISTS packets ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, sha256 TEXT NOT NULL UNIQUE, "
                    "name TEXT NOT NULL, packet_id INTEGER NOT NULL, sequence INTEGER NOT NULL, "
                    "sampled_ms INTEGER NOT NULL, received_ms INTEGER NOT NULL, "
                    "raw BLOB NOT NULL, fields_json TEXT NOT NULL, "
                    "UNIQUE(name,sampled_ms,sequence))")
                self.connection.execute("CREATE INDEX IF NOT EXISTS packets_name_id "
                                        "ON packets(name,id)")
                self.connection.execute("CREATE INDEX IF NOT EXISTS packets_measurement_time "
                                        "ON packets(name,sampled_ms,id)")
                self.connection.execute("PRAGMA user_version=1")
            self.connection.close()
            self.connection = None
        except BaseException as error:
            if self.connection is not None:
                self.connection.close()
            if isinstance(error, sqlite3.Error):
                raise ArchiveError("archive_invalid") from error
            raise

    def close(self) -> None:
        with self.lock:
            if self.connection is not None:
                self.connection.close()
                self.connection = None

    def begin(self) -> None:
        """Keep a writer open for one receiver stream; direct calls stay short-lived."""
        with self.lock:
            if self.connection is None:
                try:
                    _path(self.path, existing=True)
                    self.connection = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
                    self.connection.execute("PRAGMA synchronous=FULL")
                    self.connection.execute("PRAGMA trusted_schema=OFF")
                    self.connection.execute(f"PRAGMA max_page_count={self.max_pages}")
                except sqlite3.Error as error:
                    if self.connection is not None:
                        self.connection.close()
                        self.connection = None
                    raise ArchiveError("archive_unavailable") from error

    def append(self, packet: bytes, decoded: dict, received_ms: int) -> dict:
        if (not isinstance(packet, bytes) or not 24 <= len(packet) <= 200 or
                not isinstance(decoded, dict) or decoded.get("kind") != "TM" or
                decoded.get("operational") is not False or
                type(received_ms) is not int or received_ms < decoded["utc_ms"]):
            raise ArchiveError("invalid_packet")
        digest = hashlib.sha256(packet).hexdigest()
        fields = json.dumps(json_fields(decoded["fields"]), separators=(",", ":"),
                            allow_nan=False)
        with self.lock:
            temporary = self.connection is None
            if temporary:
                _path(self.path, existing=True)
            connection = self.connection or sqlite3.connect(self.path, timeout=5)
            try:
                if temporary:
                    connection.execute(f"PRAGMA max_page_count={self.max_pages}")
                old = connection.execute("SELECT id FROM packets WHERE sha256=?",
                                              (digest,)).fetchone()
                if old is not None:
                    return {"id": old[0], "label": f"TM{old[0]:04d}", "duplicate": True}
                with connection:
                    cursor = connection.execute(
                        "INSERT INTO packets(sha256,name,packet_id,sequence,sampled_ms,"
                        "received_ms,raw,fields_json) VALUES (?,?,?,?,?,?,?,?)",
                        (digest, decoded["name"], decoded["id"], decoded["sequence"],
                         decoded["utc_ms"], received_ms, packet, fields))
                return {"id": cursor.lastrowid, "label": f"TM{cursor.lastrowid:04d}",
                        "duplicate": False}
            except sqlite3.IntegrityError as error:
                raise ArchiveError("conflicting_packet") from error
            except sqlite3.Error as error:
                if "full" in str(error).lower():
                    raise ArchiveError("archive_full") from error
                raise ArchiveError("archive_unavailable") from error
            finally:
                if temporary:
                    connection.close()


def _metadata(row) -> dict:
    ident, name, packet_id, sequence, sampled_ms, received_ms, length = row[:7]
    if (type(ident) is not int or ident < 1 or not isinstance(name, str) or
            not _NAME.fullmatch(name) or type(length) is not int or not 24 <= length <= 200 or
            type(packet_id) is not int or not 0 <= packet_id <= 65535 or
            type(sequence) is not int or not 0 <= sequence <= 65535 or
            type(sampled_ms) is not int or type(received_ms) is not int or
            not 0 < sampled_ms <= received_ms):
        raise ArchiveError("archive_invalid")
    return {"id": ident, "label": f"TM{ident:04d}", "name": name,
            "packet_id": packet_id, "sequence": sequence,
            "sampled_utc": _utc(sampled_ms), "received_utc": _utc(received_ms),
            "size_bytes": length}


def list_packets(path: Path, *, limit: int = 50, before_id: int | None = None,
                 name: str | None = None) -> dict:
    if (type(limit) is not int or not 1 <= limit <= 100 or
            (before_id is not None and (type(before_id) is not int or
                                       not 1 <= before_id <= 0x7fffffffffffffff)) or
            (name is not None and (not isinstance(name, str) or not _NAME.fullmatch(name)))):
        raise ArchiveError("invalid_query")
    conditions, values = [], []
    if before_id is not None:
        conditions.append("id<?")
        values.append(before_id)
    if name is not None:
        conditions.append("name=?")
        values.append(name)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    connection = _connect(path)
    try:
        rows = connection.execute("SELECT id,name,packet_id,sequence,sampled_ms,received_ms,"
                                  "length(raw) FROM packets" + where +
                                  " ORDER BY id DESC LIMIT ?", (*values, limit + 1)).fetchall()
        count = connection.execute("SELECT COUNT(*) FROM packets" +
                                   (" WHERE name=?" if name else ""),
                                   (name,) if name else ()).fetchone()[0]
        items = [_metadata(row) for row in rows[:limit]]
        return {"items": items, "next_before_id": items[-1]["id"]
                if len(rows) > limit else None, "total": count, "operational": False}
    except (sqlite3.Error, OverflowError, ValueError) as error:
        raise ArchiveError("archive_invalid") from error
    finally:
        connection.close()


def packet_detail(path: Path, packet_id: int) -> dict:
    if type(packet_id) is not int or not 1 <= packet_id <= 0x7fffffffffffffff:
        raise ArchiveError("invalid_query")
    connection = _connect(path)
    try:
        row = connection.execute("SELECT id,name,packet_id,sequence,sampled_ms,received_ms,"
            "length(raw),raw,sha256,fields_json FROM packets WHERE id=?", (packet_id,)).fetchone()
        if row is None:
            raise ArchiveError("packet_not_found")
        result = _metadata(row)
        raw, digest, fields = bytes(row[7]), row[8], json.loads(row[9])
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ArchiveError("archive_invalid")
        # Independently decode stored bytes so corruption cannot become parsed data.
        parsed = decode(raw)
        if (json_fields(parsed["fields"]) != fields or parsed["kind"] != "TM" or
                parsed["name"] != row[1] or parsed["id"] != row[2] or
                parsed["sequence"] != row[3] or parsed["utc_ms"] != row[4]):
            raise ArchiveError("archive_invalid")
        magic, version, kind, ident, sequence, utc_ms, length, flags, reserved = _HEADER.unpack_from(raw)
        result.update({"raw_hex": raw.hex(), "sha256": digest, "fields": fields,
            "header": {"magic": magic.decode("ascii"), "version": version,
                "kind": "TM" if kind == 2 else "TC", "packet_id": ident,
                "sequence": sequence, "utc_ms": utc_ms, "payload_bytes": length,
                "flags": flags, "reserved": reserved,
                "crc32": raw[-4:].hex()}, "operational": False})
        if parsed["name"] == "FULL_HK_CHUNK":
            chunks = connection.execute("SELECT raw FROM packets WHERE name=? AND sampled_ms=? "
                                        "AND json_extract(fields_json,'$.record_id')=? "
                                        "ORDER BY id LIMIT 512", ("FULL_HK_CHUNK",
                                        parsed["utc_ms"], fields["record_id"])).fetchall()
            matching = []
            for (chunk_raw,) in chunks:
                chunk = decode(bytes(chunk_raw))
                if chunk["fields"]["record_id"] == parsed["fields"]["record_id"]:
                    matching.append(bytes(chunk_raw))
            summary = {"state": "receiving", "record_id": fields["record_id"],
                       "received_chunks": len(matching), "chunk_count": fields["chunk_count"],
                       "total_bytes": fields["total_len"]}
            if len(matching) == fields["chunk_count"]:
                record = join_full_hk(matching)
                summary["state"] = "complete"
                summary["record_sha256"] = hashlib.sha256(record).hexdigest()
                try:
                    document = json.loads(record.decode("utf-8"),
                                          parse_constant=_reject_constant,
                                          object_pairs_hook=_json_pairs)
                    _finite_record(document)
                    if isinstance(document, dict):
                        summary["parameters"] = document
                except (UnicodeError, ValueError, RecursionError):
                    summary["encoding"] = "binary"
            result["full_hk"] = summary
        return result
    except (sqlite3.Error, UnicodeError, ValueError, struct.error) as error:
        if isinstance(error, ArchiveError):
            raise
        raise ArchiveError("archive_invalid") from error
    finally:
        connection.close()


def archive_stats(path: Path) -> dict:
    connection = _connect(path)
    try:
        count, size, last = connection.execute(
            "SELECT COUNT(*),COALESCE(SUM(length(raw)),0),MAX(received_ms) FROM packets").fetchone()
        families = [{"name": name, "count": amount} for name, amount in connection.execute(
            "SELECT name,COUNT(*) FROM packets GROUP BY name ORDER BY name")]
        return {"total": count, "raw_bytes": size, "database_bytes": path.stat().st_size,
                "last_received_utc": _utc(last) if last is not None else None,
                "families": families, "operational": False}
    except (sqlite3.Error, OSError, ValueError) as error:
        raise ArchiveError("archive_invalid") from error
    finally:
        connection.close()


def list_events(path: Path, *, limit: int = 30) -> dict:
    """Select nonzero FDIR events before applying the result window."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ArchiveError("invalid_query")
    connection = _connect(path)
    try:
        identities = [row[0] for row in connection.execute(
            "SELECT id FROM packets WHERE name='FDIR_EVENT' AND ("
            "COALESCE(json_extract(fields_json,'$.level'),0)>0 OR "
            "COALESCE(json_extract(fields_json,'$.fault_code'),0)>0) "
            "ORDER BY id DESC LIMIT ?", (limit,))]
    except sqlite3.Error as error:
        raise ArchiveError("archive_invalid") from error
    finally:
        connection.close()
    return {"items": [packet_detail(path, ident) for ident in identities],
            "operational": False}
