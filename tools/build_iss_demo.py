"""Publish a fictional ISS display trajectory from one fixed historical TLE.

The old TLE is propagated and refitted at each refresh. It is never a live ISS
position or an MPS contact schedule. Production input remains N05/N11.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__:
    from .publish_fds_view import publish as publish_view
else:
    from publish_fds_view import publish as publish_view


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "services/fds/demo/iss-fixed.tle"
POM = ROOT / "services/fds/pom.xml"
EXPECTED = (
    "1 25544U 98067A   26295.91886925  .00009886  00000-0  18726-3 0  9990",
    "2 25544  51.6334  28.0071 0006866 297.7993  62.2299 15.49005378591551",
)
_RUN = re.compile(r"fds-iss-display-(\d{8}T\d{6}Z-[0-9a-f]{8})")


def prune_display_runs(output_root: Path, *, keep: int = 8) -> int:
    """Bound completed ISS-only runs and their generated TLE/config sidecars."""
    if type(keep) is not int or keep < 1 or output_root.is_symlink() or \
            not output_root.is_dir():
        raise ValueError("invalid ISS display retention root")
    runs = sorted((path for path in output_root.iterdir() if not path.is_symlink() and
                   path.is_dir() and _RUN.fullmatch(path.name) and
                   (path / "run.properties").is_file()),
                  key=lambda path: path.name, reverse=True)
    removed = 0
    for run in runs[keep:]:
        stamp = _RUN.fullmatch(run.name).group(1)
        sidecars = [output_root / f"iss-display-{stamp}.tle",
                    output_root / f"iss-display-{stamp}.properties"]
        if run.parent.resolve() != output_root.resolve() or \
                any(path.parent.resolve() != output_root.resolve() or path.is_symlink()
                    for path in sidecars):
            raise ValueError("ISS display retention target escaped its root")
        shutil.rmtree(run)
        for path in sidecars:
            path.unlink(missing_ok=True)
        removed += 1
    return removed


def validate_fixture() -> None:
    lines = tuple(FIXTURE.read_text(encoding="ascii").splitlines())
    if lines != EXPECTED:
        raise ValueError("ISS demo TLE differs from the user-supplied fixed fixture")
    for line in lines:
        checksum = sum(int(char) if char.isdigit() else (char == "-") for char in line[:68]) % 10
        if len(line) != 69 or checksum != int(line[68]):
            raise ValueError("ISS demo TLE checksum invalid")


def config_text(start: datetime) -> str:
    stamp = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""# TEST_ONLY fictional extrapolation of a fixed historical ISS TLE.
# This is not a current ISS orbit or a real contact schedule.
expected_norad_id=25544
start_utc={stamp}
horizon_hours=72
ephemeris_step_s=60
profile_step_s=10
gravity_degree=8
gravity_order=8
station.yonsei.band=UHF
station.yonsei.latitude_deg=37.5624
station.yonsei.longitude_deg=126.9389
station.yonsei.altitude_m=0
station.yonsei.minimum_elevation_deg=10
station.yonsei.carrier_hz=435000000
station.yonsei.frequency_example=true
station.kari.band=S_BAND
station.kari.latitude_deg=36.3700
station.kari.longitude_deg=127.3600
station.kari.altitude_m=0
station.kari.minimum_elevation_deg=10
station.kari.carrier_hz=2200000000
station.kari.frequency_example=true
"""


def _maven_args(values: list[object]) -> str:
    result = []
    for value in values:
        token = str(value)
        if any(character in token for character in ('"', "\n", "\r")):
            raise ValueError("unsupported quote or newline in ISS display argument")
        result.append(f'"{token}"' if " " in token else token)
    return " ".join(result)


def _java(args: argparse.Namespace, main_class: str, options: list[object]) -> None:
    environment = os.environ.copy()
    if args.maven_repo:
        environment["MAVEN_OPTS"] = (
            environment.get("MAVEN_OPTS", "") +
            f" -Dmaven.repo.local={args.maven_repo.resolve()}"
        ).strip()
    subprocess.run([str(args.mvn), "-f", str(POM), "test-compile", "exec:java",
                    f"-Dexec.mainClass=kr.ac.khu.padosat.fds.{main_class}",
                    "-Dexec.classpathScope=test", "-Dexec.args=" + _maven_args(options)],
                   cwd=ROOT, env=environment, check=True)


def run_once(args: argparse.Namespace, *, now: datetime | None = None) -> Path:
    validate_fixture()
    if not args.orekit_data.is_dir():
        raise ValueError("--orekit-data must be an existing directory")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    args.output_root.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run = args.output_root / f"fds-iss-display-{stamp}"
    config = args.output_root / f"iss-display-{stamp}.properties"
    tle_copy = args.output_root / f"iss-display-{stamp}.tle"
    config.write_text(config_text(now), encoding="utf-8")
    # TLE's day fraction is rounded; use a two-second margin before FDS start.
    tle_epoch = now - timedelta(seconds=2)
    _java(args, "IssDisplayTleMain", ["--orekit-data", args.orekit_data.resolve(),
                                       "--reference", FIXTURE.resolve(),
                                       "--epoch", tle_epoch.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                       "--out", tle_copy.resolve()])
    lines = tle_copy.read_text(encoding="ascii").splitlines()
    if len(lines) != 2 or any(len(line) != 69 or line[2:7] != "25544"
                              for line in lines):
        raise ValueError("ISS display refit returned an unexpected TLE")
    _java(args, "FdsMain", ["--config", config.resolve(), "--orekit-data",
                             args.orekit_data.resolve(), "--tle", tle_copy.resolve(),
                             "--out", run.resolve()])
    published = publish_view(run, args.view_dir)
    print(f"TEST_ONLY fictional ISS fixed-reference extrapolation: {published}", flush=True)
    try:
        removed = prune_display_runs(args.output_root)
        if removed:
            print(f"TEST_ONLY removed {removed} expired ISS display runs", flush=True)
    except (OSError, ValueError) as error:
        print(f"TEST_ONLY ISS retention warning: {error}", file=sys.stderr, flush=True)
    return published


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-only", action="store_true", required=True)
    parser.add_argument("--orekit-data", required=True, type=Path)
    parser.add_argument("--mvn", default="mvn", type=Path)
    parser.add_argument("--output-root", default=ROOT / "out/iss-display", type=Path)
    parser.add_argument("--view-dir", default=ROOT / "out/fds-iss-view", type=Path)
    parser.add_argument("--maven-repo", type=Path, help="optional local Maven cache")
    parser.add_argument("--watch", action="store_true", help="repeat the fixed-TLE test calculation")
    parser.add_argument("--refresh-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.refresh_seconds < 60:
        parser.error("--refresh-seconds must be at least 60")
    while True:
        try:
            run_once(args)
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            if not args.watch:
                parser.exit(2, f"ISS demo failed: {error}\n")
            print(f"ISS demo waiting: {error}", file=sys.stderr, flush=True)
        if not args.watch:
            break
        try:
            time.sleep(args.refresh_seconds)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
