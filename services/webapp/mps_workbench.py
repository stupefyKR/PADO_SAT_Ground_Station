"""Persistent test-only MPS candidate labels bound to a validated N13 schedule."""

from __future__ import annotations

import json
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
