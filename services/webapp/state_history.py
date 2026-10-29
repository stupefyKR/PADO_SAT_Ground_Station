"""Read-only, receipt-paginated GNSS and measured attitude history for ROS."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl

from services.sim_ground.archive import ARCHIVE_FILENAME, ArchiveError, _connect
from tools.pado_packet_codec import PacketError, decode, load_catalog


WINDOW_S = 72 * 3600
MAX_HISTORY_BYTES = 256 * 1024
MAX_PAGE_RECORDS = 1000
SATELLITE_ID = 99999


class StateHistoryError(ValueError):
    pass


def _utc(stamp_ms: int) -> str:
    return datetime.fromtimestamp(stamp_ms/1000, timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def history_query(query: str, *, orbit=False, now_s: int | None = None) -> dict:
    """Only numeric time bounds and bounded pagination; no file identifiers."""
    now_s = int(time.time()) if now_s is None else now_s
    if len(query)>512:
        raise StateHistoryError("invalid_query")
    try:
        pairs = parse_qsl(query,keep_blank_values=True,strict_parsing=True,max_num_fields=5) if query else []
    except ValueError as error:
        raise StateHistoryError("invalid_query") from error
    values = dict(pairs)
    allowed = {"start_utc_s","end_utc_s","target"} if orbit else \
        {"start_utc_s","end_utc_s","after_id","limit","follow"}
    if len(values)!=len(pairs) or not set(values)<=allowed:
        raise StateHistoryError("invalid_query")
    result = {}
    if orbit:
        target = values.pop("target","synthetic")
        if target not in ("synthetic","iss","operational"):
            raise StateHistoryError("invalid_query")
        result["target"] = target
    for key,value in values.items():
        if not re.fullmatch(r"0|[1-9][0-9]{0,18}",value):
            raise StateHistoryError("invalid_query")
        result[key] = int(value)
    start = result.get("start_utc_s",now_s-WINDOW_S)
    end = result.get("end_utc_s",now_s)
    if (not 0<start<end or end-start>WINDOW_S or end>now_s+60 or
            end<now_s-WINDOW_S or start<now_s-WINDOW_S-300 or
            not 0<=result.get("after_id",0)<=0x7fffffffffffffff or
            not 1<=result.get("limit",500)<=MAX_PAGE_RECORDS or
            result.get("follow",0) not in (0,1)):
        raise StateHistoryError("invalid_query")
    result.update(start_utc_s=max(start,now_s-WINDOW_S),end_utc_s=min(end,now_s))
    if result["start_utc_s"]>=result["end_utc_s"]:
        raise StateHistoryError("invalid_query")
    if not orbit:
        result.setdefault("after_id",0)
        result.setdefault("limit",500)
        result.setdefault("follow",0)
    return result


def _finite_vector(fields: dict, names: tuple[str,...]) -> list[float] | None:
    values = [fields.get(name) for name in names]
    return values if all(type(v) in (int,float) and math.isfinite(v) for v in values) else None


def _record(row, catalog) -> tuple[str,dict]:
    ident,name,sampled,received,raw = row
    base = {"id":ident,"utc":_utc(sampled),"received_utc":_utc(received),"valid":False}
    family = "positions" if name=="GNSS_PVT" else "attitudes"
    if family=="positions":
        base.update(norad_id=SATELLITE_ID,position_itrf_m=None,velocity_itrf_mps=None,source="GNSS_TM")
    else:
        base.update(quaternion_wxyz=None,frame="SIM_BODY_TO_ITRF",source="AOCS_TM")
    try:
        if not isinstance(raw,bytes) or not 24<=len(raw)<=200 or not 0<sampled<=received:
            raise ValueError("invalid history packet")
        packet = decode(raw,catalog=catalog)
        if (packet["kind"]!="TM" or packet["name"]!=name or packet["utc_ms"]!=sampled or
                packet["operational"] is not False):
            raise ValueError("mismatched history packet")
        fields = packet["fields"]
        if family=="positions":
            position = _finite_vector(fields,("x_m","y_m","z_m"))
            velocity = _finite_vector(fields,("vx_mps","vy_mps","vz_mps"))
            if (fields.get("norad_id")!=SATELLITE_ID or fields.get("solution_status")!=1 or
                    type(fields.get("receiver_age_ms")) is not int or fields["receiver_age_ms"]>1000 or
                    position is None or velocity is None or
                    not 6.3e6<=math.hypot(*position)<=8e6 or math.hypot(*velocity)>12000):
                base["reason"] = "invalid_gnss_solution"
            else:
                base.update(valid=True,position_itrf_m=position,velocity_itrf_mps=velocity)
        else:
            q = _finite_vector(fields,("q_w","q_x","q_y","q_z"))
            length = math.hypot(*q) if q is not None else 0
            if not .98<=length<=1.02:
                base["reason"] = "invalid_quaternion"
            else:
                base.update(valid=True,quaternion_wxyz=[v/length for v in q])
    except (PacketError,ValueError,KeyError,TypeError):
        base["reason"] = "corrupt_packet"
    return family,base


def state_history(path: Path | None, *, start_utc_s: int, end_utc_s: int,
                  after_id: int=0, limit: int=500, follow: int=0,
                  now_s: int | None=None) -> dict:
    """A receipt cursor includes late TM at its historical measurement time.

    The snapshot's global high-water mark advances even when newly received
    packets are unrelated telemetry. Database bytes and paths remain private.
    ``follow=1`` pauses that cursor at state measurements beyond a rolling
    request's captured end, leaving them for the next live-window refresh.
    """
    now_s = int(time.time()) if now_s is None else now_s
    if (type(start_utc_s) is not int or type(end_utc_s) is not int or
            not 0<start_utc_s<end_utc_s or end_utc_s-start_utc_s>WINDOW_S or
            type(after_id) is not int or not 0<=after_id<=0x7fffffffffffffff or
            type(limit) is not int or not 1<=limit<=MAX_PAGE_RECORDS or
            type(follow) is not int or follow not in (0,1)):
        raise StateHistoryError("invalid_query")
    result = {"schema":"pado.state-history.v1","state":"WAITING","non_operational":True,
        "norad_id":SATELLITE_ID,"start_utc_s":start_utc_s,"end_utc_s":end_utc_s,
        "server_utc_s":now_s,"after_id":after_id,"next_after_id":after_id,
        "has_more":False,"revision":0,"deferred":False,"positions":[],"attitudes":[]}
    if path is None or not path.exists():
        return result
    if path.name!=ARCHIVE_FILENAME:
        raise StateHistoryError("state_history_unavailable")
    try:
        connection = _connect(path)
        try:
            connection.execute("BEGIN")
            revision = connection.execute("SELECT COALESCE(MAX(id),0) FROM packets").fetchone()[0]
            if after_id>revision:
                # A replaced archive cannot silently masquerade as an unchanged
                # feed. The caller must discard its old receipt cursor/cache.
                result.update(state="RESET_REQUIRED",revision=revision,next_after_id=0)
                return result
            effective_revision=revision
            end_ms=min(end_utc_s,now_s)*1000
            if follow:
                # A rolling request captures its end before a network/page
                # round trip. State TM already received after that end must
                # remain behind the receipt cursor until a later refresh
                # advances the window. Otherwise MAX(id) skips it forever.
                first_deferred=connection.execute(
                    "SELECT MIN(id) FROM packets WHERE id>? AND id<=? "
                    "AND name IN ('GNSS_PVT','AOCS_STATE') AND sampled_ms>?",
                    (after_id,revision,end_ms)).fetchone()[0]
                if first_deferred is not None:
                    effective_revision=first_deferred-1
                    result["deferred"]=True
            rows = connection.execute("SELECT id,name,sampled_ms,received_ms,raw FROM packets "
                "WHERE id>? AND id<=? AND name IN ('GNSS_PVT','AOCS_STATE') "
                "AND sampled_ms>=? AND sampled_ms<=? ORDER BY id LIMIT ?",
                (after_id,effective_revision,max(start_utc_s,now_s-WINDOW_S)*1000,
                 end_ms,limit+1)).fetchall()
        finally:
            connection.close()
        result.update(state="AVAILABLE",revision=revision)
        catalog = load_catalog()
        cursor, used = after_id, 1000
        stopped = False
        for row in rows[:limit]:
            family,item = _record(row,catalog)
            size = len(json.dumps(item,separators=(",",":"),allow_nan=False).encode())+1
            if used+size>MAX_HISTORY_BYTES-2048:
                stopped=True
                break
            result[family].append(item)
            used+=size
            cursor=row[0]
        more = stopped or len(rows)>limit
        result.update(has_more=more,next_after_id=cursor if more else effective_revision)
        if len(json.dumps(result,separators=(",",":"),allow_nan=False).encode())>MAX_HISTORY_BYTES:
            raise StateHistoryError("state_history_too_large")
        return result
    except (ArchiveError,sqlite3.Error,OSError,ValueError,TypeError) as error:
        if isinstance(error,StateHistoryError):
            raise
        raise StateHistoryError("state_history_unavailable") from error
