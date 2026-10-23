"""Draft PADO-SAT UHF application PDU codec. Never emits an operational uplink.

This is a ground-owned proposal, not an AX100 RF decoder or spacecraft ICD.
Every encoded packet carries the draft bit and lacks a flight authentication tag.
"""

from __future__ import annotations

import json
import math
import re
import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).resolve().parents[1] / "interfaces/pado_packets/draft_v0.json"
HEADER = struct.Struct(">2sBBHHQHBB")
CRC = struct.Struct(">I")
TYPES = {"u8": "B", "u16": "H", "u32": "I", "u64": "Q",
         "i32": "i", "f32": "f", "f64": "d", "bool": "B", "ascii69": "69s",
         "bytes160": "160s"}
DRAFT_FLAG = 0x80
SCHEDULED_FLAG = 0x01


class PacketError(ValueError):
    """A proposed packet is malformed or cannot safely be represented."""


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PacketError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    catalog = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
    if (catalog.get("schema") != "pado.packet-catalog.v0" or
            catalog.get("status") != "DRAFT_NOT_FLIGHT_APPROVED" or
            catalog.get("operational") is not False or
            catalog.get("scope") != "UHF_TC_TM_APPLICATION_PDU" or
            catalog.get("max_packet_bytes") != 200):
        raise PacketError("catalog must remain a bounded, non-operational draft")
    for group in ("commands", "telemetry"):
        entries = catalog.get(group)
        if not isinstance(entries, list) or not entries:
            raise PacketError(f"empty {group}")
        seen_ids: set[int] = set()
        seen_names: set[str] = set()
        for entry in entries:
            ident, name, fields = entry.get("id"), entry.get("name"), entry.get("fields")
            if (type(ident) is not int or not 1 <= ident <= 65535 or
                    not isinstance(name, str) or
                    re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) is None or
                    ident in seen_ids or name in seen_names or
                    (fields is not None and not isinstance(fields, dict))):
                raise PacketError(f"invalid {group} identity or field map")
            seen_ids.add(ident)
            seen_names.add(name)
            if fields is not None:
                for field, type_name in fields.items():
                    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", field) is None or \
                            type_name not in TYPES:
                        raise PacketError(f"unsupported field: {field}")
                size = struct.calcsize(">" + "".join(TYPES[t] for t in fields.values()))
                if size + HEADER.size + CRC.size + (8 if group == "commands" else 0) > 200:
                    raise PacketError(f"{name} exceeds UHF application PDU budget")
    return catalog


def _entry(catalog: dict[str, Any], group: str, key: str | int) -> dict[str, Any]:
    for entry in catalog[group]:
        if entry["name"] == key or entry["id"] == key:
            if entry["fields"] is None:
                raise PacketError(f"{entry['name']} awaits subsystem ICD")
            return entry
    raise PacketError(f"unknown {group} item: {key}")


def _pack_fields(definition: dict[str, str], values: dict[str, Any]) -> bytes:
    if set(values) != set(definition):
        raise PacketError("field names do not match packet definition")
    result = bytearray()
    for name, type_name in definition.items():
        value = values[name]
        if type_name == "ascii69":
            if not isinstance(value, str) or len(value) != 69 or not value.isascii() or \
                    not value.isprintable():
                raise PacketError(f"{name} must contain 69 printable ASCII characters")
            result.extend(value.encode("ascii"))
        elif type_name == "bytes160":
            if not isinstance(value, bytes) or len(value) != 160:
                raise PacketError(f"{name} must contain exactly 160 bytes")
            result.extend(value)
        elif type_name == "bool":
            if type(value) is not bool:
                raise PacketError(f"{name} must be boolean")
            result.extend(struct.pack(">B", int(value)))
        elif type_name.startswith("f"):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise PacketError(f"{name} must be finite")
            try:
                packed = struct.pack(">" + TYPES[type_name], value)
            except (OverflowError, struct.error) as exc:
                raise PacketError(f"{name} out of range") from exc
            if not math.isfinite(struct.unpack(">" + TYPES[type_name], packed)[0]):
                raise PacketError(f"{name} loses finite range")
            result.extend(packed)
        else:
            if type(value) is not int:
                raise PacketError(f"{name} must be integer")
            try:
                result.extend(struct.pack(">" + TYPES[type_name], value))
            except struct.error as exc:
                raise PacketError(f"{name} out of range") from exc
    return bytes(result)


def _unpack_fields(definition: dict[str, str], payload: bytes) -> dict[str, Any]:
    expected = struct.calcsize(">" + "".join(TYPES[t] for t in definition.values()))
    if len(payload) != expected:
        raise PacketError("payload length differs from catalog")
    result: dict[str, Any] = {}
    offset = 0
    for name, type_name in definition.items():
        fmt = ">" + TYPES[type_name]
        value = struct.unpack_from(fmt, payload, offset)[0]
        offset += struct.calcsize(fmt)
        if type_name == "ascii69":
            try:
                value = value.decode("ascii")
            except UnicodeError as exc:
                raise PacketError("non-ASCII packet string") from exc
            if not value.isprintable():
                raise PacketError("non-printable packet string")
        elif type_name == "bool":
            if value not in (0, 1):
                raise PacketError("invalid boolean encoding")
            value = bool(value)
        elif type_name.startswith("f") and not math.isfinite(value):
            raise PacketError("non-finite telemetry value")
        result[name] = value
    return result


