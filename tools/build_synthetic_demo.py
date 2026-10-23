"""Publish a test-only PADO-SAT orbit and accept simulated GNSS for FDS.

The immutable synthetic TLE model supplies the satellite emulator's truth
view.  The operator view is a separate FDS run: it selects validated N11 GNSS
when fresh and uses the synthetic TLE when N11 is absent or 72 h old.  No
simulated input is sent to the operational N05/N11 exchange.
"""

from __future__ import annotations

import argparse
import hashlib
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
    from .publish_mps_schedule import publish as publish_schedule
    from .receive_fds_inputs import ingest_inbox
    from .run_fds_pipeline import current_pair, verified_pair
else:
    from publish_fds_view import publish as publish_view
    from publish_mps_schedule import publish as publish_schedule
    from receive_fds_inputs import ingest_inbox
    from run_fds_pipeline import current_pair, verified_pair


ROOT = Path(__file__).resolve().parents[1]
POM = ROOT / "services/fds/pom.xml"
_GROUP = re.compile(r"fds-synthetic-truth-(\d{8}T\d{6}Z-[0-9a-f]{8})")
_N11_DIGEST = re.compile(r"[0-9a-f]{64}")


def prune_completed_runs(output_root: Path, schedule_root: Path, *, keep: int = 8) -> int:
    """Keep a bounded number of *this simulator's* complete FDS runs."""
    if type(keep) is not int or keep < 1 or output_root.is_symlink() or \
            not output_root.is_dir() or schedule_root.is_symlink():
        raise ValueError("invalid synthetic FDS retention roots")
    runs = sorted((path for path in output_root.iterdir() if not path.is_symlink() and
                   path.is_dir() and _GROUP.fullmatch(path.name) and
                   (path / "run.properties").is_file()),
                  key=lambda path: path.name, reverse=True)
    deleted = 0
    for truth in runs[keep:]:
        group = _GROUP.fullmatch(truth.name).group(1)
        paths = [truth, output_root / f"fds-synthetic-operator-{group}",
                 output_root / f"synthetic-{group}.tle",
                 output_root / f"synthetic-{group}.properties",
                 output_root / f"synthetic-operator-{group}.properties"]
        if schedule_root.is_dir():
            paths.extend((schedule_root / f"fds-synthetic-truth-{group}",
                          schedule_root / f"fds-synthetic-operator-{group}"))
        for path in paths:
            parent = schedule_root if path.parent == schedule_root else output_root
            if path.parent.resolve() != parent.resolve() or path.is_symlink():
                raise ValueError("synthetic retention target escaped its root")
            if path.is_dir():
                shutil.rmtree(path)
            elif path.is_file():
                path.unlink()
        deleted += 1
    return deleted


