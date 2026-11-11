"""Read literal stack data from a reference HTML without executing JavaScript.

The candidate keeps reviewed telemetry selectors from an existing layout. New
equipment is explicitly unmapped; this never changes power or packet settings.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
MAX_SOURCE_BYTES = 2 * 1024 * 1024
OBJECT_ROW = re.compile(
    r'^\s*([a-z][a-z0-9_]{0,31}):\{name:"([^"\r\n]{1,96})",\s*'
    r'vendor:"([^"\r\n]{1,200})",\s*sub:"([A-Z][A-Z0-9_]{0,15})",\s*'
    r'h:([0-9]+(?:\.[0-9]+)?),\s*stack:(true|false)\s*[,}]', re.MULTILINE)


def _literal_block(text: str, name: str) -> str:
    pattern = re.compile(r'\bconst\s+' + re.escape(name) + r'\s*=\s*\{(.*?)^\};',
                         re.DOTALL | re.MULTILINE)
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError(f"one recognizable literal {name} block required")
    return matches[0].group(1)


def _numeric_literal(text: str, pattern: str, name: str, maximum: float = 1000) -> float:
    matches = re.findall(pattern, text)
    if len(matches) != 1:
        raise ValueError(f"one recognizable {name} literal required")
    value = float(matches[0])
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(f"invalid {name} literal")
    return value


def extract(source: Path, base: dict, plan: str = "p2") -> dict:
    """Return a candidate; input HTML is reference text, never executable code."""
    if source.is_symlink() or not source.is_file() or not 1 <= source.stat().st_size <= MAX_SOURCE_BYTES:
        raise ValueError("regular bounded stack HTML required")
    raw = source.read_bytes()
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("stack HTML exceeds size limit")
    text = raw.decode("utf-8-sig")
    number = r"([0-9]+(?:\.[0-9]+)?)"
    frame_height = _numeric_literal(text, r"\bconst\s+FRAME_H\s*=\s*" + number + r"\s*;", "frame height")
    width = _numeric_literal(text, r"\bconst\s+w\s*=\s*" + number + r"\s*,\s*d\s*=", "board width")
    depth = _numeric_literal(text, r"\bconst\s+w\s*=\s*[0-9.]+\s*,\s*d\s*=\s*" + number + r"\s*;", "board depth")
    gap = _numeric_literal(text, r"\bconst\s+gap\s*=\s*" + number + r"\s*\+\s*explode\s*\*", "compact gap", 10)
    objects_text = _literal_block(text, "OBJ")
    objects = {}
    for match in OBJECT_ROW.finditer(objects_text):
        identifier, name, vendor, group, raw_height, _ = match.groups()
        height = float(raw_height)
        if identifier in objects or not math.isfinite(height) or not 0 < height <= 1000:
            raise ValueError("invalid or duplicate equipment literal")
        objects[identifier] = {"name": name, "vendor": vendor, "group": group, "height": height}
    # Refuse unsupported expressions instead of guessing their result. Every
    # top-level object line must have matched the known literal equipment form.
    declared = re.findall(r'^\s{2}([a-z][a-z0-9_]{0,31}):\{', objects_text, re.MULTILINE)
    if not objects or set(declared) != set(objects) or len(declared) != len(objects):
        raise ValueError("unsupported equipment literal; review source schema")
    if not re.fullmatch(r"pdr|p[1-9][0-9]?", plan):
        raise ValueError("invalid plan identifier")
    order_text = _literal_block(text, "ORDER")
    rows = re.findall(r'^\s*([a-z0-9]+):\[([^\]\r\n]*)\]\s*[,\r\n]', order_text + "\n", re.MULTILINE)
    plans = {}
    for key, value in rows:
        if key in plans or not re.fullmatch(r'"[a-z][a-z0-9_]{0,31}"(?:\s*,\s*"[a-z][a-z0-9_]{0,31}")*', value):
            raise ValueError("unsupported or duplicate stack order literal")
        plans[key] = re.findall(r'"([a-z][a-z0-9_]{0,31})"', value)
    if plan not in plans:
        raise ValueError("requested stack plan is absent")
    order = plans[plan]
    if not 1 <= len(order) <= 32 or len(set(order)) != len(order) or set(order) - objects.keys():
        raise ValueError("invalid stack order")
    if (not isinstance(base, dict) or base.get("schema") != "pado.equipment-layout.v1" or
            not isinstance(base.get("equipment"), list)):
        raise ValueError("reviewed equipment layout baseline required")
    result = copy.deepcopy(base)
    total = sum(objects[key]["height"] for key in order) + gap * (len(order) - 1)
    if total > 1000:
        raise ValueError("stack extent exceeds display safety bound")
    centers = {}
    top = total / 2
    for key in order:
        centers[key] = top - objects[key]["height"] / 2
        top -= objects[key]["height"] + gap
    equipment = []
    for entry in result["equipment"]:
        key = entry.get("source_id")
        if key not in centers:
            continue
        entry["anchor_body_mm"] = [0, 0, centers[key]]
        entry["size_body_mm"] = [width, depth, objects[key]["height"]]
        entry["anchor_kind"] = "BOARD_CENTER_PROXY"
        entry["anchor_status"] = "PROVISIONAL"
        equipment.append(entry)
    mapped = {entry["source_id"] for entry in equipment}
    for key in order:
        if key in mapped:
            continue
        obj = objects[key]
        equipment.append({"id": key, "source_id": key, "label": obj["name"], "group": obj["group"],
            "anchor_body_mm": [0, 0, centers[key]], "size_body_mm": [width, depth, obj["height"]],
            "anchor_kind": "BOARD_CENTER_PROXY", "anchor_status": "PROVISIONAL",
            "location_note": "새 스택 부품의 근사 중심입니다. 센서 위치와 TM 연결을 검토해야 합니다.",
            "geometry_role": "STACK_HOST", "telemetry_status": "UNMAPPED",
            "telemetry_note": "이전 부품의 계측을 자동으로 대신 연결하지 않습니다.",
            "callout": {"side": "right", "order": len(equipment)}, "readings": []})
    revision = re.search(r"(?<!\d)(\d{6})(?!\d)", source.stem)
    result["revision"] = f"stack-{revision.group(1) if revision else 'updated'}-{plan}-candidate"
    result["source"].update(file=source.name, sha256=hashlib.sha256(raw).hexdigest(),
        revision=revision.group(1) if revision else "UNSPECIFIED", plan=plan, provisional=True,
        selection_basis="명시적으로 선택한 스택 안의 compact 배치입니다. HTML JavaScript는 실행하지 않았습니다.")
    frame_size = list(result["stack"]["frame_size_source_mm"])
    frame_size[1] = frame_height
    result["stack"].update(order_positive_z_to_negative_z=order, compact_total_height_mm=total,
        compact_gap_mm=gap, board_width_source_mm=width, board_depth_source_mm=depth,
        frame_size_source_mm=frame_size)
    result["equipment"] = equipment
    result["unmapped_legacy_equipment"] = []
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--base", type=Path, default=ROOT / "config/equipment-layout.example.json")
    parser.add_argument("--plan", default="p2")
    parser.add_argument("--output", type=Path, required=True, help="new candidate file; never overwrite settings")
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise SystemExit("output must be a new candidate file")
    candidate = extract(args.source, json.loads(args.base.read_text(encoding="utf-8")), args.plan)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(candidate, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"Candidate: {args.output}; plan={args.plan}; equipment={len(candidate['equipment'])}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Stack layout extraction failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
