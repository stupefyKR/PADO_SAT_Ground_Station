"""Retention cannot erase unrelated data from a shared FDS output volume."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from tools.build_synthetic_demo import (prune_completed_runs,
                                        prune_synthetic_n11_snapshots)
from tools.receive_fds_inputs import ingest_inbox
from tools.run_fds_pipeline import current_pair


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
                (root / f"synthetic-operator-{group}.properties").write_text("test\n")
                (schedules / f"fds-synthetic-truth-{group}").mkdir()
                (schedules / f"fds-synthetic-operator-{group}").mkdir()
            unrelated = root / "fds-live-important"
            unrelated.mkdir()
            (unrelated / "keep.txt").write_text("keep")
            (schedules / "current.txt").write_text(f"fds-synthetic-operator-{groups[-1]}\n")
            self.assertEqual(prune_completed_runs(root, schedules, keep=2), 1)
            self.assertFalse((root / f"fds-synthetic-truth-{groups[0]}").exists())
            self.assertFalse((root / f"synthetic-{groups[0]}.tle").exists())
            self.assertFalse((root / f"synthetic-operator-{groups[0]}.properties").exists())
            self.assertFalse((schedules / f"fds-synthetic-operator-{groups[0]}").exists())
            self.assertTrue((root / f"fds-synthetic-truth-{groups[-1]}").exists())
            self.assertEqual((unrelated / "keep.txt").read_text(), "keep")
            self.assertIn(groups[-1], (schedules / "current.txt").read_text())

    def test_n11_snapshots_keep_current_and_one_recent_verified_pair(self):
        scratch = Path(__file__).resolve().parents[1] / "out"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as name:
            root = Path(name)
            inbox = root / "inbox"
            ingress = root / "synthetic-n11-current"
            inbox.mkdir()
            snapshots = []
            for index in range(4):
                filename = f"gnss-{index}.properties"
                payload = f"GNSS state {index}\n".encode("ascii")
                (inbox / filename).write_bytes(payload)
                manifest = inbox / (filename + ".manifest.json")
                manifest.write_text(json.dumps({
                    "file": filename, "type": "GNSS", "from": "ros", "to": "fds",
                    "version": 1, "created": "2026-10-23T12:00:00Z",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }), encoding="ascii")
                os.utime(manifest, ns=(1_800_000_000_000_000_000 + index,
                                       1_800_000_000_000_000_000 + index))
                snapshot = ingest_inbox("gnss", inbox, ingress)
                snapshots.append(snapshot)
            # The current pointer must survive even when its directory's mtime
            # is older than an earlier valid snapshot.
            for index, snapshot in enumerate(snapshots):
                os.utime(snapshot, ns=(1_800_000_000_000_000_000 - index * 1_000_000_000,
                                       1_800_000_000_000_000_000 - index * 1_000_000_000))
            unrelated = ingress / ("f" * 64)
            unrelated.mkdir()
            (unrelated / "keep.txt").write_text("unrelated")

            self.assertEqual(prune_synthetic_n11_snapshots(ingress, keep=2), 2)
            self.assertTrue(snapshots[0].is_dir())
            self.assertFalse(snapshots[1].exists())
            self.assertFalse(snapshots[2].exists())
            self.assertTrue(snapshots[3].is_dir())
            self.assertEqual(current_pair(ingress, "gnss")[0].parent, snapshots[3])
            self.assertEqual((unrelated / "keep.txt").read_text(), "unrelated")


if __name__ == "__main__":
    unittest.main()