def prune_synthetic_n11_snapshots(root: Path, *, keep: int = 288) -> int:
    """Bound verified simulator N11 snapshots, retaining the atomic current one."""
    if type(keep) is not int or keep < 1 or root.is_symlink() or not root.is_dir():
        raise ValueError("invalid synthetic N11 retention root")
    body, manifest = current_pair(root, "gnss")
    if body is None or manifest is None:
        return 0
    current = body.parent
    if current.parent != root or not _N11_DIGEST.fullmatch(current.name):
        raise ValueError("invalid synthetic N11 current snapshot")

    snapshots = []
    for path in root.iterdir():
        if path.is_symlink() or not path.is_dir() or not _N11_DIGEST.fullmatch(path.name):
            continue
        entries = list(path.iterdir())
        manifests = [entry for entry in entries if entry.name.endswith(".manifest.json")]
        if len(entries) != 2 or len(manifests) != 1:
            continue
        pair_manifest = manifests[0]
        pair_body = path / pair_manifest.name.removesuffix(".manifest.json")
        try:
            payload = verified_pair(pair_body, pair_manifest, "gnss")
            metadata = pair_manifest.read_bytes()
        except (OSError, ValueError):
            continue
        if payload is None or hashlib.sha256(b"gnss\0" + payload + b"\0" +
                                              metadata).hexdigest() != path.name:
            continue
        snapshots.append(path)
    if current not in snapshots:
        raise ValueError("synthetic N11 current snapshot is not verified")
    recent = sorted((path for path in snapshots if path != current),
                    key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    removed = 0
    for path in recent[keep - 1:]:
        if path.parent != root or path.is_symlink():
            raise ValueError("synthetic N11 retention target escaped its root")
        shutil.rmtree(path)
        removed += 1
    return removed


def config_text(start: datetime) -> str:
    stamp = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""# TEST_ONLY synthetic PADO-SAT. Site positions and RF are examples.
expected_norad_id=99999
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


def _utc_now_second() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _maven_args(values: list[object]) -> str:
    result = []
    for value in values:
        token = str(value)
        if any(character in token for character in ('"', "\n", "\r")):
            raise ValueError("unsupported quote or newline in FDS argument")
        result.append(f'"{token}"' if " " in token else token)
    return " ".join(result)


def _java(args: argparse.Namespace, main_class: str, options: list[object]) -> None:
    environment = os.environ.copy()
    if args.maven_repo:
        environment["MAVEN_OPTS"] = (environment.get("MAVEN_OPTS", "") +
                f" -Dmaven.repo.local={args.maven_repo.resolve()}").strip()
    subprocess.run([str(args.mvn), "-f", str(POM), "test-compile", "exec:java",
                    f"-Dexec.mainClass=kr.ac.khu.padosat.fds.{main_class}",
                    "-Dexec.classpathScope=test",
                    "-Dexec.args=" + _maven_args(options)],
                   cwd=ROOT, env=environment, check=True)


def _latest_gnss(args: argparse.Namespace) -> Path | None:
    if args.n11_inbox is None or not args.n11_inbox.is_dir():
        return None
    ingress = args.output_root / "synthetic-n11-current"
    committed = ingest_inbox("gnss", args.n11_inbox, ingress)
    if committed is None and not (ingress / "current.txt").exists():
        return None
    body, manifest = current_pair(ingress, "gnss")
    if verified_pair(body, manifest, "gnss") is None:
        return None
    prune_synthetic_n11_snapshots(ingress)
    return body


def run_once(args: argparse.Namespace, *, now: datetime | None = None) -> tuple[Path, str]:
    if not args.orekit_data.is_dir():
        raise ValueError("--orekit-data must be an existing directory")
    now = (now or _utc_now_second()).astimezone(timezone.utc).replace(microsecond=0)
    if now.year < 2026 or now.year > 2100:
        raise ValueError("synthetic TLE epoch outside supported test years")
    args.output_root.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    tle = args.output_root / f"synthetic-{stamp}.tle"
    config = args.output_root / f"synthetic-{stamp}.properties"
    config.write_text(config_text(now), encoding="utf-8")
    # TLE epoch has eight decimal places in days and can round a few hundred
    # microseconds into the future. Keep a two-second margin before FDS start.
    tle_epoch = now - timedelta(seconds=2)
    _java(args, "SyntheticTleMain", ["--orekit-data", args.orekit_data.resolve(),
                                      "--epoch", tle_epoch.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                      "--out", tle.resolve()])
    lines = tle.read_text(encoding="ascii").splitlines()
    if len(lines) != 2 or any(len(line) != 69 for line in lines) or \
            any(line[2:7] != "99999" for line in lines):
        raise ValueError("synthetic TLE generator returned an unexpected satellite")
    truth_run = args.output_root / f"fds-synthetic-truth-{stamp}"
    fds_args: list[object] = ["--config", config.resolve(), "--orekit-data",
                              args.orekit_data.resolve(), "--tle", tle.resolve(),
                              "--out", truth_run.resolve()]
    _java(args, "FdsMain", fds_args)
    truth = publish_view(truth_run, args.truth_view_dir)
    # The emulator sees only the TLE-derived truth. GNSS-fed operator output
    # cannot feed itself through this path on later refreshes.
    gnss = _latest_gnss(args)
    selected_run = truth_run
    if gnss is not None:
        # The truth propagation may take minutes. N11 can arrive during it,
        # after the truth start. Plan the operator run from the time N11 was
        # selected so that a fresh GNSS epoch is not rejected as future data.
        operator_start = max(now, _utc_now_second())
        operator_config = args.output_root / f"synthetic-operator-{stamp}.properties"
        operator_config.write_text(config_text(operator_start), encoding="utf-8")
        selected_run = args.output_root / f"fds-synthetic-operator-{stamp}"
        _java(args, "FdsMain", ["--config", operator_config.resolve(), "--orekit-data",
                                args.orekit_data.resolve(), "--tle", tle.resolve(),
                                "--gnss", gnss.resolve(), "--out", selected_run.resolve()])
    schedule = publish_schedule(selected_run, args.mps_schedule_dir)
    view = publish_view(selected_run, args.view_dir)
    metadata = dict(line.split("=", 1) for line in
                    (selected_run / "run.properties").read_text(encoding="ascii").splitlines()
                    if "=" in line)
    print(f"TEST_ONLY synthetic truth={truth} operator={view} "
          f"source={metadata['source']} schedule={schedule}", flush=True)
    try:
        removed = prune_completed_runs(args.output_root, args.mps_schedule_dir)
        if removed:
            print(f"TEST_ONLY removed {removed} expired synthetic FDS run groups", flush=True)
    except (OSError, ValueError) as error:
        print(f"TEST_ONLY FDS retention warning: {error}", file=sys.stderr, flush=True)
    return view, metadata["source"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-only", action="store_true", required=True)
    parser.add_argument("--orekit-data", required=True, type=Path)
    parser.add_argument("--mvn", default="mvn", type=Path)
    parser.add_argument("--maven-repo", type=Path)
    parser.add_argument("--output-root", default=ROOT / "out", type=Path)
    parser.add_argument("--truth-view-dir", default=ROOT / "out/fds-truth", type=Path)
    parser.add_argument("--view-dir", default=ROOT / "out/fds-view", type=Path)
    parser.add_argument("--mps-schedule-dir", default=ROOT / "out/mps-schedules", type=Path)
    parser.add_argument("--n11-inbox", type=Path)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--refresh-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.refresh_seconds < 60:
        parser.error("--refresh-seconds must be at least 60")
    while True:
        try:
            run_once(args)
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            if not args.watch:
                parser.exit(2, f"synthetic FDS demo failed: {error}\n")
            print(f"synthetic FDS demo waiting: {error}", file=sys.stderr, flush=True)
        if not args.watch:
            break
        try:
            time.sleep(args.refresh_seconds)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
