"""Persistent, non-operational ROS sequence inspection and reservation drafts.

This module deliberately has no command encoder, timer, sender, or TC gate link.
The import check is for a UI draft, not the independent C approval verifier.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
import time
from pathlib import Path


class DraftError(ValueError):
    pass


def _pairs(items: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise DraftError("duplicate_field")
        result[key] = value
    return result


def _integer(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def parse_test_pair(sequence_text: str, manifest_text: str) -> dict:
    if not isinstance(sequence_text, str) or not isinstance(manifest_text, str) or \
            len(sequence_text.encode("utf-8")) > 32768 or \
            len(manifest_text.encode("utf-8")) > 4096:
        raise DraftError("invalid_file_size")
    try:
        sequence = json.loads(sequence_text, object_pairs_hook=_pairs)
        manifest = json.loads(manifest_text, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError) as exc:
        raise DraftError("invalid_json") from exc
    if not isinstance(sequence, dict) or not isinstance(manifest, dict) or \
            set(manifest) != {"file", "type", "from", "to", "version", "created", "sha256"} or \
            manifest["type"] != "TEST_ONLY_SEQUENCE" or manifest["from"] != "mps" or \
            manifest["to"] != "ros" or not isinstance(manifest["file"], str) or \
            not re.fullmatch(r"TEST_ONLY_[A-Za-z0-9._-]{1,180}", manifest["file"]) or \
            not _integer(manifest["version"], 1, 0xffffffff) or \
            not isinstance(manifest["created"], str) or \
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", manifest["created"]) or \
            not isinstance(manifest["sha256"], str) or \
            not re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"]):
        raise DraftError("invalid_manifest")
    digest = hashlib.sha256(sequence_text.encode("utf-8")).hexdigest()
    if digest != manifest["sha256"]:
        raise DraftError("digest_mismatch")
    expected = {"type", "non_operational", "revision", "command_db_version", "pass_id",
                "aos_utc_s", "los_utc_s", "guard_before_s", "guard_after_s",
                "initial_state", "transfer_rate_bps", "max_commands", "edited_by",
                "approved_by", "approved_at_utc_s", "steps"}
    if set(sequence) != expected or sequence["type"] != "TEST_ONLY_SEQUENCE" or \
            sequence["non_operational"] is not True or \
            sequence["revision"] != manifest["version"] or \
            not _integer(sequence["revision"], 1, 0xffffffff) or \
            not _integer(sequence["command_db_version"], 1, 0xffffffff) or \
            not isinstance(sequence["pass_id"], str) or \
            not re.fullmatch(r"[A-Za-z0-9._-]{1,63}", sequence["pass_id"]) or \
            not _integer(sequence["aos_utc_s"], 1, 9007199254740991) or \
            not _integer(sequence["los_utc_s"], sequence["aos_utc_s"] + 1,
                         sequence["aos_utc_s"] + 86400) or \
            not _integer(sequence["guard_before_s"], 0, 3600) or \
            not _integer(sequence["guard_after_s"], 0, 3600) or \
            not isinstance(sequence["steps"], list) or \
            not 1 <= len(sequence["steps"]) <= 64:
        raise DraftError("invalid_sequence")
    for key in ("initial_state", "transfer_rate_bps", "max_commands", "approved_at_utc_s"):
        if not _integer(sequence[key], 0, 0xffffffff):
            raise DraftError("invalid_sequence")
    for key in ("edited_by", "approved_by"):
        if not isinstance(sequence[key], str) or not re.fullmatch(r"[!-~]{1,63}", sequence[key]):
            raise DraftError("invalid_sequence")
    ids = set()
    last_offset = -1
    step_keys = {"step_id", "command_id", "offset_s", "duration_s", "resource_mask",
                 "require_set", "require_clear", "effect_set", "effect_clear", "uplink_bytes"}
    for step in sequence["steps"]:
        if not isinstance(step, dict) or set(step) != step_keys or \
                not _integer(step["step_id"], 1, 0xffffffff) or \
                step["step_id"] in ids or \
                not isinstance(step["command_id"], str) or \
                not re.fullmatch(r"[A-Za-z0-9._-]{1,63}", step["command_id"]) or \
                not _integer(step["offset_s"], 0,
                             sequence["los_utc_s"] - sequence["aos_utc_s"] - 1) or \
                step["offset_s"] < last_offset:
            raise DraftError("invalid_step")
        for key in step_keys - {"step_id", "command_id", "offset_s"}:
            if not _integer(step[key], 0, 0xffffffff):
                raise DraftError("invalid_step")
        if step["duration_s"] == 0 or step["offset_s"] < sequence["guard_before_s"] or \
                sequence["aos_utc_s"] + step["offset_s"] + step["duration_s"] > \
                sequence["los_utc_s"] - sequence["guard_after_s"]:
            raise DraftError("step_outside_pass")
        ids.add(step["step_id"])
        last_offset = step["offset_s"]
    return {"sha256": digest, "file": manifest["file"], "revision": sequence["revision"],
            "pass_id": sequence["pass_id"], "aos_utc_s": sequence["aos_utc_s"],
            "los_utc_s": sequence["los_utc_s"],
            "guard_before_s": sequence["guard_before_s"],
            "guard_after_s": sequence["guard_after_s"], "steps": [
                {"step_id": step["step_id"], "command_id": step["command_id"],
                 "offset_s": step["offset_s"], "duration_s": step["duration_s"]}
                for step in sequence["steps"]]}


class RosDraftStore:
    def __init__(self, path: Path) -> None:
        if path.is_symlink() or (path.exists() and os.name == "posix" and
                                 stat.S_IMODE(path.stat().st_mode) & 0o077):
            raise DraftError("draft_database_not_private")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        if os.name == "posix":
            os.chmod(path, 0o600)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("CREATE TABLE IF NOT EXISTS sequences ("
                        "sha256 TEXT PRIMARY KEY, payload TEXT NOT NULL, imported_by TEXT NOT NULL, "
                        "imported_at INTEGER NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS reservations ("
                        "id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL REFERENCES sequences(sha256), "
                        "step_id INTEGER NOT NULL, target_utc_s INTEGER NOT NULL, "
                        "created_by TEXT NOT NULL, created_at INTEGER NOT NULL, "
                        "status TEXT NOT NULL CHECK(status IN ('DRAFT_ONLY','CANCELLED')))")
        self.db.commit()
        self.lock = threading.Lock()

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def import_pair(self, sequence_text: str, manifest_text: str, actor: str) -> dict:
        payload = parse_test_pair(sequence_text, manifest_text)
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO sequences VALUES (?,?,?,?)",
                            (payload["sha256"], json.dumps(payload, separators=(",", ":")),
                             actor, int(time.time())))
        return payload

    def list_sequences(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT payload FROM sequences ORDER BY imported_at DESC LIMIT 32").fetchall()
        return [json.loads(row[0]) for row in rows]

    def reserve(self, sha256: str, step_id: int, target_utc_s: int, actor: str) -> dict:
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256) or \
                not _integer(step_id, 1, 0xffffffff) or \
                not _integer(target_utc_s, 1, 9007199254740991):
            raise DraftError("invalid_reservation")
        with self.lock, self.db:
            row = self.db.execute("SELECT payload FROM sequences WHERE sha256=?", (sha256,)).fetchone()
            if row is None:
                raise DraftError("unknown_sequence")
            sequence = json.loads(row[0])
            step = next((item for item in sequence["steps"] if item["step_id"] == step_id), None)
            if step is None or target_utc_s < sequence["aos_utc_s"] + sequence["guard_before_s"] or \
                    target_utc_s + step["duration_s"] > \
                    sequence["los_utc_s"] - sequence["guard_after_s"]:
                raise DraftError("outside_pass")
            if target_utc_s < int(time.time()):
                raise DraftError("past_reservation")
            existing = self.db.execute(
                "SELECT id FROM reservations WHERE sha256=? AND step_id=? AND status='DRAFT_ONLY'",
                (sha256, step_id)).fetchone()
            if existing:
                raise DraftError("already_reserved")
            cursor = self.db.execute("INSERT INTO reservations "
                                     "(sha256,step_id,target_utc_s,created_by,created_at,status) "
                                     "VALUES (?,?,?,?,?,'DRAFT_ONLY')",
                                     (sha256, step_id, target_utc_s, actor, int(time.time())))
            return {"id": cursor.lastrowid, "sha256": sha256, "step_id": step_id,
                    "target_utc_s": target_utc_s, "status": "DRAFT_ONLY"}

    def reservations(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT id,sha256,step_id,target_utc_s,created_by,status "
                                   "FROM reservations ORDER BY id DESC LIMIT 128").fetchall()
        return [dict(zip(("id", "sha256", "step_id", "target_utc_s", "created_by", "status"), row))
                for row in rows]

    def cancel(self, reservation_id: int, actor: str) -> None:
        if not _integer(reservation_id, 1, 0x7fffffffffffffff):
            raise DraftError("invalid_reservation")
        with self.lock, self.db:
            result = self.db.execute("UPDATE reservations SET status='CANCELLED' "
                                     "WHERE id=? AND status='DRAFT_ONLY' AND created_by=?",
                                     (reservation_id, actor))
            if result.rowcount != 1:
                raise DraftError("not_cancellable")
