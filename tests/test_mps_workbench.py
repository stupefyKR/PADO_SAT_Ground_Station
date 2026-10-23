"""Test-only MPS candidate drafts remain bound to one validated schedule."""

import tempfile
import unittest
from pathlib import Path

from services.webapp.mps_workbench import CandidateError, MpsCandidateStore, draft_commands
from tools.stage_fds_outbox import ROOT


def schedule(digest="a" * 64):
    return {"schedule_state": "validated", "schedule_sha256": digest,
            "passes": [{"pass_id": "yonsei-001", "station_id": "yonsei",
                        "band": "UHF", "aos_utc_s": 100, "los_utc_s": 200},
                       {"pass_id": "kari-001", "station_id": "kari",
                        "band": "S_BAND", "aos_utc_s": 300, "los_utc_s": 400}]}


class MpsCandidateStoreTest(unittest.TestCase):
    def test_catalog_is_non_operational_and_blocks_undefined_arguments(self):
        commands = draft_commands()
        self.assertEqual(len(commands), 58)
        self.assertEqual(sum(item["fields_defined"] for item in commands), 36)
        self.assertTrue(all(item["operational"] is False for item in commands))

    def test_persists_only_revision_bound_candidate_ids(self):
        (ROOT / "out").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "out") as folder:
            path = Path(folder) / "candidates.db"
            store = MpsCandidateStore(path)
            first = store.save(schedule(), "yonsei-001", "a" * 64, 0,
                               ["HK_GET", "MM_GET_MODE"], "planner01")
            self.assertEqual(first["revision"], 1)
            self.assertFalse(first["operational"])
            with self.assertRaisesRegex(CandidateError, "revision_conflict"):
                store.save(schedule(), "yonsei-001", "a" * 64, 0,
                           ["HK_GET"], "planner01")
            with self.assertRaisesRegex(CandidateError, "invalid_candidate_draft"):
                store.save(schedule(), "yonsei-001", "a" * 64, 1,
                           ["unsafe command"], "planner01")
            with self.assertRaisesRegex(CandidateError, "invalid_candidate_draft"):
                store.save(schedule(), "yonsei-001", "a" * 64, 1,
                           ["SP_BURN"], "planner01")
            with self.assertRaisesRegex(CandidateError, "stale_schedule"):
                store.save(schedule("b" * 64), "yonsei-001", "a" * 64, 1,
                           ["HK_GET"], "planner01")
            with self.assertRaisesRegex(CandidateError, "tc_not_available_for_pass"):
                store.save(schedule(), "kari-001", "a" * 64, 0,
                           ["HK_GET"], "planner01")
            self.assertEqual(store.save(schedule(), "yonsei-001", "a" * 64, 1,
                                        ["HK_GET"], "planner01")["revision"], 2)
            store.close()
            reopened = MpsCandidateStore(path)
            self.assertEqual(reopened.list_current(schedule())[0]["candidate_ids"], ["HK_GET"])
            self.assertEqual(reopened.list_current(schedule("b" * 64)), [])
            self.assertEqual(reopened.list_current({"schedule_state": "absent"}), [])
            reopened.close()


if __name__ == "__main__":
    unittest.main()
