"""The full equipment display retains an hour inside its bounded API payload."""

import json
import unittest
from datetime import timedelta
from pathlib import Path

from services.sim_ground.receiver import load_limits
from services.sim_sat.emulator import generate_housekeeping_batch, parse_orbit_view
from tests.test_sim_sat_emulator import START, _sample, _view_document
from tools.preview_draft_tm import MAX_SIM_HISTORY_PDUS, build_snapshot


class FullTmSnapshotTests(unittest.TestCase):
    def test_twenty_sensors_hour_is_complete_and_bounded(self):
        document = _view_document()
        document["end_utc"] = (START + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        document["samples"] = [_sample(START + timedelta(seconds=offset), offset)
                               for offset in range(0, 3601, 60)]
        view = parse_orbit_view(json.dumps(document).encode())
        packets = []
        for offset in range(0, 3601, 5):
            batch, _ = generate_housekeeping_batch(
                view, START + timedelta(seconds=offset), (offset // 5 * 256) & 0xffff)
            packets.extend(batch)
        limits = load_limits(Path(__file__).resolve().parents[1] /
                             "services/sim_ground/full_test_limits.json")
        snapshot = build_snapshot(packets, limits,
            received_ms=int((START + timedelta(hours=1)).timestamp()) * 1000,
            max_packets=MAX_SIM_HISTORY_PDUS)
        self.assertEqual(len(snapshot["sensors"]), 20)
        self.assertTrue(all(len(sensor["samples"]) == 721 for sensor in snapshot["sensors"]))
        self.assertIsNotNone(snapshot["attitude"])
        self.assertLess(len(json.dumps(snapshot, ensure_ascii=False).encode()), 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