def _encode(kind: int, entry: dict[str, Any], sequence: int, utc_ms: int,
            payload: bytes, flags: int, catalog: dict[str, Any]) -> bytes:
    if type(sequence) is not int or not 0 <= sequence <= 65535 or \
            type(utc_ms) is not int or not 1 <= utc_ms <= 0xffffffffffffffff:
        raise PacketError("invalid sequence or UTC milliseconds")
    packet = HEADER.pack(b"PS", 0, kind, entry["id"], sequence, utc_ms,
                         len(payload), flags | DRAFT_FLAG, 0) + payload
    packet += CRC.pack(zlib.crc32(packet) & 0xffffffff)
    if len(packet) > catalog["max_packet_bytes"]:
        raise PacketError("packet exceeds proposed AX100 application limit")
    return packet


def encode_tc(name: str, sequence: int, issued_at_utc_ms: int,
              fields: dict[str, Any], *, execute_at_utc_ms: int = 0,
              catalog: dict[str, Any] | None = None) -> bytes:
    catalog = catalog or load_catalog()
    entry = _entry(catalog, "commands", name)
    if type(execute_at_utc_ms) is not int or not 0 <= execute_at_utc_ms <= 0xffffffffffffffff:
        raise PacketError("invalid execution time")
    if execute_at_utc_ms and execute_at_utc_ms <= issued_at_utc_ms:
        raise PacketError("scheduled TC must execute after issue time")
    if name == "aocs_set_tle":
        line1, line2 = fields.get("line1"), fields.get("line2")
        if not isinstance(line1, str) or not isinstance(line2, str) or \
                len(line1) != 69 or len(line2) != 69 or \
                not line1.startswith("1 ") or not line2.startswith("2 ") or \
                line1[2:7] != line2[2:7] or \
                not all(line[-1].isdigit() and
                        sum(int(char) if char.isdigit() else 1 if char == "-" else 0
                            for char in line[:68]) % 10 == int(line[-1])
                        for line in (line1, line2)):
            raise PacketError("TLE lines, satellite IDs or checksums invalid")
    if name == "hk_dump" and fields.get("start_utc_ms", 0) >= fields.get("end_utc_ms", 0):
        raise PacketError("HK dump end time must follow start time")
    payload = struct.pack(">Q", execute_at_utc_ms) + _pack_fields(entry["fields"], fields)
    return _encode(1, entry, sequence, issued_at_utc_ms, payload,
                   SCHEDULED_FLAG if execute_at_utc_ms else 0, catalog)


def encode_tm(name: str, sequence: int, sampled_at_utc_ms: int,
              fields: dict[str, Any], *, catalog: dict[str, Any] | None = None) -> bytes:
    catalog = catalog or load_catalog()
    entry = _entry(catalog, "telemetry", name)
    return _encode(2, entry, sequence, sampled_at_utc_ms,
                   _pack_fields(entry["fields"], fields), 0, catalog)


