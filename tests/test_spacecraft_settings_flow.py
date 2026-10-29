"""One physical edit reaches generated TM; ROS edits bounds without rewriting it."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch

from services.spacecraft_config import load_settings
from services.sim_sat.emulator import TmSpool, parse_orbit_view
from services.sim_ground.receiver import TmReceiver, load_limits
from services.webapp.settings import SettingsStore
from services.webapp.tm_preview import load_snapshot
from tests.test_sim_sat_emulator import START, _view_document
from tools.pado_packet_codec import decode


ROOT = Path(__file__).resolve().parents[1]
KEY = "EPS_POWER.solar_raw_w"


class SharedSettingsFlow(unittest.TestCase):
    def test_windows_utf8_bom_file_is_shared_by_all_readers(self):
        (ROOT / "out").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="settings-bom-", dir=ROOT / "out") as temporary:
            path = Path(temporary) / "spacecraft.json"
            document = load_settings(ROOT / "config/spacecraft.example.json")
            path.write_text(json.dumps(document), encoding="utf-8-sig")
            self.assertEqual(load_limits(path)["sensors"], document["sensor_limits"])
            self.assertEqual(SettingsStore(path).view()["power"], document["power"])

    def test_generated_packet_received_graph_and_live_configuration_share_file(self):
        (ROOT / "out").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="settings-flow-", dir=ROOT / "out") as temporary:
            folder = Path(temporary)
            path = folder / "spacecraft.json"
            settings = load_settings(ROOT / "config/spacecraft.example.json")
            settings["power"]["solar"]["cell_types"]["DSP"] = {"voltage_v": 2.6, "current_a": .6}
            path.write_text(json.dumps(settings), encoding="utf-8")
            view = parse_orbit_view(json.dumps(_view_document()).encode())
            snapshot = folder / "test_tm_snapshot.json"
            inbox = folder / "n11"
            inbox.mkdir()
            with patch.dict(os.environ, {"PADO_SPACECRAFT_CONFIG": str(path)}):
                spool = TmSpool(folder / "spool.sqlite3", full_housekeeping=True)
                receiver = TmReceiver(snapshot, inbox, load_limits(path), limits_path=path)
                try:
                    spool.queue_tick(view, START)
                    for _, packet in spool.iter_pending():
                        receiver.ingest(packet, received_ms=int(START.timestamp() * 1000) + 1000)
                    store = SettingsStore(path)
                    original = load_snapshot(snapshot, limits=store.limits())
                    rule_id = settings["sensor_limits"][KEY]["id"]
                    sensor = next(s for s in original["sensors"] if s["id"] == rule_id)
                    expected = 24 * 2.6 * .6 + 4 * 2.18 * .42
                    self.assertAlmostEqual(sensor["samples"][-1]["value"], expected, places=4)
                    self.assertGreater(expected, sensor["upper"])
                    before = spool.power_model.snapshot()
                    raw_snapshot = snapshot.read_bytes()
                    store.save_limits(store.view()["sha256"], {KEY: {"lower": 0, "upper": 50}})
                    updated = load_snapshot(snapshot, limits=store.limits())
                    updated_sensor = next(s for s in updated["sensors"] if s["id"] == rule_id)
                    self.assertEqual(updated_sensor["upper"], 50)
                    self.assertEqual(updated_sensor["samples"], sensor["samples"])
                    self.assertEqual(updated["received_utc"], original["received_utc"])
                    self.assertEqual(snapshot.read_bytes(), raw_snapshot)
                    self.assertEqual(load_settings(path)["power"], settings["power"])
                    spool.queue_tick(view, START + timedelta(seconds=5))
                    self.assertEqual(before["profile_sha256"], spool.power_model.snapshot()["profile_sha256"])
                    self.assertNotIn("configuration_change", spool.power_sample)
                    state_before_edit = spool.power_model.snapshot()
                    edited = load_settings(path)
                    edited["power"]["solar"]["cell_types"]["DSP"]["voltage_v"] = 2.7
                    path.write_text(json.dumps(edited), encoding="utf-8")
                    spool.queue_tick(view, START + timedelta(seconds=10))
                    # The next orbital sample has a slightly different incidence angle.
                    self.assertAlmostEqual(spool.power_sample["solar_raw_w"], 24 * 2.7 * .6 + 4 * 2.18 * .42, delta=.01)
                    self.assertLess(abs(spool.power_model.snapshot()["energy_wh"] - state_before_edit["energy_wh"]), .1)
                    self.assertIn("configuration_change", spool.power_sample)
                    # Previously generated packets retain their physical values and UTC.
                    first_power = next(decode(p) for _, p in spool.iter_pending()
                                       if decode(p)["name"] == "EPS_POWER")
                    self.assertAlmostEqual(first_power["fields"]["solar_raw_w"], expected, places=4)
                    self.assertEqual(first_power["utc_ms"], int(START.timestamp() * 1000))
                    final_state = spool.power_model.snapshot()
                finally:
                    receiver.archive.close()
                    spool.close()
                reopened = TmSpool(folder / "spool.sqlite3", full_housekeeping=True)
                try:
                    self.assertEqual(reopened.power_model.snapshot(), final_state)
                finally:
                    reopened.close()


if __name__ == "__main__":
    unittest.main()
