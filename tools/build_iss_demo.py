"""Run the offline FDS with the user-supplied fixed ISS TLE for the orbit UI demo.

This is a development fixture only. Production FDS input remains N05 TLE / N11 GNSS.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from .publish_fds_view import publish as publish_view
    from .publish_mps_schedule import publish as publish_schedule
else:
    from publish_fds_view import publish as publish_view
    from publish_mps_schedule import publish as publish_schedule


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "services/fds/demo/iss-fixed.tle"
POM = ROOT / "services/fds/pom.xml"
EXPECTED = (
    "1 25544U 98067A   26295.91886925  .00009886  00000-0  18726-3 0  9990",
    "2 25544  51.6334  28.0071 0006866 297.7993  62.2299 15.49005378591551",
)


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
    return f"""# ISS fixed-TLE display demo only. Coordinates, masks and RF are provisional.
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


def run_once(args: argparse.Namespace, *, now: datetime | None = None) -> tuple[Path, Path]:
    validate_fixture()
    if not args.orekit_data.is_dir():
        raise ValueError("--orekit-data must be an existing directory")
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    run = ROOT / "out" / f"fds-iss-demo-{now.strftime('%Y%m%dT%H%M%SZ')}"
    config = run.parent / f"{run.name}.properties"
    tle_copy = run.parent / f"{run.name}.tle"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(config_text(now), encoding="utf-8")
    tle_copy.write_bytes(("\n".join(EXPECTED) + "\n").encode("ascii"))
    environment = os.environ.copy()
    if args.maven_repo:
        environment["MAVEN_OPTS"] = (
            environment.get("MAVEN_OPTS", "") +
            f" -Dmaven.repo.local={args.maven_repo.resolve()}"
        ).strip()
    fds_args = (f"--config {config} --orekit-data {args.orekit_data.resolve()} "
                f"--tle {tle_copy} --out {run}")
    subprocess.run([str(args.mvn), "-f", str(POM), "test-compile", "exec:java",
                    "-Dexec.mainClass=kr.ac.khu.padosat.fds.FdsMain",
                    "-Dexec.classpathScope=test", f"-Dexec.args={fds_args}"],
                   cwd=ROOT, env=environment, check=True)
    # Both public views are derived from one immutable FDS run. Consumers reject
    # a transient mismatch while the two pointers are swapped.
    schedule = publish_schedule(run, args.mps_schedule_dir)
    published = publish_view(run, args.view_dir)
    print(f"ISS demo input: {FIXTURE}")
    print(f"FDS output: {run}")
    print(f"Portal view: {published}")
    print(f"MPS N13 schedule: {schedule}")
    return published, schedule


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orekit-data", required=True, type=Path)
    parser.add_argument("--mvn", default="mvn", type=Path)
    parser.add_argument("--view-dir", default=ROOT / "out/fds-view", type=Path)
    parser.add_argument("--mps-schedule-dir", default=ROOT / "out/mps-schedules", type=Path)
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
