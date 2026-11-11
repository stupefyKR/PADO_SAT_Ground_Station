"""Read-only, measurement-time equipment overview from validated received TM."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time

from services.sim_ground.archive import ArchiveError, _connect, packet_detail


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LAYOUT = ROOT / "config/equipment-layout.example.json"
MAX_LAYOUT_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 128 * 1024
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,47}")


class EquipmentError(ValueError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise EquipmentError("equipment_layout_invalid")
        result[key] = value
    return result


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def load_layout(settings_path: Path | None = None, layout_path: Path | None = None):
    source = layout_path
    if source is None and "PADO_EQUIPMENT_LAYOUT" in os.environ:
        source = Path(os.environ["PADO_EQUIPMENT_LAYOUT"])
    if source is None and settings_path is not None:
        candidate = settings_path.with_name("equipment-layout.json")
        if candidate.exists() or candidate.is_symlink():
            source = candidate
    source = source or DEFAULT_LAYOUT
    try:
        if source.is_symlink() or source.parent.is_symlink() or not source.is_file() or not 1 <= source.stat().st_size <= MAX_LAYOUT_BYTES:
            raise EquipmentError("equipment_layout_unavailable")
        raw = source.read_bytes()
        if len(raw) > MAX_LAYOUT_BYTES:
            raise EquipmentError("equipment_layout_invalid")
        layout = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(EquipmentError("equipment_layout_invalid")))
        if (not isinstance(layout, dict) or layout.get("schema") != "pado.equipment-layout.v1" or
                layout.get("frame") != "BODY" or layout.get("units") != "mm" or
                not isinstance(layout.get("source"), dict) or
                not isinstance(layout.get("equipment"), list) or not 1 <= len(layout["equipment"]) <= 32):
            raise EquipmentError("equipment_layout_invalid")
        seen = set()
        for item in layout["equipment"]:
            if (not isinstance(item, dict) or not isinstance(item.get("id"), str) or
                    not IDENTIFIER.fullmatch(item["id"]) or item["id"] in seen or
                    not isinstance(item.get("label"), str) or len(item["label"]) > 80 or
                    not isinstance(item.get("group"), str) or len(item["group"]) > 32 or
                    not isinstance(item.get("anchor_body_mm"), list) or len(item["anchor_body_mm"]) != 3 or
                    any(not _finite(v) or abs(v) > 2000 for v in item["anchor_body_mm"]) or
                    item.get("anchor_kind") not in ("BOARD_CENTER_PROXY", "COMPONENT_CENTER", "MEASURED_SENSOR") or
                    not isinstance(item.get("readings"), list) or len(item["readings"]) > 12):
                raise EquipmentError("equipment_layout_invalid")
            seen.add(item["id"])
            callout = item.get("callout", {})
            if (not isinstance(callout, dict) or callout.get("side", "left") not in ("left", "right") or
                    type(callout.get("order", 0)) is not int or not 0 <= callout.get("order", 0) <= 31):
                raise EquipmentError("equipment_layout_invalid")
            item["callout"] = {"side": callout.get("side", "left"), "order": callout.get("order", 0)}
            names = set()
            for reading in item["readings"]:
                if (not isinstance(reading, dict) or not isinstance(reading.get("id"), str) or
                        not IDENTIFIER.fullmatch(reading["id"]) or reading["id"] in names or
                        not isinstance(reading.get("label"), str) or len(reading["label"]) > 80 or
                        not isinstance(reading.get("unit"), str) or len(reading["unit"]) > 24 or
                        reading.get("kind", "numeric") not in ("numeric", "code", "counter", "boolean", "text") or
                        type(reading.get("decimals", 1)) is not int or not 0 <= reading.get("decimals", 1) <= 6):
                    raise EquipmentError("equipment_layout_invalid")
                names.add(reading["id"])
                for key, default in (("max_age_s", 120), ("full_hk_max_age_s", 1200)):
                    if type(reading.get(key, default)) is not int or not 1 <= reading.get(key, default) <= 86400:
                        raise EquipmentError("equipment_layout_invalid")
                selector = reading.get("tm")
                if selector is not None and (not isinstance(selector, dict) or set(selector) != {"packet", "field"} or
                        not isinstance(selector["packet"], str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", selector["packet"]) or
                        not isinstance(selector["field"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", selector["field"])):
                    raise EquipmentError("equipment_layout_invalid")
                path = reading.get("full_hk_path")
                if path is not None and (not isinstance(path, list) or not 1 <= len(path) <= 12 or any(
                        not ((isinstance(v, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", v)) or
                             (type(v) is int and 0 <= v <= 255)) for v in path)):
                    raise EquipmentError("equipment_layout_invalid")
        return layout, hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, RecursionError, OverflowError) as error:
        if isinstance(error, EquipmentError):
            raise
        raise EquipmentError("equipment_layout_invalid") from error


def _latest(archive: Path, names: set[str]):
    if not archive.exists() and not archive.is_symlink():
        return {}, None
    connection = _connect(archive)
    try:
        identities = {}
        for name in sorted(names):
            row = connection.execute("SELECT id FROM packets WHERE name=? ORDER BY sampled_ms DESC,id DESC LIMIT 1", (name,)).fetchone()
            if row:
                identities[name] = row[0]
        # Filter incomplete transfers before limiting candidates. Any number of
        # interrupted new dumps must not hide the last complete measurement.
        candidates = connection.execute("SELECT MAX(id) FROM packets WHERE name='FULL_HK_CHUNK' "
            "GROUP BY sampled_ms,json_extract(fields_json,'$.record_id') "
            "HAVING COUNT(*)=MAX(json_extract(fields_json,'$.chunk_count')) "
            "AND COUNT(DISTINCT json_extract(fields_json,'$.chunk_index'))=MAX(json_extract(fields_json,'$.chunk_count')) "
            "ORDER BY sampled_ms DESC,MAX(id) DESC LIMIT 8").fetchall()
    finally:
        connection.close()
    packets = {name: packet_detail(archive, ident) for name, ident in identities.items()}
    complete = None
    for (ident,) in candidates:
        packet = packet_detail(archive, ident)
        full = packet.get("full_hk", {})
        document = full.get("parameters")
        if full.get("state") != "complete":
            continue
        if (not isinstance(document, dict) or document.get("schema") != "pado.sim-full-hk.v1" or
                document.get("non_operational") is not True or
                document.get("sampled_utc_ms") != packet["header"]["utc_ms"] or
                not isinstance(document.get("equipment"), dict)):
            continue
        complete = (packet, document)
        break
    return packets, complete


def _value(document, path):
    try:
        for token in path:
            document = document[token]
        return document
    except (KeyError, IndexError, TypeError):
        return None


def _reading(reading, packets, full, limits, now_ms):
    result = {key: reading[key] for key in ("id", "label", "unit", "decimals", "kind") if key in reading}
    result.update(value=None, sampled_utc=None, received_utc=None, source=None,
                  freshness="MISSING", limit_state="INDETERMINATE", lower=None, upper=None,
                  max_age_s=reading.get("max_age_s", 120))
    choices = []
    selector = reading.get("tm")
    if selector and selector["packet"] in packets:
        packet = packets[selector["packet"]]
        choices.append((packet, packet["fields"].get(selector["field"]), reading.get("max_age_s", 120)))
    if full and reading.get("full_hk_path"):
        packet, document = full
        choices.append((packet, _value(document, reading["full_hk_path"]), reading.get("full_hk_max_age_s", 1200)))
    kind = reading.get("kind", "numeric")
    valid = lambda value: (isinstance(value, str) and len(value) <= 80 if kind == "text" else
        type(value) is bool if kind == "boolean" else
        type(value) is int and abs(value) <= 9007199254740991 if kind in ("code", "counter") else
        _finite(value) and abs(value) <= 1e30)
    choices = [choice for choice in choices if valid(choice[1])]
    if not choices:
        return result
    packet, value, max_age = max(choices, key=lambda choice: (choice[0]["header"]["utc_ms"], choice[0]["id"]))
    age = now_ms - packet["header"]["utc_ms"]
    if age < -30000:
        return result
    fresh = age < max_age * 1000
    result.update(value=value, sampled_utc=packet["sampled_utc"], received_utc=packet["received_utc"],
                  source={"packet_name": packet["name"], "id": packet["id"]},
                  freshness="CURRENT" if fresh else "STALE", max_age_s=max_age)
    rule = limits.get("sensors", {}).get(reading.get("sensor_limit_key"))
    if rule and rule.get("unit") == reading["unit"] and _finite(value):
        result.update(lower=rule["lower"], upper=rule["upper"])
        result["limit_state"] = ("IN_RANGE" if rule["lower"] <= value <= rule["upper"] else "OUT_OF_RANGE") if fresh else "INDETERMINATE"
    elif fresh:
        result["limit_state"] = "UNCONFIGURED"
    return result


def equipment_status(archive: Path | None, *, settings_path=None, layout_path=None, limits=None, now_s=None):
    try:
        now_s = time.time() if now_s is None else now_s
        layout, digest = load_layout(settings_path, layout_path)
        names = {r["tm"]["packet"] for item in layout["equipment"] for r in item["readings"] if r.get("tm")}
        packets, full = _latest(archive, names) if archive is not None else ({}, None)
        output = []
        summary = {"current": 0, "alarms": 0, "stale": 0, "missing": 0, "partial": 0}
        for item in layout["equipment"]:
            entry = {key: copy.deepcopy(value) for key, value in item.items() if key != "readings"}
            readings = [_reading(r, packets, full, limits or {}, int(now_s * 1000)) for r in item["readings"]]
            states = [r["freshness"] for r in readings]
            status = ("ALARM" if any(r["limit_state"] == "OUT_OF_RANGE" for r in readings) else
                "CURRENT" if states and all(s == "CURRENT" for s in states) else
                "PARTIAL" if "CURRENT" in states else "STALE" if "STALE" in states else "MISSING")
            summary[{"ALARM": "alarms", "CURRENT": "current", "PARTIAL": "partial", "STALE": "stale", "MISSING": "missing"}[status]] += 1
            entry.update(readings=readings, status=status)
            output.append(entry)
        from datetime import datetime, timezone
        payload = {"schema": "pado.equipment-status.v1", "generated_utc": datetime.fromtimestamp(now_s, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "layout_sha256": digest, "layout": {key: value for key, value in layout.items() if key != "equipment"},
            "equipment": output, "summary": summary}
        if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise EquipmentError("equipment_status_too_large")
        return payload
    except (ArchiveError, sqlite3.Error, OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError) as error:
        if isinstance(error, EquipmentError):
            raise
        raise EquipmentError("equipment_status_unavailable") from error
