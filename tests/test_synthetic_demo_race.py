"""Synthetic FDS must plan after a GNSS packet received during truth propagation."""

import json
import io
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools import build_synthetic_demo


class SyntheticGnssArrivalTests(unittest.TestCase):
    def test_reconstruction_failure_does_not_block_forward_view_or_schedule(self):
        stamp = datetime(2026,10,29,12,0,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"orekit").mkdir()
            options = SimpleNamespace(orekit_data=root/"orekit",mvn=Path("mvn"),maven_repo=None,
                output_root=root/"runs",truth_view_dir=root/"truth",view_dir=root/"view",
                mps_schedule_dir=root/"schedules",n11_inbox=None)

            def fake_java(_args,name,command):
                output = Path(command[command.index("--out")+1])
                if name=="SyntheticTleMain":
                    output.write_text("1 99999"+" "*62+"\n2 99999"+" "*62+"\n",encoding="ascii")
                elif name=="OrbitReconstructionMain":
                    raise subprocess.CalledProcessError(1,["test-reconstruction"])
                else:
                    self.assertEqual(name,"FdsMain")
                    output.mkdir()
                    (output/"orbit_view.json").write_text(json.dumps({"source":"TLE","norad_id":99999}))
                    (output/"run.properties").write_text("source=TLE\n")

            errors = io.StringIO()
            with patch.object(build_synthetic_demo,"_utc_now_second",return_value=stamp), \
                 patch.object(build_synthetic_demo,"_java",side_effect=fake_java), \
                 patch.object(build_synthetic_demo,"_latest_gnss",return_value=None), \
                 patch.object(build_synthetic_demo,"publish_view",side_effect=lambda run,dest:dest/"orbit_view.json") as views, \
                 patch.object(build_synthetic_demo,"publish_schedule",return_value=root/"schedules") as schedules, \
                 patch.object(build_synthetic_demo,"prune_completed_runs",return_value=0), \
                 patch.object(build_synthetic_demo.sys,"stderr",errors):
                view,source = build_synthetic_demo.run_once(options,now=stamp)
            self.assertEqual(source,"TLE")
            self.assertEqual(view,root/"view/orbit_view.json")
            self.assertEqual(views.call_count,2)
            schedules.assert_called_once()
            self.assertIn("retrospective display unavailable",errors.getvalue())

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
            reconstructions = []

            def fake_java(_args, main_class, command):
                output = Path(command[command.index("--out") + 1])
                if main_class == "SyntheticTleMain":
                    output.write_text("1 99999" + " " * 62 + "\n" +
                                      "2 99999" + " " * 62 + "\n", encoding="ascii")
                    return
                if main_class == "OrbitReconstructionMain":
                    reconstructions.append((output,command))
                    self.assertEqual(output.parent.name.replace("operator","truth"),published[0])
                    self.assertEqual(command[command.index("--end")+1],"2026-10-23T12:00:04Z")
                    self.assertIn("synthetic-",Path(command[command.index("--tle")+1]).name)
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
                (output / "orbit_view.json").write_text(json.dumps({
                    "source": "GNSS" if "--gnss" in command else "TLE",
                    "norad_id": 99999}), encoding="utf-8")

            def fake_publish(run, destination):
                if "truth" in run.name:
                    truth = json.loads((run / "orbit_view.json").read_text())
                    self.assertEqual(truth["simulation_scenario_id"], "ltan12-v1")
                published.append(run.name)
                return destination / "orbit_view.json"

            def fake_latest(_args):
                self.assertEqual(len(published), 1, "truth must be published first")
                gnss = root / "gnss.properties"
                gnss.write_text("epoch_utc=2026-10-23T12:00:04Z\n", encoding="ascii")
                return gnss

            with patch.object(build_synthetic_demo, "_utc_now_second",
                              side_effect=[truth_start, gnss_arrival,gnss_arrival]), \
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
            self.assertEqual(len(reconstructions),1)
            self.assertEqual(reconstructions[0][0].name,"orbit_reconstruction.json")


if __name__ == "__main__":
    unittest.main()
