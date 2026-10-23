"""Retention cannot erase unrelated data from a shared FDS output volume."""

import tempfile
import unittest
from pathlib import Path

from tools.build_synthetic_demo import prune_completed_runs


class SyntheticRetentionTests(unittest.TestCase):
    def test_only_expired_complete_simulator_groups_are_removed(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            schedules = root / "schedules"
            schedules.mkdir()
            groups = [f"20261023T12000{i}Z-1234567{i}" for i in range(3)]
            for group in groups:
                truth = root / f"fds-synthetic-truth-{group}"
                truth.mkdir()
                (truth / "run.properties").write_text("schema=fds.run.v1\n")
                (root / f"fds-synthetic-operator-{group}").mkdir()
                (root / f"synthetic-{group}.tle").write_text("test\n")
                (root / f"synthetic-{group}.properties").write_text("test\n")
                (schedules / f"fds-synthetic-truth-{group}").mkdir()
                (schedules / f"fds-synthetic-operator-{group}").mkdir()
            unrelated = root / "fds-live-important"
            unrelated.mkdir()
            (unrelated / "keep.txt").write_text("keep")
            (schedules / "current.txt").write_text(f"fds-synthetic-operator-{groups[-1]}\n")
            self.assertEqual(prune_completed_runs(root, schedules, keep=2), 1)
            self.assertFalse((root / f"fds-synthetic-truth-{groups[0]}").exists())
            self.assertFalse((root / f"synthetic-{groups[0]}.tle").exists())
            self.assertFalse((schedules / f"fds-synthetic-operator-{groups[0]}").exists())
            self.assertTrue((root / f"fds-synthetic-truth-{groups[-1]}").exists())
            self.assertEqual((unrelated / "keep.txt").read_text(), "keep")
            self.assertIn(groups[-1], (schedules / "current.txt").read_text())


if __name__ == "__main__":
    unittest.main()
