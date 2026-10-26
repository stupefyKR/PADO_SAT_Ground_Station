"""Bound the separate fictional ISS display's retained FDS outputs."""

from pathlib import Path
import tempfile
import unittest

from tools.build_iss_demo import prune_display_runs, validate_fixture


class IssDisplayDemoTests(unittest.TestCase):
    def test_fixed_reference_is_unchanged(self):
        validate_fixture()

    def test_retention_only_removes_old_completed_iss_display_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(10):
                stamp = f"20261026T{index:02d}0000Z-{index:08x}"
                run = root / f"fds-iss-display-{stamp}"
                run.mkdir()
                (run / "run.properties").write_text("schema=fds.run.v1\n", encoding="ascii")
                (root / f"iss-display-{stamp}.tle").write_text("fixture", encoding="ascii")
                (root / f"iss-display-{stamp}.properties").write_text("config", encoding="ascii")
            unrelated = root / "fds-synthetic-truth-20261026T000000Z-00000000"
            unrelated.mkdir()
            (unrelated / "run.properties").write_text("unrelated", encoding="ascii")
            incomplete = root / "fds-iss-display-20261026T110000Z-0000000b"
            incomplete.mkdir()
            self.assertEqual(prune_display_runs(root), 2)
            self.assertTrue(unrelated.is_dir())
            self.assertTrue(incomplete.is_dir())
            self.assertEqual(len(list(root.glob("fds-iss-display-*/run.properties"))), 8)
            self.assertEqual(len(list(root.glob("iss-display-*.tle"))), 8)
            self.assertEqual(len(list(root.glob("iss-display-*.properties"))), 8)


if __name__ == "__main__":
    unittest.main()
