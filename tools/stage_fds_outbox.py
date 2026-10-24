"""Validate one completed FDS run and stage route-specific file pairs.

N13/N15 go to the transport outbox. N08 drafts go to a separate local-only
outbox; no DGS-GS upload or antenna actuation is implemented here.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
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
N08_FILES = (
    ("pass_request.csv", "PASS_REQUEST", "dgs-gs", 131072,
     b"schema,station_id,band,pass_id,aos_utc,los_utc,minimum_elevation_deg,clipped_start,clipped_end,quality,station_parameters_status\n"),
    ("antenna_pointing.csv", "ANTENNA_POINTING", "dgs-gs", 8 * 1024 * 1024,
     b"schema,station_id,band,pass_id,utc,azimuth_deg,elevation_deg,quality,station_parameters_status\n"),
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


def _n08_rows(body: bytes) -> list[list[str]]:
    try:
        rows = list(csv.reader(io.StringIO(body.decode("utf-8")), strict=True))
    except (UnicodeError, csv.Error) as error:
        raise ValueError("invalid N08 CSV") from error
    if len(rows) > 50000 or any(not row for row in rows):
        raise ValueError("invalid N08 row count")
    return rows[1:]


def _n08_utc(value: str) -> datetime:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", value):
        raise ValueError("invalid N08 UTC")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as error:
        raise ValueError("invalid N08 UTC") from error


def _n08_station(station: str, band: str, pass_id: str) -> None:
    if ((station, band) not in (("yonsei", "UHF"), ("kari", "S_BAND")) or
            not re.fullmatch(re.escape(station) + r"-\d{3}", pass_id)):
        raise ValueError("unknown N08 station or PASS")


def _n08_quality(row: list[str]) -> None:
    if row[-2:] != ["PRELIMINARY_NOT_FOR_OPERATIONS", "UNVERIFIED_TEST_VALUES"]:
        raise ValueError("N08 draft quality or station provenance missing")


def _validate_n08(run: Path, request_body: bytes, pointing_body: bytes,
                  properties: dict[str, str]) -> None:
    """Check that local N08 drafts track N13 windows and stay in physical bounds."""
    schedule_body, _ = _pair(run, *FILES[0])
    profile_body, _ = _pair(run, *FILES[1])
    schedule = _n08_rows(schedule_body)
    profile = _n08_rows(profile_body)
    requests = _n08_rows(request_body)
    pointing = _n08_rows(pointing_body)
    if len(requests) != len(schedule):
        raise ValueError("N08 requests differ from N13 PASS count")
    if len(pointing) != len(profile):
        raise ValueError("N08 pointing differs from N15 profile count")
    try:
        cadence = int(properties["profile_step_s"])
    except (KeyError, ValueError) as error:
        raise ValueError("N08 run profile cadence missing") from error
    if not 1 <= cadence <= 60:
        raise ValueError("N08 run profile cadence outside allowed range")
    windows = {}
    for request, contact in zip(requests, schedule):
        if len(request) != 11 or len(contact) != 11 or \
                request[0] != "fds.pass-request.draft.v1":
            raise ValueError("invalid N08 request schema")
        _n08_station(*request[1:4])
        _n08_quality(request)
        if (request[1:6] != contact[1:6] or request[6] != contact[8] or
                request[7:9] != contact[9:11] or
                request[7] not in ("true", "false") or
                request[8] not in ("true", "false")):
            raise ValueError("N08 request differs from N13 window")
        try:
            minimum = float(request[6])
        except ValueError as error:
            raise ValueError("invalid N08 minimum elevation") from error
        aos, los = _n08_utc(request[4]), _n08_utc(request[5])
        if not math.isfinite(minimum) or not 0 <= minimum <= 90 or aos >= los:
            raise ValueError("invalid N08 PASS window")
        key = (request[1], request[3])
        if key in windows:
            raise ValueError("duplicate N08 PASS")
        windows[key] = (aos, los)
    samples = {key: [] for key in windows}
    for sample, reference in zip(pointing, profile):
        if len(sample) != 9 or sample[0] != "fds.antenna-pointing.draft.v1":
            raise ValueError("invalid N08 pointing schema")
        _n08_station(*sample[1:4])
        _n08_quality(sample)
        key = (sample[1], sample[3])
        if key not in windows:
            raise ValueError("N08 pointing outside N13 PASS list")
        when = _n08_utc(sample[4])
        aos, los = windows[key]
        try:
            azimuth, elevation = float(sample[5]), float(sample[6])
        except ValueError as error:
            raise ValueError("invalid N08 pointing angle") from error
        if (not math.isfinite(azimuth) or not math.isfinite(elevation) or
                not 0 <= azimuth <= 360 or not -90 <= elevation <= 90 or
                not aos <= when <= los):
            raise ValueError("N08 pointing outside physical or PASS bounds")
        if (len(reference) != 13 or reference[0] != "fds.pass-information.v1" or
                sample[1:7] != reference[1:7]):
            raise ValueError("N08 pointing differs from N15 calculated profile")
        samples[key].append(when)
    for key, times in samples.items():
        if (not times or times[0] != windows[key][0] or
                times[-1] != windows[key][1] or times != sorted(set(times))):
            raise ValueError("N08 pointing misses PASS endpoints or is unordered")
        for index, (previous, current) in enumerate(zip(times, times[1:])):
            elapsed = (current - previous).total_seconds()
            last_gap = index == len(times) - 2
            if (not 0 < elapsed <= cadence + 0.002 or
                    (not last_gap and abs(elapsed - cadence) > 0.002)):
                raise ValueError("N08 pointing cadence differs from FDS profile step")


def ensure_separate_outboxes(n08: Path, transport: Path) -> None:
    """Reject overlapping draft and transport roots, including nested paths."""
    local = n08.resolve()
    sending = transport.resolve()
    if local.is_relative_to(sending) or sending.is_relative_to(local):
        raise ValueError("N08 drafts require a separate local-only outbox")


def _stage(run: Path, outbox: Path, specs: tuple) -> Path:
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
    snapshots = [_pair(run, *spec) for spec in specs]
    if specs is N08_FILES:
        _validate_n08(run, snapshots[0][0], snapshots[1][0], fields)
    if outbox.is_symlink():
        raise ValueError("outbox root must not be a symlink")
    outbox.mkdir(parents=True, exist_ok=True)
    final = outbox / run.name
    if final.exists() or final.is_symlink():
        raise ValueError("FDS run already staged")
    with tempfile.TemporaryDirectory(prefix=".fds-outbox-", dir=outbox) as temporary:
        staging = Path(temporary)
        for (name, _, _, _, _), (body, metadata) in zip(specs, snapshots):
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


def stage(run: Path, outbox: Path) -> Path:
    """Stage N13/N15 pairs for the existing pinned-key SFTP sender."""
    return _stage(run, outbox, FILES)


def stage_n08(run: Path, outbox: Path) -> Path:
    """Stage N08 draft pairs locally without enabling a DGS-GS route."""
    ensure_separate_outboxes(outbox, ROOT / "out/fds-outbox")
    return _stage(run, outbox, N08_FILES)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--outbox", type=Path)
    parser.add_argument("--n08-only", action="store_true",
                        help="stage N08 drafts in a separate local-only outbox")
    args = parser.parse_args()
    default_transport = ROOT / "out/fds-outbox"
    outbox = args.outbox or (ROOT / "out/fds-n08-outbox" if args.n08_only else default_transport)
    if args.n08_only:
        ensure_separate_outboxes(outbox, default_transport)
    print((stage_n08 if args.n08_only else stage)(args.run, outbox))


if __name__ == "__main__":
    main()
