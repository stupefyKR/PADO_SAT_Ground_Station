"""N13 snapshot publication preserves the previous schedule on invalid input."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.publish_mps_schedule import publish
from tools.stage_fds_outbox import ROOT


HEADER = ("schema,station_id,band,pass_id,aos_utc,los_utc,peak_utc,"
          "max_elevation_deg,minimum_elevation_deg,clipped_start,clipped_end\n")
ROW = ("fds.contact-schedule.v1,yonsei,UHF,yonsei-001,2026-10-23T10:00:00.125Z,"
       "2026-10-23T10:08:00.250Z,2026-10-23T10:04:00.000Z,42.5,10.0,false,false\n")


class SchedulePublisherTest(unittest.TestCase):
    def test_marker_switches_only_after_valid_pair(self):
        (ROOT / "out").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "out") as folder:
            root = Path(folder)
            destination = root / "published"

            def make_run(name):
                run = root / name
                run.mkdir()
                body = (HEADER + ROW).encode("ascii")
                (run / "contact_schedule.csv").write_bytes(body)
                (run / "contact_schedule.csv.manifest.json").write_text(json.dumps({
                    "file": "contact_schedule.csv", "type": "CONTACT_SCHEDULE",
                    "from": "fds", "to": "mps", "version": 1,
                    "created": "2026-10-23T10:00:00Z",
                    "sha256": hashlib.sha256(body).hexdigest(),
                }), encoding="ascii")
                return run

            first = make_run("fds-20261023T100000Z-1234abcd")
            published = publish(first, destination)
            self.assertEqual((destination / "current.txt").read_text(), first.name + "\n")
            self.assertEqual((published / "contact_schedule.csv").read_bytes(),
                             (HEADER + ROW).encode("ascii"))
            second = make_run("fds-20261023T110000Z-1234abcd")
            (second / "contact_schedule.csv").write_bytes(b"altered")
            with self.assertRaises(ValueError):
                publish(second, destination)
            self.assertEqual((destination / "current.txt").read_text(), first.name + "\n")
            self.assertFalse((destination / second.name).exists())
            with self.assertRaises(ValueError):
                publish(first, destination)


if __name__ == "__main__":
    unittest.main()
