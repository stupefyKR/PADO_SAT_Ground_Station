"""Server publication of the test TM view must fail closed on malformed input."""

import json
import tempfile
import unittest
from pathlib import Path

from services.webapp.tm_preview import PreviewError, load_snapshot, validate_snapshot


def snapshot() -> dict:
    return {"type": "TEST_ONLY_TM_SNAPSHOT", "non_operational": True,
            "received_utc": "2026-10-23T09:00:00Z",
            "sensors": [{"id": "eps.bus_v", "name": "Bus", "group": "EPS", "unit": "V",
                         "lower": 25.0, "upper": 31.0,
                         "samples": [{"utc": "2026-10-23T08:59:59Z", "value": 28.1}]}],
            "attitude": {"utc": "2026-10-23T09:00:00Z", "frame": "TEST_FRAME",
                         "quaternion_wxyz": [1, 0, 0, 0]}}


class TestTmPreview(unittest.TestCase):
    def test_accepts_only_explicit_test_snapshot(self):
        data = json.dumps(snapshot()).encode()
        self.assertEqual(validate_snapshot(data)["sensors"][0]["id"], "eps.bus_v")
        altered = snapshot()
        altered["non_operational"] = False
        with self.assertRaises(PreviewError):
            validate_snapshot(json.dumps(altered).encode())
        with self.assertRaises(PreviewError):
            validate_snapshot(data[:-1] + b',"type":"LIVE"}')

    def test_rejects_invalid_measurement_time_and_attitude(self):
        for change in ("nan", "future", "duplicate", "attitude"):
            value = snapshot()
            if change == "nan":
                value["sensors"][0]["samples"][0]["value"] = float("nan")
            elif change == "future":
                value["sensors"][0]["samples"][0]["utc"] = "2026-10-23T09:00:01Z"
            elif change == "duplicate":
                value["sensors"][0]["samples"].append(value["sensors"][0]["samples"][0])
            else:
                value["attitude"]["quaternion_wxyz"] = [2, 0, 0, 0]
            with self.subTest(change=change), self.assertRaises(PreviewError):
                validate_snapshot(json.dumps(value).encode())

    def test_bounded_regular_file_and_no_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test_tm_snapshot.json"
            path.write_text(json.dumps(snapshot()), encoding="utf-8")
            self.assertTrue(load_snapshot(path)["non_operational"])
            with self.assertRaises(PreviewError):
                load_snapshot(Path(folder) / "wrong.json")
            path.write_bytes(b" " * (1024 * 1024 + 1))
            with self.assertRaises(PreviewError):
                load_snapshot(path)
            path.unlink()
            source = Path(folder) / "source.json"
            source.write_text(json.dumps(snapshot()), encoding="utf-8")
            try:
                path.symlink_to(source)
            except (OSError, NotImplementedError):
                return
            with self.assertRaises(PreviewError):
                load_snapshot(path)


if __name__ == "__main__":
    unittest.main()
