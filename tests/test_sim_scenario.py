"""A new LTAN scenario must not generate TM from an old mounted truth file."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.sim_sat.emulator import (parse_orbit_view, require_scenario, run_forever)
from tests.test_sim_sat_emulator import _view_document
from tools.build_synthetic_demo import SIMULATION_SCENARIO_ID, annotate_truth_scenario


ROOT = Path(__file__).resolve().parents[1]


class SimScenarioTests(unittest.TestCase):
    def test_optional_truth_scenario_is_validated_and_must_match_if_requested(self):
        document = _view_document()
        legacy = parse_orbit_view(json.dumps(document).encode())
        self.assertIsNone(legacy.scenario_id)
        require_scenario(legacy, None)
        with self.assertRaisesRegex(ValueError, "scenario mismatch"):
            require_scenario(legacy, SIMULATION_SCENARIO_ID)
        document["simulation_scenario_id"] = SIMULATION_SCENARIO_ID
        current = parse_orbit_view(json.dumps(document).encode())
        self.assertEqual(current.scenario_id, SIMULATION_SCENARIO_ID)
        require_scenario(current, SIMULATION_SCENARIO_ID)
        with self.assertRaisesRegex(ValueError, "scenario mismatch"):
            require_scenario(current, "ltan6-v1")
        for invalid in (None, 12, "../ltan12", "A" * 65):
            document["simulation_scenario_id"] = invalid
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "scenario ID"):
                parse_orbit_view(json.dumps(document).encode())

    def test_mismatching_truth_waits_without_generating_or_connecting(self):
        class StopLoop(RuntimeError):
            pass

        def stop(_delay):
            raise StopLoop()

        legacy = parse_orbit_view(json.dumps(_view_document()).encode())
        with patch("services.sim_sat.emulator.TmSpool") as spool, \
                patch("services.sim_sat.emulator.load_orbit_view", return_value=legacy), \
                patch("services.sim_sat.emulator.socket.create_connection") as connect:
            with self.assertRaises(StopLoop):
                run_forever(Path("truth.json"), "sim-ground", 9200, 5, 1,
                            Path("new-spool.sqlite3"), scenario_id=SIMULATION_SCENARIO_ID,
                            sleeper=stop)
            spool.return_value.queue_tick.assert_not_called()
            connect.assert_not_called()
            spool.return_value.close.assert_called_once()

    def test_truth_is_marked_before_publish_and_deployment_paths_are_isolated(self):
        output = ROOT / "out"
        output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output) as folder:
            run = Path(folder)
            source = run / "orbit_view.json"
            document = _view_document()
            source.write_text(json.dumps(document), encoding="utf-8")
            annotate_truth_scenario(run)
            marked = json.loads(source.read_text(encoding="utf-8"))
            self.assertEqual(marked.pop("simulation_scenario_id"), SIMULATION_SCENARIO_ID)
            self.assertEqual(marked, document)

        dockerfile = (ROOT / "docker/FdsDemo.Dockerfile").read_text(encoding="utf-8")
        command = json.loads(next(line[4:] for line in dockerfile.splitlines()
                                  if line.startswith("CMD ")))
        self.assertEqual(command[command.index("--output-root") + 1],
                         "/app/out/scenario-ltan12-v1")
        self.assertEqual(command[command.index("--n11-inbox") + 1],
                         "/n11-inbox/scenario-ltan12-v1")
        compose = (ROOT / "deploy/compose.local.yaml").read_text(encoding="utf-8")
        self.assertIn('"--n11-inbox", "/n11-inbox/scenario-ltan12-v1"', compose)
        self.assertIn('"--spool", "/state/test_tm_spool-ltan12-v1.sqlite3"', compose)
        self.assertIn('"--scenario-id", "ltan12-v1"', compose)


if __name__ == "__main__":
    unittest.main()
