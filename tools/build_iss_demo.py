"""Run the offline FDS with the user-supplied fixed ISS TLE for the orbit UI demo.

This is a development fixture only. Production FDS input remains N05 TLE / N11 GNSS.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


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
spacecraft_mass_kg=10
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orekit-data", required=True, type=Path)
    parser.add_argument("--mvn", default="mvn", type=Path)
    parser.add_argument("--view-dir", default=ROOT / "out/fds-view", type=Path)
    parser.add_argument("--maven-repo", type=Path, help="optional local Maven cache")
    args = parser.parse_args()
    validate_fixture()
    if not args.orekit_data.is_dir():
        parser.error("--orekit-data must be an existing directory")
    now = datetime.now(timezone.utc).replace(microsecond=0)
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
    spec = importlib.util.spec_from_file_location("publish_fds_view", ROOT / "tools/publish_fds_view.py")
    assert spec and spec.loader
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    published = publisher.publish(run, args.view_dir)
    print(f"ISS demo input: {FIXTURE}")
    print(f"FDS output: {run}")
    print(f"Portal view: {published}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"ISS demo failed: {error}", file=sys.stderr)
        raise SystemExit(2) from error
