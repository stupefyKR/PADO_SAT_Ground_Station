"""Persistent test-only MPS candidate labels bound to a validated N13 schedule."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import stat
import threading
import time
from pathlib import Path


class CandidateError(ValueError):
    pass


CATALOG_PATH = Path(__file__).resolve().parents[2] / "interfaces/pado_packets/draft_v0.json"


def draft_commands() -> list[dict]:
    """Read only the separately marked draft; never populate the flight DB."""
    value = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if (value.get("schema") != "pado.packet-catalog.v0" or
            value.get("operational") is not False or
            value.get("status") != "DRAFT_NOT_FLIGHT_APPROVED"):
        raise CandidateError("draft_catalog_invalid")
    entries = value.get("commands")
    if not isinstance(entries, list) or len(entries) > 128:
        raise CandidateError("draft_catalog_invalid")
    result = []
    seen = set()
    for entry in entries:
        if (not isinstance(entry, dict) or type(entry.get("id")) is not int or
                not 1 <= entry["id"] <= 65535 or
                not isinstance(entry.get("name"), str) or
                not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", entry["name"]) or
                entry["name"] in seen or
                entry.get("risk") not in ("low", "medium", "high", "critical") or
                not isinstance(entry.get("group"), str) or
                not re.fullmatch(r"[A-Z0-9]{2,12}", entry["group"]) or
                (entry.get("fields") is not None and not isinstance(entry["fields"], dict))):
            raise CandidateError("draft_catalog_invalid")
        seen.add(entry["name"])
        result.append({"id": entry["id"], "name": entry["name"],
                       "group": entry["group"], "risk": entry["risk"],
                       "parameters": list(entry["fields"] or {}),
                       "field_types": entry["fields"],
                       "fields_defined": entry["fields"] is not None,
                       "operational": False})
    return result


class MpsCandidateStore:
    def __init__(self, path: Path) -> None:
        if path.is_symlink() or (path.exists() and os.name == "posix" and
                                 stat.S_IMODE(path.stat().st_mode) & 0o077):
            raise CandidateError("candidate_database_not_private")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        if os.name == "posix":
            os.chmod(path, 0o600)
        self.db.execute("CREATE TABLE IF NOT EXISTS candidate_drafts ("
                        "pass_id TEXT NOT NULL, schedule_sha256 TEXT NOT NULL, "
                        "aos_utc_s INTEGER NOT NULL, los_utc_s INTEGER NOT NULL, "
                        "revision INTEGER NOT NULL, candidate_ids TEXT NOT NULL, "
                        "updated_by TEXT NOT NULL, updated_at INTEGER NOT NULL, "
                        "PRIMARY KEY(pass_id, schedule_sha256))")
        self.db.execute("CREATE TABLE IF NOT EXISTS sequence_drafts ("
                        "pass_id TEXT NOT NULL, schedule_sha256 TEXT NOT NULL, "
                        "aos_utc_s INTEGER NOT NULL, los_utc_s INTEGER NOT NULL, "
                        "revision INTEGER NOT NULL, steps TEXT NOT NULL, "
                        "updated_by TEXT NOT NULL, updated_at INTEGER NOT NULL, "
                        "PRIMARY KEY(pass_id, schedule_sha256))")
        self.db.commit()
        self.lock = threading.Lock()

    def close(self) -> None:
        with self.lock:
            self.db.close()

    @staticmethod
    def _pass(schedule: dict, pass_id: str, digest: str) -> dict:
        if (schedule.get("schedule_state") != "validated" or
                not isinstance(digest, str) or
                digest != schedule.get("schedule_sha256") or
                not isinstance(pass_id, str)):
            raise CandidateError("stale_schedule")
        match = next((item for item in schedule["passes"] if item["pass_id"] == pass_id), None)
        if match is None:
            raise CandidateError("unknown_pass")
        if (match.get("station_id"), match.get("band")) != ("yonsei", "UHF"):
            raise CandidateError("tc_not_available_for_pass")
        return match

    def list_current(self, schedule: dict) -> list[dict]:
        if schedule.get("schedule_state") != "validated":
            return []
        digest = schedule["schedule_sha256"]
        with self.lock:
            rows = self.db.execute(
                "SELECT pass_id,aos_utc_s,los_utc_s,revision,candidate_ids,updated_by "
                "FROM candidate_drafts WHERE schedule_sha256=? ORDER BY pass_id LIMIT 64",
                (digest,)).fetchall()
        current = {item["pass_id"]: item for item in schedule["passes"]}
        result = []
        for pass_id, aos, los, revision, ids, actor in rows:
            item = current.get(pass_id)
            if (item and (item.get("station_id"), item.get("band")) ==
                    ("yonsei", "UHF") and
                    (aos, los) == (item["aos_utc_s"], item["los_utc_s"])):
                result.append({"pass_id": pass_id, "schedule_sha256": digest,
                               "aos_utc_s": aos, "los_utc_s": los,
                               "revision": revision, "candidate_ids": json.loads(ids),
                               "updated_by": actor, "operational": False})
        return result

    def save(self, schedule: dict, pass_id: str, digest: str,
             expected_revision: int, candidate_ids: list[str], actor: str) -> dict:
        selected = self._pass(schedule, pass_id, digest)
        allowed = {entry["name"].upper() for entry in draft_commands()
                   if entry["fields_defined"]}
        if (type(expected_revision) is not int or not 0 <= expected_revision < 0xffffffff or
                not isinstance(candidate_ids, list) or len(candidate_ids) > 64 or
                any(not isinstance(value, str) or
                    value not in allowed
                    for value in candidate_ids) or len(set(candidate_ids)) != len(candidate_ids) or
                not isinstance(actor, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", actor)):
            raise CandidateError("invalid_candidate_draft")
        aos, los = selected["aos_utc_s"], selected["los_utc_s"]
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT aos_utc_s,los_utc_s,revision FROM candidate_drafts "
                "WHERE pass_id=? AND schedule_sha256=?", (pass_id, digest)).fetchone()
            if row is None:
                if expected_revision != 0:
                    raise CandidateError("revision_conflict")
                revision = 1
                self.db.execute(
                    "INSERT INTO candidate_drafts VALUES (?,?,?,?,?,?,?,?)",
                    (pass_id, digest, aos, los, revision,
                     json.dumps(candidate_ids, separators=(",", ":")), actor, int(time.time())))
            else:
                if row != (aos, los, expected_revision):
                    raise CandidateError("revision_conflict")
                revision = expected_revision + 1
                self.db.execute(
                    "UPDATE candidate_drafts SET revision=?,candidate_ids=?,updated_by=?,updated_at=? "
                    "WHERE pass_id=? AND schedule_sha256=?",
                    (revision, json.dumps(candidate_ids, separators=(",", ":")), actor,
                     int(time.time()), pass_id, digest))
        return {"pass_id": pass_id, "schedule_sha256": digest,
                "aos_utc_s": aos, "los_utc_s": los,
                "revision": revision, "candidate_ids": candidate_ids,
                "updated_by": actor, "operational": False}

    def list_sequences(self, schedule: dict) -> list[dict]:
        if schedule.get("schedule_state") != "validated":
            return []
        digest = schedule["schedule_sha256"]
        with self.lock:
            rows = self.db.execute(
                "SELECT pass_id,aos_utc_s,los_utc_s,revision,steps,updated_by "
                "FROM sequence_drafts WHERE schedule_sha256=? ORDER BY pass_id LIMIT 256",
                (digest,)).fetchall()
        current = {item["pass_id"]: item for item in schedule["passes"]}
        return [{"pass_id": pass_id, "schedule_sha256": digest,
                 "aos_utc_s": aos, "los_utc_s": los, "revision": revision,
                 "steps": json.loads(steps), "updated_by": actor, "operational": False}
                for pass_id, aos, los, revision, steps, actor in rows
                if pass_id in current and
                (current[pass_id].get("station_id"), current[pass_id].get("band")) ==
                ("yonsei", "UHF") and
                (aos, los) == (current[pass_id]["aos_utc_s"], current[pass_id]["los_utc_s"])]

    @staticmethod
    def _validated_steps(steps: object, duration: int) -> list[dict]:
        if not isinstance(steps, list) or len(steps) > 64:
            raise CandidateError("invalid_sequence")
        catalog = {item["name"]: item for item in draft_commands()
                   if item["fields_defined"]}
        clean = []
        for step in steps:
            if not isinstance(step, dict) or set(step) != {"offset_s", "command_name", "parameters"}:
                raise CandidateError("invalid_sequence")
            offset = step["offset_s"]
            name = step["command_name"]
            params = step["parameters"]
            if (type(offset) is not int or not 0 <= offset < duration or
                    not isinstance(name, str) or name not in catalog or
                    not isinstance(params, dict) or
                    set(params) != set(catalog[name]["field_types"])):
                raise CandidateError("invalid_sequence")
            for field, kind in catalog[name]["field_types"].items():
                value = params[field]
                if kind in ("u8", "u16", "u32", "u64"):
                    if type(value) is not int or not 0 <= value < 2 ** int(kind[1:]):
                        raise CandidateError("invalid_sequence")
                elif kind == "bool":
                    if type(value) is not bool:
                        raise CandidateError("invalid_sequence")
                elif kind in ("f32", "f64"):
                    if type(value) not in (int, float) or not math.isfinite(value):
                        raise CandidateError("invalid_sequence")
                elif kind.startswith("ascii") and kind[5:].isdigit():
                    if (not isinstance(value, str) or not value.isascii() or
                            len(value.encode("ascii")) > int(kind[5:])):
                        raise CandidateError("invalid_sequence")
                else:
                    raise CandidateError("unsupported_field_type")
            clean.append({"offset_s": offset, "command_name": name,
                          "parameters": params})
        return clean

    def save_sequence(self, schedule: dict, pass_id: str, digest: str,
                      expected_revision: int, steps: object, actor: str) -> dict:
        selected = self._pass(schedule, pass_id, digest)
        if (type(expected_revision) is not int or not 0 <= expected_revision < 0xffffffff or
                not isinstance(actor, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", actor)):
            raise CandidateError("invalid_sequence")
        aos, los = selected["aos_utc_s"], selected["los_utc_s"]
        clean = self._validated_steps(steps, los - aos)
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT aos_utc_s,los_utc_s,revision FROM sequence_drafts "
                "WHERE pass_id=? AND schedule_sha256=?", (pass_id, digest)).fetchone()
            if row is None:
                if expected_revision != 0:
                    raise CandidateError("revision_conflict")
                revision = 1
                self.db.execute("INSERT INTO sequence_drafts VALUES (?,?,?,?,?,?,?,?)",
                                (pass_id, digest, aos, los, revision,
                                 json.dumps(clean, separators=(",", ":")), actor, int(time.time())))
            else:
                if row != (aos, los, expected_revision):
                    raise CandidateError("revision_conflict")
                revision = expected_revision + 1
                self.db.execute(
                    "UPDATE sequence_drafts SET revision=?,steps=?,updated_by=?,updated_at=? "
                    "WHERE pass_id=? AND schedule_sha256=?",
                    (revision, json.dumps(clean, separators=(",", ":")), actor,
                     int(time.time()), pass_id, digest))
        return {"pass_id": pass_id, "schedule_sha256": digest,
                "aos_utc_s": aos, "los_utc_s": los, "revision": revision,
                "steps": clean, "updated_by": actor, "operational": False}
