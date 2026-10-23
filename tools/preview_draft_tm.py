"""Build a ROS test snapshot from draft UHF TM PDUs and explicit test limits.

No sensor range is inferred from a vendor brochure or packet numeric type.
This does not connect to the live ROS TM store or the spacecraft link.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__:
    from .pado_packet_codec import PacketError, decode, load_catalog
else:
    from pado_packet_codec import PacketError, decode, load_catalog


def _iso(milliseconds: int) -> str:
    if milliseconds % 1000:
        raise ValueError("ROS preview requires whole-second TM timestamps")
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


MAX_SIM_HISTORY_PDUS = 3 * 721 + 1  # Up to one hour at the simulator's five-second cadence.


def build_snapshot(packets: list[bytes], limits: dict[str, Any], *, received_ms: int,
                   max_packets: int = 256) -> dict:
    if (type(max_packets) is not int or not 1 <= max_packets <= MAX_SIM_HISTORY_PDUS or
            not isinstance(packets, list) or not 1 <= len(packets) <= max_packets or
            type(received_ms) is not int):
        raise ValueError("bounded packet list and received UTC required")
    if not isinstance(limits, dict) or set(limits) != {"sensors"} or \
            not isinstance(limits["sensors"], dict) or \
            not 1 <= len(limits["sensors"]) <= 64:
        raise ValueError("explicit test sensor limit map required")
    catalog = load_catalog()
    tm_fields = {entry["name"]: entry["fields"] for entry in catalog["telemetry"]}
    sensors: dict[str, dict] = {}
    ids: set[str] = set()
    for key, rule in limits["sensors"].items():
        if not isinstance(key, str) or key.count(".") != 1:
            raise ValueError("sensor key must be TM_NAME.field")
        packet_name, field = key.split(".")
        field_type = (tm_fields.get(packet_name) or {}).get(field)
        if field_type not in ("u8", "u16", "u32", "u64", "f32", "f64") or \
                not isinstance(rule, dict) or \
                set(rule) != {"id", "name", "group", "unit", "lower", "upper"}:
            raise ValueError("test sensor is absent or not scalar")
        if (not isinstance(rule["id"], str) or
                not re.fullmatch(r"[A-Za-z0-9._-]{1,48}", rule["id"]) or
                rule["id"] in ids or
                any(not isinstance(rule[name], str) or not 1 <= len(rule[name]) <= maximum
                    for name, maximum in (("name", 64), ("group", 64), ("unit", 12))) or
                any(type(rule[name]) not in (int, float) or not math.isfinite(rule[name])
                    for name in ("lower", "upper")) or
                rule["lower"] >= rule["upper"]):
            raise ValueError("invalid test sensor metadata or limits")
        ids.add(rule["id"])
        sensors[key] = {**rule, "samples": []}
    attitude = None
    for packet in packets:
        decoded = decode(packet, catalog=catalog)
        if decoded["kind"] != "TM" or decoded["operational"] is not False or \
                decoded["utc_ms"] > received_ms:
            raise ValueError("only received draft TM may enter the preview")
        stamp = _iso(decoded["utc_ms"])
        for key, sensor in sensors.items():
            packet_name, field = key.split(".")
            if decoded["name"] == packet_name:
                sensor["samples"].append({"utc": stamp, "value": decoded["fields"][field]})
        if decoded["name"] == "AOCS_STATE":
            values = [decoded["fields"][f"q_{axis}"] for axis in "wxyz"]
            if abs(math.hypot(*values) - 1) > 0.02:
                raise ValueError("non-unit draft attitude quaternion")
            if attitude is None or stamp > attitude["utc"]:
                attitude = {"utc": stamp, "frame": "TEST_FRAME",
                            "quaternion_wxyz": values}
    output = []
    for sensor in sensors.values():
        samples = sorted(sensor["samples"], key=lambda sample: sample["utc"])
        if not samples or len(samples) > 3600 or \
                any(left["utc"] == right["utc"] for left, right in zip(samples, samples[1:])):
            raise ValueError("each configured test sensor needs unique TM samples")
        output.append({**sensor, "samples": samples})
    snapshot = {"type": "TEST_ONLY_TM_SNAPSHOT", "non_operational": True,
                "received_utc": _iso(received_ms), "sensors": output,
                "attitude": attitude}
    if len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > 1024 * 1024:
        raise ValueError("ROS test snapshot exceeds 1 MiB")
    return snapshot


def publish_snapshot(snapshot: dict, destination: Path) -> None:
    """Replace the test-only read target only after a complete bounded write."""
    if (destination.name != "test_tm_snapshot.json" or
            destination.parent.is_symlink() or not destination.parent.is_dir() or
            destination.is_symlink()):
        raise ValueError("regular test snapshot destination directory required")
    body = (json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(body) > 1024 * 1024:
        raise ValueError("ROS test snapshot exceeds 1 MiB")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".test_tm_", suffix=".partial",
                                         dir=destination.parent, delete=False) as output:
            temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-only", action="store_true", required=True)
    parser.add_argument("--limits", type=Path, required=True)
    parser.add_argument("--out", type=Path,
                        help="atomically publish to test_tm_snapshot.json; default prints stdout")
    parser.add_argument("packets", type=Path, nargs="+")
    args = parser.parse_args()
    try:
        if args.limits.is_symlink() or args.limits.stat().st_size > 16384:
            raise ValueError("bounded regular limits file required")
        limits = json.loads(args.limits.read_text(encoding="utf-8"))
        packets = []
        for source in args.packets:
            if source.is_symlink() or not source.is_file() or not 24 <= source.stat().st_size <= 200:
                raise ValueError("bounded regular PDU files required")
            packets.append(source.read_bytes())
        received = int(datetime.now(timezone.utc).timestamp()) * 1000
        snapshot = build_snapshot(packets, limits, received_ms=received)
        if args.out:
            publish_snapshot(snapshot, args.out)
    except (OSError, ValueError, PacketError, OverflowError) as error:
        parser.exit(2, f"draft TM preview rejected input: {error}\n")
    if not args.out:
        print(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
