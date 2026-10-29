"""Atomically publish one validated FDS orbit view for the read-only portal."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__:
    from .orbit_history import read_reconstruction, record_history
else:
    from orbit_history import read_reconstruction, record_history


MAX_BYTES = 4 * 1024 * 1024


def timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("UTC timestamp required")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _reconstruction(run: Path, norad: int, now: datetime) -> bytes | None:
    source = run / "orbit_reconstruction.json"
    if not source.exists() and not source.is_symlink():
        return None
    if source.is_symlink() or not source.is_file() or not 1 <= source.stat().st_size <= 2*1024*1024:
        raise ValueError("regular bounded FDS reconstruction required")
    data = source.read_bytes()
    payload = json.loads(data.decode("utf-8"))
    validated = read_reconstruction(source,
        start_utc_s=int(timestamp(payload["start_utc"]).timestamp()),
        end_utc_s=int(timestamp(payload["end_utc"]).timestamp()),
        norad_id=norad,now_s=int(now.timestamp()))
    if validated is None:
        raise ValueError("FDS reconstruction disappeared during validation")
    return data


def _atomic_publish(destination: Path, final: Path, data: bytes) -> None:
    scratch: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination, prefix=".orbit-view-", suffix=".tmp",
                                         delete=False) as handle:
            scratch = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(scratch, 0o644)
        os.replace(scratch, final)
    finally:
        if scratch is not None:
            scratch.unlink(missing_ok=True)


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
    reconstruction = _reconstruction(run,payload["norad_id"],now)
    # Keep historical predictions before replacing the current-only view. The
    # portal reads this SQLite file through the existing read-only FDS mount.
    record_history(payload, destination, now_s=int(now.timestamp()))
    if reconstruction is not None:
        # It shares the existing read-only FDS mount but remains a separate,
        # explicitly retrospective product. It never enters forecast history.
        _atomic_publish(destination,destination / "orbit_reconstruction.json",reconstruction)
    _atomic_publish(destination,final,source.read_bytes())
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="completed FDS output directory")
    parser.add_argument("destination", type=Path, help="read-only portal bind source directory")
    args = parser.parse_args()
    print(publish(args.run, args.destination))


if __name__ == "__main__":
    main()
