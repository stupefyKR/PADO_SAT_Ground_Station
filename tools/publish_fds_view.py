"""Atomically publish one validated FDS orbit view for the read-only portal."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


MAX_BYTES = 4 * 1024 * 1024


def timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("UTC timestamp required")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def publish(run: Path, destination: Path) -> Path:
    source = run / "orbit_view.json"
    metadata = run / "run.properties"
    if not source.is_file() or not metadata.is_file() or source.stat().st_size > MAX_BYTES:
        raise ValueError("complete FDS output directory required (maximum viewer size 4 MB)")
    properties = dict(line.split("=", 1) for line in metadata.read_text(encoding="utf-8").splitlines()
                      if "=" in line)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if (payload.get("schema") != "fds.orbit-view.v1" or
            payload.get("quality") != "PRELIMINARY_NOT_FOR_OPERATIONS" or
            properties.get("schema") != "fds.run.v1" or
            payload.get("input_sha256") != properties.get("input_sha256") or
            payload.get("source") != properties.get("source") or
            str(payload.get("norad_id")) != properties.get("expected_norad_id") or
            sorted(station.get("id") for station in payload.get("stations", [])) != ["kari", "yonsei"] or
            not isinstance(payload.get("samples"), list) or len(payload["samples"]) < 2):
        raise ValueError("FDS viewer provenance or station set does not match the run")
    start = timestamp(payload["start_utc"])
    end = timestamp(payload["end_utc"])
    epoch = timestamp(payload["input_epoch_utc"])
    if (end <= start or end - start > timedelta(hours=72) or
            epoch > start or start - epoch >= timedelta(hours=72) or
            timestamp(payload["samples"][0][0]) != start or
            timestamp(payload["samples"][-1][0]) != end):
        raise ValueError("FDS viewer prediction range is invalid")
    now = datetime.now(timezone.utc)
    if not (start <= now <= end and now - epoch < timedelta(hours=72)):
        raise ValueError("prediction does not cover now with an input younger than 72 hours")
    destination.mkdir(parents=True, exist_ok=True)
    final = destination / "orbit_view.json"
    if source.resolve() == final.resolve():
        raise ValueError("published file must be separate from the original FDS run")
    scratch: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination, prefix=".orbit-view-", suffix=".tmp",
                                         delete=False) as handle:
            scratch = Path(handle.name)
            handle.write(source.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(scratch, 0o644)
        os.replace(scratch, final)
    finally:
        if scratch is not None:
            scratch.unlink(missing_ok=True)
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="completed FDS output directory")
    parser.add_argument("destination", type=Path, help="read-only portal bind source directory")
    args = parser.parse_args()
    print(publish(args.run, args.destination))


if __name__ == "__main__":
    main()
