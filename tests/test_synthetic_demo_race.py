"""Synthetic FDS must plan after a GNSS packet received during truth propagation."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools import build_synthetic_demo


class SyntheticGnssArrivalTests(unittest.TestCase):
    def test_operator_start_is_refreshed_after_truth_and_gnss_selection(self):
        truth_start = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        gnss_arrival = datetime(2026, 10, 23, 12, 0, 4, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "orekit").mkdir()
            options = SimpleNamespace(orekit_data=root / "orekit", mvn=Path("mvn"),
                                      maven_repo=None, output_root=root / "runs",
                                      truth_view_dir=root / "truth", view_dir=root / "view",
                                      mps_schedule_dir=root / "schedules",
                                      n11_inbox=root / "inbox")
            starts = []
            published = []

            def fake_java(_args, main_class, command):
                output = Path(command[command.index("--out") + 1])
                if main_class == "SyntheticTleMain":
                    output.write_text("1 99999" + " " * 62 + "\n" +
                                      "2 99999" + " " * 62 + "\n", encoding="ascii")
                    return
                self.assertEqual(main_class, "FdsMain")
                config = Path(command[command.index("--config") + 1]).read_text()
                start = next(line.removeprefix("start_utc=") for line in config.splitlines()
                             if line.startswith("start_utc="))
                starts.append(start)
                if "--gnss" in command:
                    gnss = Path(command[command.index("--gnss") + 1]).read_text()
                    epoch = next(line.removeprefix("epoch_utc=") for line in gnss.splitlines()
                                 if line.startswith("epoch_utc="))
                    self.assertGreaterEqual(start, epoch)
                output.mkdir()
                (output / "run.properties").write_text(
                    "source=" + ("GNSS" if "--gnss" in command else "TLE") + "\n")

            def fake_publish(run, destination):
                published.append(run.name)
                return destination / "orbit_view.json"

            def fake_latest(_args):
                self.assertEqual(len(published), 1, "truth must be published first")
                gnss = root / "gnss.properties"
                gnss.write_text("epoch_utc=2026-10-23T12:00:04Z\n", encoding="ascii")
                return gnss

            with patch.object(build_synthetic_demo, "_utc_now_second",
                              side_effect=[truth_start, gnss_arrival]), \
                 patch.object(build_synthetic_demo, "_java", side_effect=fake_java), \
                 patch.object(build_synthetic_demo, "_latest_gnss", side_effect=fake_latest), \
                 patch.object(build_synthetic_demo, "publish_view", side_effect=fake_publish), \
                 patch.object(build_synthetic_demo, "publish_schedule",
                              return_value=root / "schedules"), \
                 patch.object(build_synthetic_demo, "prune_completed_runs", return_value=0):
                _, source = build_synthetic_demo.run_once(options)

            self.assertEqual(source, "GNSS")
            self.assertEqual(starts, ["2026-10-23T12:00:00Z", "2026-10-23T12:00:04Z"])
            self.assertIn("truth", published[0])
            self.assertIn("operator", published[1])


if __name__ == "__main__":
    unittest.main()
