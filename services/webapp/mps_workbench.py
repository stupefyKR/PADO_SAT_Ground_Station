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
PASS_BOUNDARY_DRIFT_S = 300


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
        self.db.execute("CREATE INDEX IF NOT EXISTS candidate_drafts_aos "
                        "ON candidate_drafts(aos_utc_s)")
        self.db.execute("CREATE INDEX IF NOT EXISTS sequence_drafts_aos "
                        "ON sequence_drafts(aos_utc_s)")
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
        if not pass_id.startswith(match["station_id"] + "-"):
            raise CandidateError("unknown_pass")
        if (match.get("station_id"), match.get("band")) != ("yonsei", "UHF"):
            raise CandidateError("tc_not_available_for_pass")
        return match

    def _draft_row(self, table: str, payload: str, schedule: dict,
                   selected: dict) -> tuple | None:
        """Find one prior draft for the same physical pass across FDS refreshes.

        FDS pass IDs are ordinal within each rolling 72 h window, so neither the
        ID nor the schedule hash identifies a pass across runs. Only a nearby,
        overlapping pass at the same site may inherit a test draft.
        """
        digest = schedule["schedule_sha256"]
        pass_id = selected["pass_id"]
        aos, los = selected["aos_utc_s"], selected["los_utc_s"]
        prefix = selected["station_id"] + "-"
        if not pass_id.startswith(prefix):
            return None
        fields = f"pass_id,schedule_sha256,aos_utc_s,los_utc_s,revision,{payload},updated_by"
        exact = self.db.execute(
            f"SELECT {fields} FROM {table} WHERE pass_id=? AND schedule_sha256=?",
            (pass_id, digest)).fetchone()
        if exact is not None:
            if exact[2:4] != (aos, los):
                raise CandidateError("revision_conflict")
            return exact
        # A clipped boundary moves with the prediction window and cannot serve
        # as a stable identity for a future contact.
        if selected.get("clipped_start") or selected.get("clipped_end"):
            return None
        nearby = self.db.execute(
            f"SELECT {fields} FROM {table} WHERE aos_utc_s BETWEEN ? AND ? "
            "AND los_utc_s BETWEEN ? AND ?",
            (aos - PASS_BOUNDARY_DRIFT_S, aos + PASS_BOUNDARY_DRIFT_S,
             los - PASS_BOUNDARY_DRIFT_S, los + PASS_BOUNDARY_DRIFT_S)).fetchall()
        matches = [row for row in nearby
                   if row[0].startswith(prefix) and
                   min(row[3], los) - max(row[2], aos) >=
                   (min(row[3] - row[2], los - aos) + 1) // 2]
        # If two previously saved drafts could apply, avoid silently choosing
        # either command set. The rows remain intact for review.
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _current_passes(schedule: dict) -> list[dict]:
        return [item for item in schedule["passes"]
                if (item.get("station_id"), item.get("band")) in
                (("yonsei", "UHF"), ("kari", "S_BAND"))]

    def list_current(self, schedule: dict) -> list[dict]:
        if schedule.get("schedule_state") != "validated":
            return []
        digest = schedule["schedule_sha256"]
        result = []
        with self.lock:
            for item in self._current_passes(schedule):
                try:
                    row = self._draft_row("candidate_drafts", "candidate_ids", schedule, item)
                except CandidateError:
                    continue
                if row is None:
                    continue
                _, _, _, _, revision, ids, actor = row
                result.append({"pass_id": item["pass_id"], "schedule_sha256": digest,
                               "aos_utc_s": item["aos_utc_s"],
                               "los_utc_s": item["los_utc_s"],
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
            row = self._draft_row("candidate_drafts", "candidate_ids", schedule, selected)
            if row is None:
                if expected_revision != 0:
                    raise CandidateError("revision_conflict")
                revision = 1
                self.db.execute(
                    "INSERT INTO candidate_drafts VALUES (?,?,?,?,?,?,?,?)",
                    (pass_id, digest, aos, los, revision,
                     json.dumps(candidate_ids, separators=(",", ":")), actor, int(time.time())))
            else:
                if row[4] != expected_revision:
                    raise CandidateError("revision_conflict")
                revision = expected_revision + 1
                self.db.execute(
                    "UPDATE candidate_drafts SET pass_id=?,schedule_sha256=?,aos_utc_s=?,"
                    "los_utc_s=?,revision=?,candidate_ids=?,updated_by=?,updated_at=? "
                    "WHERE pass_id=? AND schedule_sha256=?",
                    (pass_id, digest, aos, los, revision,
                     json.dumps(candidate_ids, separators=(",", ":")), actor,
                     int(time.time()), row[0], row[1]))
        return {"pass_id": pass_id, "schedule_sha256": digest,
                "aos_utc_s": aos, "los_utc_s": los,
                "revision": revision, "candidate_ids": candidate_ids,
                "updated_by": actor, "operational": False}

    def list_sequences(self, schedule: dict) -> list[dict]:
        if schedule.get("schedule_state") != "validated":
            return []
        digest = schedule["schedule_sha256"]
        result = []
        with self.lock:
            for item in self._current_passes(schedule):
                try:
                    row = self._draft_row("sequence_drafts", "steps", schedule, item)
                except CandidateError:
                    continue
                if row is None:
                    continue
                _, _, old_aos, old_los, revision, steps_json, actor = row
                try:
                    steps = json.loads(steps_json)
                    self._validated_steps(steps, old_los - old_aos)
                except (ValueError, TypeError):
                    continue
                # Keep the work visible if a refreshed pass became shorter,
                # but require the planner to adjust out-of-bounds offsets
                # before any further save.
                needs_review = any(step["offset_s"] >=
                                   item["los_utc_s"] - item["aos_utc_s"]
                                   for step in steps)
                result.append({"pass_id": item["pass_id"],
                               "schedule_sha256": digest,
                               "aos_utc_s": item["aos_utc_s"],
                               "los_utc_s": item["los_utc_s"],
                               "revision": revision, "steps": steps,
                               "updated_by": actor, "needs_review": needs_review,
                               "operational": False})
        return result

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
            row = self._draft_row("sequence_drafts", "steps", schedule, selected)
            if row is None:
                if expected_revision != 0:
                    raise CandidateError("revision_conflict")
                revision = 1
                self.db.execute("INSERT INTO sequence_drafts VALUES (?,?,?,?,?,?,?,?)",
                                (pass_id, digest, aos, los, revision,
                                 json.dumps(clean, separators=(",", ":")), actor, int(time.time())))
            else:
                if row[4] != expected_revision:
                    raise CandidateError("revision_conflict")
                revision = expected_revision + 1
                self.db.execute(
                    "UPDATE sequence_drafts SET pass_id=?,schedule_sha256=?,aos_utc_s=?,"
                    "los_utc_s=?,revision=?,steps=?,updated_by=?,updated_at=? "
                    "WHERE pass_id=? AND schedule_sha256=?",
                    (pass_id, digest, aos, los, revision,
                     json.dumps(clean, separators=(",", ":")), actor,
                     int(time.time()), row[0], row[1]))
        return {"pass_id": pass_id, "schedule_sha256": digest,
                "aos_utc_s": aos, "los_utc_s": los, "revision": revision,
                "steps": clean, "updated_by": actor, "operational": False}
