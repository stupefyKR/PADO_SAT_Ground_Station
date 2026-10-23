"""End-to-end, current-time GNSS input through FDS and atomic view publication."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.run_fds_pipeline import run_cycle


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("orekit_data", type=Path)
    parser.add_argument("--mvn", type=Path, default=Path("mvn"))
    parser.add_argument("--maven-repo", type=Path)
    args = parser.parse_args()
    scratch = ROOT / "out/fds-pipeline-smoke"
    scratch.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    epoch = (now - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    gnss = scratch / "synthetic-gnss.properties"
    body = (ROOT / "services/fds/src/test/resources/test-gnss.properties").read_text(encoding="utf-8")
    gnss.write_text(body.replace("epoch_utc=2000-06-27T19:00:00Z", "epoch_utc=" + epoch),
                    encoding="utf-8")
    manifest = scratch / "synthetic-gnss.properties.manifest.json"
    manifest.write_text(json.dumps({
        "file": gnss.name, "type": "GNSS", "from": "ros", "to": "fds", "version": 1,
        "created": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha256": hashlib.sha256(gnss.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    config = scratch / "config.properties"
    original = (ROOT / "services/fds/src/test/resources/test-config.properties").read_text(encoding="utf-8")
    config.write_text(original.replace("horizon_hours=24", "horizon_hours=1"), encoding="utf-8")
    pipeline = argparse.Namespace(
        config=config, orekit_data=args.orekit_data.resolve(), tle_body=None, tle_manifest=None,
        gnss_body=gnss, gnss_manifest=manifest, output_root=scratch / "runs",
        view_dir=scratch / "view", mvn=args.mvn, maven_repo=args.maven_repo,
    )
    published, source = run_cycle(pipeline)
    if source != "GNSS":
        raise AssertionError(f"GNSS input was not selected: {source}")
    view = json.loads(published.read_text(encoding="utf-8"))
    if view["source"] != "GNSS" or view["norad_id"] != 5 or len(view["samples"]) != 61:
        raise AssertionError("published viewer output does not match the synthetic GNSS run")
    print(f"FDS pipeline smoke passed: {published}")


if __name__ == "__main__":
    main()
