"""Reproducible packet corpus and expected outcomes, kept off the TM wire.

The extra trigger belongs only to the validation link. It is never added to
the spacecraft ICD or enabled by the operations deployment.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import struct
import zlib
from dataclasses import dataclass

from services.spacecraft_config import get_sensor_limits
from tools.pado_packet_codec import (CRC, HEADER, TYPES, decode, encode_tc,
                                     encode_tm, load_catalog, split_full_hk)

TRIGGER = "validation_tm_dump"
TRIGGER_ID = 32766
COUNT = 10000
MAX_COUNT = 10000


def catalog():
    result = copy.deepcopy(load_catalog())
    result["commands"].append({"id": TRIGGER_ID, "name": TRIGGER,
        "group": "VALIDATION", "risk": "low", "fields": {
            "seed": "u32", "count": "u16", "base_utc_ms": "u64"}})
    return result


def _canonical(fields, definition):
    result = {}
    for key, value in fields.items():
        kind = definition[key]
        if kind == "f32":
            value = struct.unpack(">f", struct.pack(">f", value))[0]
        if isinstance(value, bytes):
            value = {"encoding": "hex", "value": value.hex()}
        result[key] = value
    return result


@dataclass(frozen=True)
class Case:
    index: int
    packet: bytes
    category: str
    mutation: str | None
    name: str
    expected_fields: dict | None
    spike_keys: tuple[str, ...] = ()

    def expected(self):
        return {"case_id": self.index, "sha256": hashlib.sha256(self.packet).hexdigest(),
            "category": self.category, "mutation": self.mutation, "name": self.name,
            "expected_valid": self.category != "malformed", "fields": self.expected_fields,
            "spike_keys": list(self.spike_keys)}


def _values(entry, rng, stamp, index, limits):
    fields = {}
    for name, kind in entry["fields"].items():
        if kind == "bool": value = bool(rng.getrandbits(1))
        elif kind == "ascii69": value = ("PADO-SAT " + str(index)).ljust(69)
        elif kind == "bytes160": value = bytes(160)
        elif kind.startswith("f"): value = rng.uniform(.1, 5)
        elif kind == "i32": value = rng.randint(-1000, 1000)
        else: value = rng.randint(0, min(10, 2 ** (8 * struct.calcsize(TYPES[kind])) - 1))
        rule = limits.get(f"{entry['name']}.{name}")
        if rule and kind.startswith("f"):
            value = rng.uniform(rule["lower"] + .2*(rule["upper"]-rule["lower"]),
                                rule["upper"] - .2*(rule["upper"]-rule["lower"]))
        fields[name] = value
    if entry["name"] == "GNSS_PVT":
        angle = stamp/1000*2*math.pi/5700
        radius = rng.uniform(6828137, 6928137)
        fields.update(norad_id=99999, solution_status=1, receiver_age_ms=0,
            x_m=radius*math.cos(angle),y_m=radius*math.sin(angle),z_m=0.,
            vx_mps=-7500*math.sin(angle),vy_mps=7500*math.cos(angle),vz_mps=0.)
    if entry["name"] == "AOCS_STATE":
        q = [rng.uniform(-1, 1) for _ in range(4)]
        length = math.hypot(*q)
        fields.update({f"q_{key}": value/length for key,value in zip("wxyz",q)})
    if entry["name"] == "COMMAND_RESULT":
        accepted = bool(rng.getrandbits(1))
        fields.update(command_id=4098,command_sequence=index+1,result=0 if accepted else 1,
            reason=0 if accepted else rng.randint(1,6),executed_at_utc_ms=stamp if accepted else 0)
    return fields


def _crc(packet):
    return bytes(packet[:-4]) + CRC.pack(zlib.crc32(packet[:-4]) & 0xffffffff)


def malformed(packet, entry, mutation):
    raw = bytearray(packet)
    if mutation == "magic": raw[0] ^= 1
    elif mutation == "version": raw[2] = 255
    elif mutation == "kind": raw[3] = 9
    elif mutation == "packet_id": raw[4:6] = b"\xff\xff"
    elif mutation == "payload_length": raw[16:18] = b"\xff\xff"
    elif mutation == "flags": raw[18] = 0
    elif mutation == "reserved": raw[19] = 1
    elif mutation == "crc":
        raw[-1] ^= 1
        return bytes(raw)
    elif mutation == "truncated": return bytes(raw[:23])
    elif mutation == "oversized": return bytes(raw) + bytes(201-len(raw))
    elif mutation in ("nan", "infinity", "boolean", "ascii"):
        offset = HEADER.size
        for kind in entry["fields"].values():
            if mutation in ("nan","infinity") and kind.startswith("f"):
                raw[offset:offset+struct.calcsize(TYPES[kind])] = struct.pack(">"+TYPES[kind], float("nan") if mutation=="nan" else float("inf"))
                break
            if mutation == "boolean" and kind == "bool": raw[offset] = 2; break
            if mutation == "ascii" and kind == "ascii69": raw[offset] = 255; break
            offset += struct.calcsize(TYPES[kind])
        else:
            raise ValueError("mutation field unavailable")
    elif mutation == "chunk_index": raw[HEADER.size+4:HEADER.size+6] = b"\xff\xff"
    elif mutation == 'timestamp':raw[8:16]=struct.pack('>Q',struct.unpack('>Q',raw[8:16])[0]+7*86400000)
    elif mutation in ('quaternion_norm','gnss_fix','gnss_state'):
        offset=HEADER.size
        for name,kind in entry['fields'].items():
            if ((mutation=='quaternion_norm' and name.startswith('q_')) or
                (mutation=='gnss_fix' and name=='solution_status') or
                (mutation=='gnss_state' and name in ('x_m','y_m','z_m'))):
                value=1e308 if mutation=='gnss_state' and name=='x_m' else 0
                raw[offset:offset+struct.calcsize(TYPES[kind])]=struct.pack('>'+TYPES[kind],value)
            offset+=struct.calcsize(TYPES[kind])
    else: raise ValueError("unknown mutation")
    # Keep CRC correct for structural/field mutations so the deeper parser
    # branch is exercised instead of only its CRC guard.
    return _crc(raw)


def generate(seed: int, base_utc_ms: int, count=COUNT, *, limits=None):
    if type(seed) is not int or not 0 <= seed <= 0xffffffff or type(count) is not int or not 100 <= count <= MAX_COUNT:
        raise ValueError("bounded corpus seed/count required")
    if type(base_utc_ms) is not int or base_utc_ms <= 0 or base_utc_ms % 1000:
        raise ValueError("whole-second corpus base required")
    rules = (get_sensor_limits() if limits is None else limits)["sensors"]
    rng = random.Random(seed)
    entries = load_catalog()["telemetry"]
    mutations = ["magic","version","kind","packet_id","payload_length","flags",
                 "reserved","crc","truncated","oversized","nan","infinity","boolean","ascii","chunk_index",
                 'timestamp','quaternion_norm','gnss_fix','gnss_state']
    cases = []
    spikes = [(key, rule) for key,rule in rules.items() if any(
        e["name"]==key.split(".")[0] and e["fields"].get(key.split(".")[1],"").startswith("f") for e in entries)
        and not key.startswith('GNSS_PVT.') and not key.startswith('AOCS_STATE.q_')]
    if not spikes:raise ValueError('at least one configured scalar sensor is needed for valid spike packets')
    if any(max(abs(r['lower']),abs(r['upper']))>1e30 for r in rules.values()):
        raise ValueError('validation sensor ranges exceed the finite corpus generation envelope')
    for index in range(count):
        category = "malformed" if index % 4 == 0 else "spike" if index % 20 == 1 else "valid"
        mutation = mutations[(index//4) % len(mutations)] if category == "malformed" else None
        spike_keys = ()
        entry = entries[index % len(entries)]
        if category == "spike":
            key, rule = spikes[(index//20) % len(spikes)]
            entry = next(e for e in entries if e["name"]==key.split(".")[0])
            spike_keys = (key,)
        elif mutation in ('quaternion_norm','gnss_fix','gnss_state'):
            entry=next(e for e in entries if e['name']==('AOCS_STATE' if mutation=='quaternion_norm' else 'GNSS_PVT'))
        elif mutation in ("nan","infinity","boolean","ascii","chunk_index"):
            predicate = {"nan":lambda t:t.startswith("f"),"infinity":lambda t:t.startswith("f"),
                "boolean":lambda t:t=="bool","ascii":lambda t:t=="ascii69",
                "chunk_index":lambda t:t=="bytes160"}[mutation]
            options = [e for e in entries if any(predicate(t) for t in e["fields"].values())]
            entry = options[(index//4) % len(options)]
        stamp = base_utc_ms + index*1000
        fields = _values(entry,rng,stamp,index,rules)
        if spike_keys:
            field = spike_keys[0].split(".")[1]
            width = max(1,rule["upper"]-rule["lower"])
            fields[field] = rule["upper"] + width*rng.uniform(1,3)
        if entry["name"] == "FULL_HK_CHUNK":
            data = json.dumps({"schema":"pado.sim-full-hk.v1","non_operational":True,
                "sampled_utc_ms":stamp,"mode":2,"units":"SI","equipment":{"validation":index}},separators=(",",":")).encode()
            packet = split_full_hk(data,index+1,stamp,index)[0]
            # A compact single chunk makes each randomized case independently complete.
            fields = {"record_id":index+1,"chunk_index":0,"chunk_count":1,"total_len":len(data),
                "data_len":len(data),"record_crc32":zlib.crc32(data)&0xffffffff,"data":data.ljust(160,b"\x00")}
        else:
            packet = encode_tm(entry["name"],index,stamp,fields)
        expected = _canonical(fields,entry["fields"]) if category != "malformed" else None
        if mutation: packet = malformed(packet,entry,mutation)
        cases.append(Case(index,packet,category,mutation,entry["name"],expected,spike_keys))
    rng.shuffle(cases)
    return cases


def tc_matrix(base_utc_ms: int):
    """Finite representative/boundary matrix; undefined ICDs remain explicit."""
    result, blocked = [], []
    tle = ["1 25544U 98067A   26295.91886925  .00009886  00000-0  18726-3 0  9990",
           "2 25544  51.6334  28.0071 0006866 297.7993  62.2299 15.49005378591551"]
    for entry in load_catalog()["commands"]:
        if entry["fields"] is None:
            blocked.append({"id":entry["id"],"name":entry["name"],"state":"ICD_UNDEFINED"})
            continue
        variants = ["representative","minimum","maximum"] if entry["fields"] else ["representative"]
        for variant in variants:
            values = {}
            for name, kind in entry["fields"].items():
                if kind == "ascii69": value = tle[0 if name=="line1" else 1]
                elif kind == "bool": value = variant != "minimum"
                elif kind == "u64": value = base_utc_ms
                else: value = 0 if variant=="minimum" else (2**(8*struct.calcsize(TYPES[kind]))-1 if variant=="maximum" else 1)
                values[name] = value
            if entry["name"] == "hk_dump": values = {"start_utc_ms":0,"end_utc_ms":0}
            # Identical field encodings need only one transmitted representative.
            if any(r["name"]==entry["name"] and r["parameters"]==values for r in result): continue
            packet = encode_tc(entry["name"],len(result)+1,base_utc_ms,values)
            result.append({"name":entry["name"],"command_id":entry["id"],"variant":variant,
                "parameters":values,"size_bytes":len(packet)})
    return {"cases":result,"undefined_commands":blocked,"defined_command_count":len({r['name'] for r in result}),
            "coverage":"all defined command types; representative and integer/boolean boundary vectors"}
