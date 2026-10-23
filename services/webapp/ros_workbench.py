"""Persistent, non-operational ROS drafts and simulator-only reservations.

The proposed packet encoder is used for field validation only. This module
does not send TC or replace the independent C approval verifier.
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

from tools.pado_packet_codec import PacketError, encode_tc, load_catalog


SIM_HANDOFF_KEYS = {"schema", "source", "non_operational", "pass_id", "station_id",
                    "band", "schedule_sha256", "aos_utc_s", "los_utc_s", "revision",
                    "steps"}
SIM_READ_ONLY_COMMANDS = frozenset({"mm_get_mode", "hk_get", "eps_get_hk",
                                    "aocs_get_hk", "cs_get_hk", "siren_get_hk",
                                    "crest_get_hk"})
SIM_FINAL_STATUSES = frozenset({"ACKED", "REJECTED", "TIMEOUT", "SEND_FAILED"})
SIM_SEND_GRACE_S = 30


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


def normalize_sim_handoff(envelope: object) -> dict:
    """Validate a bounded MPS test draft without inventing an approval record."""
    if not isinstance(envelope, dict) or set(envelope) != SIM_HANDOFF_KEYS or \
            envelope["schema"] != "pado.sim-sequence.v1" or \
            envelope["source"] != "MPS_TEST_DRAFT" or \
            envelope["non_operational"] is not True or \
            envelope["station_id"] != "yonsei" or envelope["band"] != "UHF" or \
            not isinstance(envelope["pass_id"], str) or \
            not re.fullmatch(r"yonsei-[A-Za-z0-9._-]{1,56}", envelope["pass_id"]) or \
            not isinstance(envelope["schedule_sha256"], str) or \
            not re.fullmatch(r"[0-9a-f]{64}", envelope["schedule_sha256"]) or \
            not _integer(envelope["revision"], 1, 0xffffffff) or \
            not _integer(envelope["aos_utc_s"], 1, 9007199254740991) or \
            not _integer(envelope["los_utc_s"], envelope["aos_utc_s"] + 1,
                         envelope["aos_utc_s"] + 86400) or \
            not isinstance(envelope["steps"], list) or \
            not 1 <= len(envelope["steps"]) <= 64:
        raise DraftError("invalid_sim_handoff")
    now_s = int(time.time())
    if envelope["los_utc_s"] <= now_s:
        raise DraftError("stale_sim_pass")
    try:
        canonical = json.dumps(envelope, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise DraftError("invalid_sim_handoff") from exc
    if len(canonical) > 32768:
        raise DraftError("invalid_sim_handoff_size")
    steps = []
    for number, step in enumerate(envelope["steps"], 1):
        if not isinstance(step, dict) or set(step) != {"offset_s", "command_name", "parameters"} or \
                not _integer(step["offset_s"], 0,
                             envelope["los_utc_s"] - envelope["aos_utc_s"] - 1) or \
                not isinstance(step["command_name"], str) or \
                step["command_name"] not in SIM_READ_ONLY_COMMANDS or \
                not isinstance(step["parameters"], dict):
            raise DraftError("invalid_sim_step")
        # The packet codec is the shared draft catalog's field/range authority.
        # Encoding is validation only; no PDU is queued or transmitted here.
        try:
            encode_tc(step["command_name"], number, envelope["aos_utc_s"] * 1000,
                      step["parameters"])
        except PacketError as exc:
            raise DraftError("invalid_sim_step") from exc
        steps.append({"step_id": number, "offset_s": step["offset_s"],
                      "command_name": step["command_name"],
                      "parameters": step["parameters"]})
    return {"sha256": hashlib.sha256(canonical).hexdigest(),
            "schema": "pado.sim-sequence.v1", "source": "SIM_MPS_HANDOFF",
            "non_operational": True, "pass_id": envelope["pass_id"],
            "station_id": "yonsei", "band": "UHF",
            "schedule_sha256": envelope["schedule_sha256"],
            "aos_utc_s": envelope["aos_utc_s"], "los_utc_s": envelope["los_utc_s"],
            "revision": envelope["revision"], "steps": steps}


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
        self.db.execute("CREATE TABLE IF NOT EXISTS sim_sequences ("
                        "sha256 TEXT PRIMARY KEY, pass_id TEXT NOT NULL, "
                        "schedule_sha256 TEXT NOT NULL, revision INTEGER NOT NULL, "
                        "payload TEXT NOT NULL, imported_by TEXT NOT NULL, "
                        "imported_at INTEGER NOT NULL, "
                        "UNIQUE(pass_id,schedule_sha256,revision))")
        self.db.execute("CREATE TABLE IF NOT EXISTS sim_reservations ("
                        "id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL REFERENCES sim_sequences(sha256), "
                        "step_id INTEGER NOT NULL, target_utc_s INTEGER NOT NULL, "
                        "created_by TEXT NOT NULL, created_at INTEGER NOT NULL, "
                        "status TEXT NOT NULL CHECK(status IN "
                        "('SCHEDULED','SENDING','ACKED','REJECTED','TIMEOUT','SEND_FAILED',"
                        "'MISSED','UNKNOWN','CANCELLED')), "
                        "ack TEXT, finished_at INTEGER)")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS sim_reservations_one_active "
                        "ON sim_reservations(sha256,step_id) WHERE status!='CANCELLED'")
        self.db.execute("CREATE INDEX IF NOT EXISTS sim_reservations_due "
                        "ON sim_reservations(status,target_utc_s)")
        # An interrupted send may have reached the satellite. Never retry it
        # automatically, because an ACK could have been lost on restart.
        self.db.execute("UPDATE sim_reservations SET status='UNKNOWN',finished_at=? "
                        "WHERE status='SENDING'", (int(time.time()),))
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

    def import_sim_handoff(self, envelope: object, actor: str) -> dict:
        if not isinstance(actor, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", actor):
            raise DraftError("invalid_actor")
        payload = normalize_sim_handoff(envelope)
        with self.lock, self.db:
            existing = self.db.execute(
                "SELECT sha256 FROM sim_sequences WHERE pass_id=? AND schedule_sha256=? "
                "AND revision=?", (payload["pass_id"], payload["schedule_sha256"],
                                  payload["revision"])).fetchone()
            if existing is not None and existing[0] != payload["sha256"]:
                raise DraftError("sim_revision_conflict")
            self.db.execute("INSERT OR IGNORE INTO sim_sequences VALUES (?,?,?,?,?,?,?)",
                            (payload["sha256"], payload["pass_id"],
                             payload["schedule_sha256"], payload["revision"],
                             json.dumps(payload, separators=(",", ":")), actor,
                             int(time.time())))
        return payload

    def list_sim_sequences(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT payload FROM sim_sequences "
                                   "ORDER BY imported_at DESC,rowid DESC LIMIT 32").fetchall()
        return [json.loads(row[0]) for row in rows]

    def reserve_sim(self, sha256: str, step_id: int, actor: str) -> dict:
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256) or \
                not _integer(step_id, 1, 64) or \
                not isinstance(actor, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", actor):
            raise DraftError("invalid_sim_reservation")
        with self.lock, self.db:
            row = self.db.execute("SELECT payload FROM sim_sequences WHERE sha256=?",
                                  (sha256,)).fetchone()
            if row is None:
                raise DraftError("unknown_sim_sequence")
            sequence = json.loads(row[0])
            step = next((item for item in sequence["steps"] if item["step_id"] == step_id), None)
            if step is None or sequence["station_id"] != "yonsei" or \
                    sequence["band"] != "UHF":
                raise DraftError("invalid_sim_reservation")
            target = sequence["aos_utc_s"] + step["offset_s"]
            now_s = int(time.time())
            if target <= now_s or target >= sequence["los_utc_s"]:
                raise DraftError("past_sim_reservation")
            try:
                cursor = self.db.execute(
                    "INSERT INTO sim_reservations "
                    "(sha256,step_id,target_utc_s,created_by,created_at,status) "
                    "VALUES (?,?,?,?,?,'SCHEDULED')",
                    (sha256, step_id, target, actor, now_s))
            except sqlite3.IntegrityError as exc:
                raise DraftError("sim_already_reserved") from exc
            return {"id": cursor.lastrowid, "sha256": sha256, "step_id": step_id,
                    "target_utc_s": target, "created_by": actor,
                    "status": "SCHEDULED", "command_name": step["command_name"],
                    "ack": None}

    def list_sim_reservations(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT r.id,r.sha256,r.step_id,r.target_utc_s,r.created_by,r.status,"
                "r.ack,s.payload FROM sim_reservations AS r JOIN sim_sequences AS s "
                "ON r.sha256=s.sha256 ORDER BY r.id DESC LIMIT 128").fetchall()
        result = []
        for ident, digest, step_id, target, actor, status, ack, payload in rows:
            sequence = json.loads(payload)
            step = next(item for item in sequence["steps"] if item["step_id"] == step_id)
            result.append({"id": ident, "sha256": digest, "step_id": step_id,
                           "target_utc_s": target, "created_by": actor,
                           "status": status, "command_name": step["command_name"],
                           "ack": json.loads(ack) if ack else None})
        return result

    def cancel_sim(self, reservation_id: int, actor: str) -> None:
        if not _integer(reservation_id, 1, 0x7fffffffffffffff) or \
                not isinstance(actor, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", actor):
            raise DraftError("invalid_sim_reservation")
        with self.lock, self.db:
            result = self.db.execute(
                "UPDATE sim_reservations SET status='CANCELLED',finished_at=? "
                "WHERE id=? AND status='SCHEDULED' AND created_by=?",
                (int(time.time()), reservation_id, actor))
            if result.rowcount != 1:
                raise DraftError("sim_not_cancellable")

    def claim_due(self, now_s: int) -> dict | None:
        """Atomically claim one due simulator command; never replay an uncertain send."""
        if not _integer(now_s, 1, 9007199254740991):
            raise DraftError("invalid_sim_clock")
        with self.lock, self.db:
            while True:
                row = self.db.execute(
                    "SELECT r.id,r.sha256,r.step_id,r.target_utc_s,s.payload "
                    "FROM sim_reservations AS r JOIN sim_sequences AS s ON r.sha256=s.sha256 "
                    "WHERE r.status='SCHEDULED' AND r.target_utc_s<=? "
                    "ORDER BY r.target_utc_s,r.id LIMIT 1", (now_s,)).fetchone()
                if row is None:
                    return None
                ident, digest, step_id, target, payload = row
                sequence = json.loads(payload)
                if now_s >= sequence["los_utc_s"] or now_s - target > SIM_SEND_GRACE_S:
                    self.db.execute("UPDATE sim_reservations SET status='MISSED',"
                                    "finished_at=? WHERE id=? AND status='SCHEDULED'",
                                    (now_s, ident))
                    continue
                step = next(item for item in sequence["steps"] if item["step_id"] == step_id)
                changed = self.db.execute(
                    "UPDATE sim_reservations SET status='SENDING' "
                    "WHERE id=? AND status='SCHEDULED'", (ident,)).rowcount
                if changed != 1:
                    continue
                return {"id": ident, "sha256": digest, "step_id": step_id,
                        "target_utc_s": target, "status": "SENDING",
                        "command_name": step["command_name"],
                        "parameters": step["parameters"],
                        "command_sequence": (ident - 1) % 65535 + 1,
                        "pass_id": sequence["pass_id"],
                        "aos_utc_s": sequence["aos_utc_s"],
                        "los_utc_s": sequence["los_utc_s"],
                        "schedule_sha256": sequence["schedule_sha256"],
                        "revision": sequence["revision"]}

    def finish_sim(self, reservation_id: int, status: str,
                   ack: dict | None = None) -> dict:
        if not _integer(reservation_id, 1, 0x7fffffffffffffff) or \
                status not in SIM_FINAL_STATUSES:
            raise DraftError("invalid_sim_result")
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT r.sha256,r.step_id,r.target_utc_s,r.created_by,r.status,s.payload "
                "FROM sim_reservations AS r JOIN sim_sequences AS s ON r.sha256=s.sha256 "
                "WHERE r.id=?", (reservation_id,)).fetchone()
            if row is None or row[4] != "SENDING":
                raise DraftError("sim_not_sending")
            digest, step_id, target, actor, _, payload = row
            sequence = json.loads(payload)
            step = next(item for item in sequence["steps"] if item["step_id"] == step_id)
            if status in ("ACKED", "REJECTED"):
                try:
                    expected_id = next(item["id"] for item in load_catalog()["commands"]
                                       if item["name"] == step["command_name"])
                except (PacketError, StopIteration) as exc:
                    raise DraftError("invalid_sim_ack") from exc
                if not isinstance(ack, dict) or set(ack) != {
                        "command_id", "command_sequence", "result", "reason",
                        "executed_at_utc_ms"} or \
                        not _integer(ack["command_id"], expected_id, expected_id) or \
                        not _integer(ack["command_sequence"],
                                     (reservation_id - 1) % 65535 + 1,
                                     (reservation_id - 1) % 65535 + 1) or \
                        not _integer(ack["result"], 0, 255) or \
                        not _integer(ack["reason"], 0, 65535) or \
                        not _integer(ack["executed_at_utc_ms"], 1, 0xffffffffffffffff) or \
                        (status == "ACKED") != (ack["result"] == 0):
                    raise DraftError("invalid_sim_ack")
            elif ack is not None:
                raise DraftError("invalid_sim_ack")
            self.db.execute("UPDATE sim_reservations SET status=?,ack=?,finished_at=? "
                            "WHERE id=? AND status='SENDING'",
                            (status, json.dumps(ack, separators=(",", ":")) if ack else None,
                             int(time.time()), reservation_id))
            return {"id": reservation_id, "sha256": digest, "step_id": step_id,
                    "target_utc_s": target, "created_by": actor, "status": status,
                    "command_name": step["command_name"], "ack": ack}
