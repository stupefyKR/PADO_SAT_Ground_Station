"""The complete scheduled TM workflow uses the real local service boundaries."""

import os
import json
import tempfile
import unittest
from pathlib import Path

from tests.run_scheduled_dump_smoke import run_smoke


@unittest.skipIf(os.name == "nt", "Real AF_UNIX bridge requires Linux")
class ScheduledDumpSmokeTests(unittest.TestCase):
    def test_authenticated_mps_scheduled_downlink(self):
        with tempfile.TemporaryDirectory(prefix="pado-scheduled-test-") as folder:
            report = run_smoke(Path(folder) / "run")
            self.assertEqual(report["result"], "PASS")
            self.assertEqual(report["reservation_status"], "ACKED")
            self.assertEqual(report["batch_size"], 3)
            self.assertEqual(report["start_offset_s"], 10)
            self.assertEqual([command["name"] for command in report["commands_received"]],
                             ["mm_get_mode", "eps_get_hk", "hk_dump"])
            self.assertEqual(report["reservation_statuses"], ["ACKED"] * 3)
            self.assertTrue(report["ordered_sequence_verified"])
            self.assertTrue(report["no_tm_before_dump"])
            self.assertEqual(len(report["acks"]), 3)
            self.assertGreater(report["sent_pdus"], 0)
            print("Scheduled TM workflow: " + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