def decode(packet: bytes, *, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog = catalog or load_catalog()
    if not isinstance(packet, bytes) or not HEADER.size + CRC.size <= len(packet) <= 200:
        raise PacketError("invalid packet size")
    magic, version, kind, ident, sequence, utc_ms, length, flags, reserved = HEADER.unpack_from(packet)
    if (magic != b"PS" or version != 0 or kind not in (1, 2) or utc_ms == 0 or
            reserved != 0 or flags & DRAFT_FLAG == 0 or
            flags & ~(DRAFT_FLAG | SCHEDULED_FLAG) or
            length != len(packet) - HEADER.size - CRC.size or
            CRC.unpack_from(packet, len(packet) - CRC.size)[0] !=
            zlib.crc32(packet[:-CRC.size]) & 0xffffffff):
        raise PacketError("header, length, draft marker or CRC invalid")
    group = "commands" if kind == 1 else "telemetry"
    entry = _entry(catalog, group, ident)
    payload = packet[HEADER.size:-CRC.size]
    if kind == 1:
        if len(payload) < 8:
            raise PacketError("TC execution time missing")
        execute_at = struct.unpack_from(">Q", payload)[0]
        if bool(flags & SCHEDULED_FLAG) != bool(execute_at) or \
                (execute_at and execute_at <= utc_ms):
            raise PacketError("TC scheduling flag or time invalid")
        payload = payload[8:]
    elif flags & SCHEDULED_FLAG:
        raise PacketError("TM cannot carry a TC scheduling flag")
    result = {"kind": "TC" if kind == 1 else "TM", "name": entry["name"],
              "id": ident, "sequence": sequence, "utc_ms": utc_ms,
              "fields": _unpack_fields(entry["fields"], payload),
              "operational": False}
    if kind == 1:
        result["execute_at_utc_ms"] = execute_at
    elif entry["name"] == "FULL_HK_CHUNK":
        f = result["fields"]
        expected_count = (f["total_len"] + 159) // 160
        expected_length = min(160, f["total_len"] - f["chunk_index"] * 160)
        if (f["total_len"] == 0 or f["chunk_count"] != expected_count or
                f["chunk_index"] >= expected_count or
                f["data_len"] != expected_length or
                f["data"][f["data_len"]:] != bytes(160 - f["data_len"])):
            raise PacketError("full HK chunk index, length or padding invalid")
    return result


def split_full_hk(data: bytes, record_id: int, sampled_at_utc_ms: int,
                  sequence_start: int = 0) -> list[bytes]:
    """Split one opaque stored-HK record into independent 200-byte draft TM PDUs."""
    if not isinstance(data, bytes) or not 1 <= len(data) <= 65535 or \
            type(record_id) is not int or not 1 <= record_id <= 0xffffffff:
        raise PacketError("invalid full HK record")
    count = (len(data) + 159) // 160
    if type(sequence_start) is not int or sequence_start < 0 or \
            sequence_start + count > 65536:
        raise PacketError("full HK sequence range overflows")
    digest = zlib.crc32(data) & 0xffffffff
    packets = []
    for index in range(count):
        chunk = data[index * 160:(index + 1) * 160]
        packets.append(encode_tm("FULL_HK_CHUNK", sequence_start + index,
                                 sampled_at_utc_ms,
                                 {"record_id": record_id, "chunk_index": index,
                                  "chunk_count": count, "total_len": len(data),
                                  "data_len": len(chunk), "record_crc32": digest,
                                  "data": chunk.ljust(160, b"\x00")}))
    return packets


def join_full_hk(packets: list[bytes]) -> bytes:
    """Reassemble a complete draft full-HK record, rejecting mixing and gaps."""
    if not isinstance(packets, list) or not packets:
        raise PacketError("full HK chunks missing")
    decoded = [decode(packet) for packet in packets]
    first = decoded[0]
    if first["name"] != "FULL_HK_CHUNK" or first["kind"] != "TM":
        raise PacketError("full HK chunks required")
    reference = first["fields"]
    if len(decoded) != reference["chunk_count"]:
        raise PacketError("full HK chunks incomplete")
    chunks: dict[int, bytes] = {}
    for item in decoded:
        f = item["fields"]
        if (item["name"] != "FULL_HK_CHUNK" or
                item["utc_ms"] != first["utc_ms"] or
                any(f[key] != reference[key] for key in
                    ("record_id", "chunk_count", "total_len", "record_crc32")) or
                f["chunk_index"] in chunks):
            raise PacketError("full HK record identity or chunk index conflict")
        chunks[f["chunk_index"]] = f["data"][:f["data_len"]]
    if set(chunks) != set(range(reference["chunk_count"])):
        raise PacketError("full HK chunk gap")
    result = b"".join(chunks[i] for i in range(reference["chunk_count"]))
    if len(result) != reference["total_len"] or \
            zlib.crc32(result) & 0xffffffff != reference["record_crc32"]:
        raise PacketError("full HK record CRC or length invalid")
    return result


def gnss_to_fds_properties(decoded: dict[str, Any]) -> str:
    """Produce the existing N11 normalized body from a *draft* GNSS_PVT TM.

    Caller must still create and verify the N11 manifest before FDS ingestion.
    Status mapping and flight packet format require spacecraft ICD confirmation.
    """
    if decoded.get("kind") != "TM" or decoded.get("name") != "GNSS_PVT" or \
            decoded.get("operational") is not False:
        raise PacketError("GNSS_PVT draft TM required")
    fields = decoded["fields"]
    if fields["solution_status"] != 1 or fields["receiver_age_ms"] > 1000 or \
            decoded["utc_ms"] % 1000:
        raise PacketError("GNSS fix, age or whole-second UTC invalid")
    position = math.sqrt(sum(fields[k] ** 2 for k in ("x_m", "y_m", "z_m")))
    speed = math.sqrt(sum(fields[k] ** 2 for k in ("vx_mps", "vy_mps", "vz_mps")))
    if not 6_300_000 <= position <= 100_000_000 or \
            not 100 <= speed <= 15_000 or \
            not 1 <= fields["norad_id"] <= 999999:
        raise PacketError("GNSS state outside orbit envelope")
    stamp = datetime.fromtimestamp(decoded["utc_ms"] / 1000, timezone.utc)
    keys = ("x_m", "y_m", "z_m", "vx_mps", "vy_mps", "vz_mps")
    return ("schema=fds.gnss-state.v1\nframe=ITRF\n"
            f"epoch_utc={stamp:%Y-%m-%dT%H:%M:%SZ}\n"
            f"norad_id={fields['norad_id']}\n"
            "position_valid=true\nvelocity_valid=true\n" +
            "".join(f"{key}={fields[key]:.9f}\n" for key in keys))
