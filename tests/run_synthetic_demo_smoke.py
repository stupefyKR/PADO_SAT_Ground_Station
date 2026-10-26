"""Exercise the test satellite -> draft TM -> ROS/N11 -> FDS GNSS path."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from services.sim_ground.receiver import ContactGate, TmReceiver, load_limits
from services.sim_sat.emulator import generate_packet_batch, load_orbit_view
from services.webapp.tm_preview import load_snapshot
from tools.build_synthetic_demo import run_once


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("orekit_data", type=Path)
    parser.add_argument("--mvn", type=Path, default=Path("mvn"))
    parser.add_argument("--maven-repo", type=Path)
    args = parser.parse_args()
    (ROOT / "out").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="synthetic-demo-smoke-", dir=ROOT / "out") as name:
        base = Path(name)
        options = SimpleNamespace(orekit_data=args.orekit_data.resolve(),
                                  mvn=args.mvn.resolve() if args.mvn.exists() else args.mvn,
                                  maven_repo=args.maven_repo.resolve() if args.maven_repo else None,
                                  output_root=base / "runs", truth_view_dir=base / "truth",
                                  view_dir=base / "operator", mps_schedule_dir=base / "mps",
                                  n11_inbox=base / "n11-inbox")
        _, source = run_once(options)
        assert source == "TLE", source
        truth_path = options.truth_view_dir / "orbit_view.json"
        view = load_orbit_view(truth_path)
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        yonsei = next(station for station in truth["stations"]
                      if station["id"] == "yonsei" and station["band"] == "UHF")
        assert yonsei["passes"], "FDS truth has no Yonsei UHF PASS"
        first_pass = yonsei["passes"][0]
        aos = datetime.fromisoformat(first_pass["aos_utc"].replace("Z", "+00:00"))
        los = datetime.fromisoformat(first_pass["los_utc"].replace("Z", "+00:00"))
        midpoint_ms = int((aos + (los - aos) / 2).timestamp() * 1000)
        gate = ContactGate(truth_path)
        assert gate.active(midpoint_ms), "real FDS Yonsei PASS did not open the link"
        assert not gate.active(int(aos.timestamp() * 1000) - 1), \
            "TM link opened before the FDS Yonsei AOS"
        now = datetime.now(timezone.utc).replace(microsecond=0)
        packets, _ = generate_packet_batch(view, now, 0)
        (base / "ros").mkdir()
        receiver = TmReceiver(base / "ros" / "test_tm_snapshot.json",
                              options.n11_inbox,
                              load_limits(ROOT / "services/sim_ground/test_limits.json"))
        received_ms = int(now.timestamp()) * 1000
        for packet in packets:
            receiver.ingest(packet, received_ms=received_ms)
        snapshot = load_snapshot(receiver.snapshot)
        assert snapshot["non_operational"] is True
        assert snapshot["attitude"]["frame"] == "SIM_BODY_TO_ITRF_NADIR"
        assert len(snapshot["sensors"]) == 2
        # Model a GNSS packet that arrives after the next TLE truth run's
        # planning start but before its slower propagation has completed.
        _, source = run_once(options, now=now - timedelta(seconds=5))
        assert source == "GNSS", source
        operator = json.loads((options.view_dir / "orbit_view.json").read_text())
        assert operator["source"] == "GNSS"
        assert operator["norad_id"] == 99999
        assert any(options.mps_schedule_dir.iterdir())
        print("TEST_ONLY satellite TM -> ROS snapshot + N11 -> FDS GNSS + MPS passed")


if __name__ == "__main__":
    main()
