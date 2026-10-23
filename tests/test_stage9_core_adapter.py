import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from services.webapp.core import CoreUnavailable, mps_catalog, ros_summary


class CoreAdapterTests(unittest.TestCase):
    def result(self, body, code=0):
        return subprocess.CompletedProcess([], code, json.dumps(body).encode(), b"")

    @patch("services.webapp.core.subprocess.run")
    def test_ros_validated_summary(self, run):
        run.return_value = self.result({"sim_frame_count": 2,
                                        "last_sequence": 42,
                                        "record_state": "validated"})
        self.assertEqual(ros_summary(Path("/probe"), Path("/record"))["last_sequence"], 42)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0],
                         [str(Path("/probe")), "ros-summary", str(Path("/record"))])

    @patch("services.webapp.core.subprocess.run")
    def test_invalid_core_result_fails_closed(self, run):
        for body in (
            {"sim_frame_count": True, "last_sequence": 1, "record_state": "validated"},
            {"sim_frame_count": 1, "last_sequence": None, "record_state": "validated"},
            {"sim_frame_count": 0, "last_sequence": None, "record_state": "validated"},
        ):
            run.return_value = self.result(body)
            with self.assertRaises(CoreUnavailable):
                ros_summary(Path("/probe"), Path("/record"))
        run.return_value = self.result({"version": 1, "command_count": 1,
                                        "operational": False})
        with self.assertRaises(CoreUnavailable):
            mps_catalog(Path("/probe"), Path("/db"))
        run.return_value = self.result({"version": 1, "command_count": 0,
                                        "operational": True})
        with self.assertRaises(CoreUnavailable):
            mps_catalog(Path("/probe"), Path("/db"))

    @patch("services.webapp.core.subprocess.run")
    def test_failed_or_unavailable_process_fails_closed(self, run):
        run.return_value = self.result({}, code=1)
        with self.assertRaises(CoreUnavailable):
            mps_catalog(Path("/probe"), Path("/db"))
        run.side_effect = subprocess.TimeoutExpired(["/probe"], 2)
        with self.assertRaises(CoreUnavailable):
            ros_summary(Path("/probe"), Path("/record"))


if __name__ == "__main__":
    unittest.main()
