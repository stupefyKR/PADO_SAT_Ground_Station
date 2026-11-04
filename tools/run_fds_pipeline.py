"""Validate staged N05/N11 file pairs, run FDS, and publish a current orbit view.

The SFTP transport authenticates its peer separately. This runner does not fetch
external data, decode raw GNSS TM, or replace the fixed ISS screen fixture.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from .publish_fds_view import publish
    from .publish_mps_schedule import publish as publish_mps_schedule
    from .stage_fds_outbox import ensure_separate_outboxes, stage, stage_n08
else:
    from publish_fds_view import publish
    from publish_mps_schedule import publish as publish_mps_schedule
    from stage_fds_outbox import ensure_separate_outboxes, stage, stage_n08


ROOT = Path(__file__).resolve().parents[1]
POM = ROOT / "services/fds/pom.xml"
FIELDS = {"file", "type", "from", "to", "version", "created", "sha256"}
ROUTES = {
    "tle": ("NORAD_TLE", "dgs-net", "fds", 256, "n05.tle"),
    "gnss": ("GNSS", "ros", "fds", 4096, "n11.properties"),
}


def _unique_pairs(items: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate manifest field")
        result[key] = value
    return result


def _regular_bytes(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"regular, non-symlink file required: {path}")
    if path.stat().st_size < 1 or path.stat().st_size > maximum:
        raise ValueError(f"file size outside allowed range: {path}")
    with path.open("rb") as handle:
        data = handle.read(maximum + 1)
    if not data or len(data) > maximum:
        raise ValueError(f"file size outside allowed range: {path}")
    return data


def verified_pair(body: Path | None, manifest: Path | None, route: str) -> bytes | None:
    """Take a bounded byte snapshot after strict route and SHA-256 checks."""
    if body is None and manifest is None:
        return None
    if body is None or manifest is None:
        raise ValueError(f"{route}: body and manifest paths must be supplied together")
    if body.is_symlink() or manifest.is_symlink():
        raise ValueError(f"{route}: symlink input is forbidden")
    if not body.exists() and not manifest.exists():
        return None  # A configured watch inbox has not received this route yet.
    if not body.exists() or not manifest.exists():
        raise ValueError(f"{route}: incomplete file pair")
    kind, sender, recipient, maximum, _ = ROUTES[route]
    raw_manifest = _regular_bytes(manifest, 4096)
    try:
        metadata = json.loads(raw_manifest, object_pairs_hook=_unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{route}: invalid manifest JSON") from error
    if not isinstance(metadata, dict) or set(metadata) != FIELDS or metadata["type"] != kind or \
            metadata["from"] != sender or metadata["to"] != recipient or \
            metadata["file"] != body.name or not re.fullmatch(r"[A-Za-z0-9._-]{1,255}", body.name) or \
            body.name in (".", "..") or type(metadata["version"]) is not int or \
            not (1 <= metadata["version"] <= 4294967295) or \
            not isinstance(metadata["sha256"], str) or \
            not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"]) or \
            not isinstance(metadata["created"], str) or \
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", metadata["created"]):
        raise ValueError(f"{route}: manifest route or schema invalid")
    try:
        datetime.strptime(metadata["created"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise ValueError(f"{route}: manifest timestamp invalid") from error
    payload = _regular_bytes(body, maximum)
    if hashlib.sha256(payload).hexdigest() != metadata["sha256"]:
        raise ValueError(f"{route}: body SHA-256 mismatch")
    return payload


def current_pair(root: Path | None, route: str) -> tuple[Path | None, Path | None]:
    """Resolve one atomic ingress pointer, rejecting a blocked or broken snapshot."""
    if root is None:
        return None, None
    if not root.exists() and not root.is_symlink():
        return None, None
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"{route}: ingress root must be a directory")
    if (root / "blocked.txt").exists() or (root / "blocked.txt").is_symlink():
        raise ValueError(f"{route}: ingress blocked by invalid committed input")
    marker = root / "current.txt"
    if not marker.exists():
        return None, None
    if marker.is_symlink():
        raise ValueError(f"{route}: current marker must not be a symlink")
    snapshot_id = _regular_bytes(marker, 80).decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot_id):
        raise ValueError(f"{route}: invalid current snapshot ID")
    snapshot = root / snapshot_id
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise ValueError(f"{route}: current snapshot missing")
    names = [path.name for path in snapshot.iterdir() if path.name.endswith(".manifest.json")]
    if len(names) != 1:
        raise ValueError(f"{route}: snapshot must contain one manifest")
    manifest = snapshot / names[0]
    body = snapshot / names[0].removesuffix(".manifest.json")
    return body, manifest


def input_pair(args: argparse.Namespace, route: str) -> tuple[Path | None, Path | None]:
    root = getattr(args, f"{route}_current_root", None)
    if root is not None:
        return current_pair(root, route)
    return getattr(args, f"{route}_body"), getattr(args, f"{route}_manifest")


def current_config(template: Path, instant: datetime) -> str:
    source = _regular_bytes(template, 8192).decode("utf-8").replace("\r\n", "\n")
    if source.count("start_utc=") != 1 or \
            len(re.findall(r"(?m)^start_utc=[^\r\n]*$", source)) != 1:
        raise ValueError("configuration requires one start_utc field")
    stamp = instant.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return re.sub(r"(?m)^start_utc=[^\r\n]*$", f"start_utc={stamp}", source, count=1)


def _maven_token(value: str) -> str:
    if '"' in value or "\n" in value or "\r" in value:
        raise ValueError("unsupported quote or newline in FDS argument")
    return f'"{value}"' if re.search(r"\s", value) else value


def run_cycle(args: argparse.Namespace, *, now: datetime | None = None) -> tuple[Path, str]:
    now = now or datetime.now(timezone.utc)
    ensure_separate_outboxes(args.n08_outbox_dir, args.outbox_dir)
    ensure_separate_outboxes(args.n08_outbox_dir, ROOT / "out/fds-outbox")
    if not args.orekit_data.is_dir():
        raise ValueError("Orekit data directory required")
    tle = verified_pair(*input_pair(args, "tle"), "tle")
    gnss = verified_pair(*input_pair(args, "gnss"), "gnss")
    if tle is None and gnss is None:
        raise ValueError("no complete N05 TLE or N11 normalized GNSS pair")
    config = current_config(args.config, now)
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run = output_root / (now.strftime("fds-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    with tempfile.TemporaryDirectory(prefix="fds-input-", dir=output_root) as scratch_name:
        scratch = Path(scratch_name)
        configuration = scratch / "config.properties"
        configuration.write_text(config, encoding="utf-8")
        fds_args = ["--config", str(configuration), "--orekit-data", str(args.orekit_data.resolve())]
        for route, payload in (("tle", tle), ("gnss", gnss)):
            if payload is not None:
                snapshot = scratch / ROUTES[route][4]
                snapshot.write_bytes(payload)
                fds_args.extend(("--" + route, str(snapshot)))
        fds_args.extend(("--out", str(run)))
        environment = os.environ.copy()
        if args.maven_repo:
            environment["MAVEN_OPTS"] = (environment.get("MAVEN_OPTS", "") +
                                         f" -Dmaven.repo.local={args.maven_repo.resolve()}").strip()
        command = [str(args.mvn), "-f", str(POM), "test-compile", "exec:java",
                   "-Dexec.mainClass=kr.ac.khu.padosat.fds.FdsMain",
                   "-Dexec.classpathScope=test",
                   "-Dexec.args=" + " ".join(_maven_token(value) for value in fds_args)]
        subprocess.run(command, cwd=ROOT, env=environment, check=True)
    stage_n08(run, args.n08_outbox_dir.resolve())
    stage(run, args.outbox_dir.resolve())
    published = publish(run, args.view_dir.resolve())
    publish_mps_schedule(run, args.mps_schedule_dir.resolve())
    metadata = dict(line.split("=", 1) for line in (run / "run.properties").read_text().splitlines()
                    if "=" in line)
    return published, metadata["source"]


def signature(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """Recheck both pairs on every poll; never reuse a previous validation."""
    digests = []
    for route in ("tle", "gnss"):
        body, manifest = input_pair(args, route)
        payload = verified_pair(body, manifest, route)
        if payload is None:
            digests.append(None)
        else:
            digests.append(hashlib.sha256(payload + b"\0" + _regular_bytes(manifest, 4096)).hexdigest())
    return tuple(digests)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="approved FDS configuration template")
    parser.add_argument("--orekit-data", type=Path, required=True)
    parser.add_argument("--tle-body", type=Path)
    parser.add_argument("--tle-manifest", type=Path)
    parser.add_argument("--gnss-body", type=Path)
    parser.add_argument("--gnss-manifest", type=Path)
    parser.add_argument("--tle-current-root", type=Path,
                        help="atomic N05 ingress root from receive_fds_inputs")
    parser.add_argument("--gnss-current-root", type=Path,
                        help="atomic N11 ingress root from receive_fds_inputs")
    parser.add_argument('--tle-inbox',type=Path,help='local DGS-staged committed N05 pairs; requires tle-current-root')
    parser.add_argument('--gnss-inbox',type=Path,help='local ROS-staged committed N11 pairs; requires gnss-current-root')
    parser.add_argument("--output-root", type=Path, default=ROOT / "out/fds-runs")
    parser.add_argument("--outbox-dir", type=Path, default=ROOT / "out/fds-outbox",
                        help="unique N13/N15 file pairs staged after each successful FDS run")
    parser.add_argument("--n08-outbox-dir", type=Path, default=ROOT / "out/fds-n08-outbox",
                        help="local-only draft N08 pairs; never used by the SFTP sender")
    parser.add_argument("--view-dir", type=Path, default=ROOT / "out/fds-live-view",
                        help="separate operational-input view; never the fixed ISS demo by default")
    parser.add_argument("--mps-schedule-dir", type=Path, default=ROOT / "out/mps-schedules",
                        help="validated N13 snapshots for the MPS read-only service")
    parser.add_argument("--mvn", type=Path, default=Path("mvn"))
    parser.add_argument("--maven-repo", type=Path)
    parser.add_argument("--watch", action="store_true", help="poll validated file pairs continuously")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--refresh-seconds", type=int, default=900)
    args = parser.parse_args()
    if (args.tle_inbox and not args.tle_current_root) or (args.gnss_inbox and not args.gnss_current_root):
        parser.error('inbox staging requires its current-root')
    if (args.tle_current_root and (args.tle_body or args.tle_manifest)) or \
            (args.gnss_current_root and (args.gnss_body or args.gnss_manifest)):
        parser.error("use either a route's current root or explicit pair paths")
    if args.tle_body is None and args.tle_manifest is None and args.tle_current_root is None and \
            args.gnss_body is None and args.gnss_manifest is None and args.gnss_current_root is None:
        parser.error("configure at least one complete N05 or N11 file-pair path")
    if args.poll_seconds < 5 or args.refresh_seconds < args.poll_seconds:
        parser.error("refresh-seconds must be >= poll-seconds >= 5")
    previous = None
    last_success = 0.0
    while True:
        try:
            if args.tle_inbox or args.gnss_inbox:
                if __package__:from .receive_fds_inputs import ingest_inbox
                else:from receive_fds_inputs import ingest_inbox
                for route in ('tle','gnss'):
                    inbox=getattr(args,route+'_inbox')
                    if inbox is not None and inbox.is_dir():
                        ingest_inbox(route,inbox,getattr(args,route+'_current_root'))
            current = signature(args)
            if current == (None, None):
                raise ValueError("waiting for a complete N05 or N11 file pair")
            if current != previous or time.monotonic() - last_success >= args.refresh_seconds:
                published, source = run_cycle(args)
                previous, last_success = current, time.monotonic()
                print(f"FDS published {published} source={source}", flush=True)
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            if not args.watch:
                parser.exit(2, f"FDS pipeline failed: {error}\n")
            print(f"FDS pipeline waiting: {error}", file=sys.stderr, flush=True)
        if not args.watch:
            break
        try:
            time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
