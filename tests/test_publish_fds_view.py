import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.publish_fds_view import publish


SCRATCH = Path(__file__).resolve().parents[1] / "out"


class PublishFdsViewTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "run"
        self.run.mkdir()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def fixture(self, *, start_offset_hours=-1, input_offset_hours=-2,norad_id=12345):
        start = self.now + timedelta(hours=start_offset_hours)
        end = start + timedelta(hours=24)
        epoch = self.now + timedelta(hours=input_offset_hours)
        iso = lambda date: date.isoformat().replace("+00:00", "Z")
        payload = {
            "schema": "fds.orbit-view.v1", "quality": "PRELIMINARY_NOT_FOR_OPERATIONS",
            "source": "TLE", "norad_id": norad_id, "input_sha256": "a" * 64,
            "input_epoch_utc": iso(epoch), "start_utc": iso(start), "end_utc": iso(end),
            "stations": [{"id": "yonsei"}, {"id": "kari"}],
            "sample_step_s": 60,
            "samples": [[iso(start+timedelta(seconds=offset)),6878137.,0.,0.,0.,0.,500000.,1.,0.,0.]
                        for offset in range(0,24*3600+1,60)],
        }
        (self.run / "orbit_view.json").write_text(json.dumps(payload), encoding="utf-8")
        (self.run / "run.properties").write_text(
            f"schema=fds.run.v1\nsource=TLE\nexpected_norad_id={norad_id}\ninput_sha256=" +
            "a" * 64 + "\n", encoding="utf-8")
        return payload

    def reconstruction(self):
        iso = lambda date: date.isoformat().replace("+00:00","Z")
        start = self.now-timedelta(hours=72)
        payload = {"schema":"pado.orbit-reconstruction.v1","quality":"RECONSTRUCTED_NOT_MEASURED",
            "norad_id":99999,"source":"TLE","input_epoch_utc":iso(self.now-timedelta(seconds=2)),
            "generated_utc":iso(self.now),"start_utc":iso(start),"end_utc":iso(self.now),
            "sample_step_s":60,"samples":[[iso(start+timedelta(seconds=offset)),6878137.,0.,0.,1.,0.,0.]
                for offset in range(0,72*3600+1,60)]}
        path = self.run/"orbit_reconstruction.json"
        path.write_text(json.dumps(payload),encoding="utf-8")
        return payload,path

    def test_fresh_run_is_atomically_published(self):
        self.fixture()
        final = publish(self.run, self.root / "served")
        self.assertEqual(json.loads(final.read_text())["norad_id"], 12345)
        self.assertEqual(sorted(item.name for item in final.parent.iterdir()),
                         ["orbit_history.sqlite3", "orbit_view.json"])

    def test_stale_run_is_rejected(self):
        self.fixture(start_offset_hours=-100, input_offset_hours=-101)
        with self.assertRaises(ValueError):
            publish(self.run, self.root / "served")

    def test_mismatched_input_cannot_replace_existing_view(self):
        payload = self.fixture()
        final = publish(self.run, self.root / "served")
        before = final.read_bytes()
        payload["input_sha256"] = "b" * 64
        (self.run / "orbit_view.json").write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ValueError):
            publish(self.run, self.root / "served")
        self.assertEqual(final.read_bytes(), before)

    def test_reconstruction_is_published_separately_without_changing_forward_history(self):
        forward = self.fixture(norad_id=99999)
        reconstructed,path = self.reconstruction()
        final = publish(self.run,self.root/"served")
        self.assertEqual(json.loads(final.read_bytes()),forward)
        self.assertEqual((final.parent/path.name).read_bytes(),path.read_bytes())
        self.assertEqual(sorted(entry.name for entry in final.parent.iterdir()),
            ["orbit_history.sqlite3","orbit_reconstruction.json","orbit_view.json"])
        with closing(sqlite3.connect(final.with_name("orbit_history.sqlite3"))) as connection:
            oldest = connection.execute("SELECT MIN(utc_ms) FROM samples").fetchone()[0]
        stamp = lambda value: int(datetime.fromisoformat(value.replace("Z","+00:00")).timestamp())*1000
        self.assertEqual(oldest,stamp(forward["start_utc"]))
        self.assertGreater(oldest,stamp(reconstructed["start_utc"]))

    def test_invalid_reconstruction_cannot_replace_either_published_file(self):
        self.fixture(norad_id=99999)
        payload,path = self.reconstruction()
        final = publish(self.run,self.root/"served")
        before = final.read_bytes(),final.with_name(path.name).read_bytes()
        payload["quality"] = "MEASURED"
        path.write_text(json.dumps(payload),encoding="utf-8")
        with self.assertRaises(ValueError):
            publish(self.run,final.parent)
        self.assertEqual((final.read_bytes(),final.with_name(path.name).read_bytes()),before)
        self.assertFalse(any(entry.name.endswith(".tmp") for entry in final.parent.iterdir()))


if __name__ == "__main__":
    unittest.main()
