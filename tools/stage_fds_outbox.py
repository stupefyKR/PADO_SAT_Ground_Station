"""Validate one completed FDS run and stage unique N13/N15 SFTP file pairs.

The result is a local, immutable outbox directory. Transport authentication and
delivery are performed by the existing stage-6 SFTP client on Linux.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_MANIFEST = 4096
FIELDS = {"file", "type", "from", "to", "version", "created", "sha256"}
FILES = (
    ("contact_schedule.csv", "CONTACT_SCHEDULE", "mps", 131072,
     b"schema,station_id,band,pass_id,aos_utc,los_utc,peak_utc,"),
    ("pass_information.csv", "PASS_INFORMATION", "dgs-archive", 8 * 1024 * 1024,
     b"schema,station_id,band,pass_id,utc,azimuth_deg,"),
    ("orbit_ephemeris.csv", "ORBIT_EPHEMERIS", "dgs-archive", 8 * 1024 * 1024,
     b"schema,utc,frame,x_m,y_m,z_m,"),
)


def _unique_pairs(items: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate manifest field")
        result[key] = value
    return result


def _bytes(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise ValueError(f"regular bounded file required: {path}")
    with path.open("rb") as handle:
        data = handle.read(maximum + 1)
    if not 0 < len(data) <= maximum:
        raise ValueError(f"file changed or size outside limit: {path}")
    return data


def _pair(run: Path, name: str, kind: str, recipient: str,
          maximum: int, header: bytes) -> tuple[bytes, dict]:
    body = _bytes(run / name, maximum)
    if not body.startswith(header):
        raise ValueError(f"FDS output header mismatch: {name}")
    raw_manifest = _bytes(run / (name + ".manifest.json"), MAX_MANIFEST)
    try:
        metadata = json.loads(raw_manifest, object_pairs_hook=_unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid FDS manifest: {name}") from error
    if (not isinstance(metadata, dict) or set(metadata) != FIELDS or
            metadata["file"] != name or metadata["type"] != kind or
            metadata["from"] != "fds" or metadata["to"] != recipient or
            type(metadata["version"]) is not int or metadata["version"] != 1 or
            not isinstance(metadata["sha256"], str) or
            not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"]) or
            not isinstance(metadata["created"], str) or
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", metadata["created"])):
        raise ValueError(f"FDS manifest route or schema mismatch: {name}")
    try:
        datetime.strptime(metadata["created"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise ValueError(f"invalid FDS manifest timestamp: {name}") from error
    if hashlib.sha256(body).hexdigest() != metadata["sha256"]:
        raise ValueError(f"FDS output SHA-256 mismatch: {name}")
    return body, metadata


def stage(run: Path, outbox: Path) -> Path:
    if run.is_symlink() or not run.is_dir() or not re.fullmatch(r"fds-[A-Za-z0-9_-]{1,64}", run.name):
        raise ValueError("completed FDS run directory with a safe run ID required")
    properties = _bytes(run / "run.properties", 8192).decode("utf-8")
    fields = {}
    for line in properties.splitlines():
        if "=" not in line:
            raise ValueError("invalid FDS run metadata")
        key, value = line.split("=", 1)
        if key in fields:
            raise ValueError("duplicate FDS run metadata")
        fields[key] = value
    if fields.get("schema") != "fds.run.v1" or fields.get("quality") != "PRELIMINARY_NOT_FOR_OPERATIONS":
        raise ValueError("unsupported FDS run provenance")
    snapshots = [_pair(run, *spec) for spec in FILES]
    if outbox.is_symlink():
        raise ValueError("outbox root must not be a symlink")
    outbox.mkdir(parents=True, exist_ok=True)
    final = outbox / run.name
    if final.exists() or final.is_symlink():
        raise ValueError("FDS run already staged")
    with tempfile.TemporaryDirectory(prefix=".fds-outbox-", dir=outbox) as temporary:
        staging = Path(temporary)
        for (name, _, _, _, _), (body, metadata) in zip(FILES, snapshots):
            staged_name = name.removesuffix(".csv") + "-" + run.name + ".csv"
            staged_manifest = dict(metadata, file=staged_name)
            for path, content in ((staging / staged_name, body),
                                  (staging / (staged_name + ".manifest.json"),
                                   (json.dumps(staged_manifest, separators=(",", ":")) + "\n").encode("ascii"))):
                with path.open("xb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
        staging.rename(final)
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--outbox", type=Path, default=ROOT / "out/fds-outbox")
    args = parser.parse_args()
    print(stage(args.run, args.outbox))


if __name__ == "__main__":
    main()
