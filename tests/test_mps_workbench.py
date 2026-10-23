"""Test-only MPS drafts follow a physical pass through FDS refreshes."""

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
    def test_sequence_persists_typed_steps_and_rejects_stale_or_invalid_input(self):
        (ROOT / "out").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "out") as folder:
            store = MpsCandidateStore(Path(folder) / "mps.db")
            steps = [{"offset_s": 10, "command_name": "hk_set_period",
                      "parameters": {"period_s": 30}},
                     {"offset_s": 20, "command_name": "hk_get",
                      "parameters": {}}]
            saved = store.save_sequence(schedule(), "yonsei-001", "a" * 64,
                                        0, steps, "planner01")
            self.assertEqual(saved["revision"], 1)
            self.assertEqual(store.list_sequences(schedule())[0]["steps"], steps)
            with self.assertRaisesRegex(CandidateError, "revision_conflict"):
                store.save_sequence(schedule(), "yonsei-001", "a" * 64,
                                    0, steps, "planner01")
            for invalid in [
                [{**steps[0], "offset_s": 100}],
                [{**steps[0], "parameters": {"period_s": True}}],
                [{**steps[0], "parameters": {"period_s": 65536}}],
                [{**steps[0], "parameters": {"period_s": 30, "other": 1}}],
                [{"offset_s": 0, "command_name": "sp_burn", "parameters": {}}],
            ]:
                with self.assertRaisesRegex(CandidateError, "invalid_sequence"):
                    store.save_sequence(schedule(), "yonsei-001", "a" * 64,
                                        1, invalid, "planner01")
            with self.assertRaisesRegex(CandidateError, "stale_schedule"):
                store.save_sequence(schedule("b" * 64), "yonsei-001", "a" * 64,
                                    1, steps, "planner01")
            with self.assertRaisesRegex(CandidateError, "tc_not_available_for_pass"):
                store.save_sequence(schedule(), "kari-001", "a" * 64,
                                    0, steps, "planner01")
            store.close()
            reopened = MpsCandidateStore(Path(folder) / "mps.db")
            self.assertEqual(reopened.list_sequences(schedule())[0]["steps"], steps)
            self.assertEqual(reopened.list_sequences(schedule("b" * 64))[0]["steps"], steps)
            reopened.close()

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
            self.assertEqual(reopened.list_current(schedule("b" * 64))[0]["candidate_ids"],
                             ["HK_GET"])
            self.assertEqual(reopened.list_current({"schedule_state": "absent"}), [])
            reopened.close()

    def test_renumbered_pass_preserves_sequence_and_candidates_across_refresh(self):
        (ROOT / "out").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "out") as folder:
            store = MpsCandidateStore(Path(folder) / "drafts.db")
            old = schedule()
            steps = [{"offset_s": 10, "command_name": "hk_get", "parameters": {}}]
            store.save(old, "yonsei-001", old["schedule_sha256"], 0,
                       ["HK_GET"], "planner01")
            store.save_sequence(old, "yonsei-001", old["schedule_sha256"], 0,
                                steps, "planner01")
            new = schedule("b" * 64)
            new["passes"][0].update(pass_id="yonsei-002", aos_utc_s=103,
                                    los_utc_s=203)
            candidates = store.list_current(new)
            sequences = store.list_sequences(new)
            self.assertEqual([(item["pass_id"], item["revision"])
                              for item in candidates], [("yonsei-002", 1)])
            self.assertEqual(sequences[0]["steps"], steps)
            self.assertEqual(sequences[0]["schedule_sha256"], "b" * 64)
            with self.assertRaisesRegex(CandidateError, "stale_schedule"):
                store.save_sequence(new, "yonsei-002", "a" * 64, 1,
                                    steps, "planner01")
            self.assertEqual(store.save_sequence(new, "yonsei-002", "b" * 64, 1,
                                                 steps, "planner01")["revision"], 2)
            self.assertEqual(store.save(new, "yonsei-002", "b" * 64, 1,
                                        ["HK_GET", "MM_GET_MODE"],
                                        "planner01")["revision"], 2)
            store.close()
            reopened = MpsCandidateStore(Path(folder) / "drafts.db")
            self.assertEqual(reopened.list_sequences(new)[0]["revision"], 2)
            self.assertEqual(reopened.list_current(new)[0]["candidate_ids"],
                             ["HK_GET", "MM_GET_MODE"])
            reopened.close()

    def test_no_inheritance_for_different_or_clipped_pass(self):
        (ROOT / "out").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "out") as folder:
            store = MpsCandidateStore(Path(folder) / "drafts.db")
            old = schedule()
            store.save(old, "yonsei-001", old["schedule_sha256"], 0,
                       ["HK_GET"], "planner01")
            different = schedule("b" * 64)
            different["passes"][0].update(aos_utc_s=1000, los_utc_s=1100)
            self.assertEqual(store.list_current(different), [])
            adjacent = schedule("d" * 64)
            adjacent["passes"][0].update(pass_id="yonsei-002", aos_utc_s=220,
                                         los_utc_s=320)
            self.assertEqual(store.list_current(adjacent), [])
            daejeon = schedule("e" * 64)
            daejeon["passes"] = [{"pass_id": "kari-002", "station_id": "kari",
                                  "band": "S_BAND", "aos_utc_s": 103,
                                  "los_utc_s": 203}]
            self.assertEqual(store.list_current(daejeon), [])
            forged = schedule()
            forged["passes"][0].update(station_id="kari", band="S_BAND")
            self.assertEqual(store.list_current(forged), [])
            clipped = schedule("c" * 64)
            clipped["passes"][0]["clipped_start"] = True
            self.assertEqual(store.list_current(clipped), [])
            corrupted = schedule()
            corrupted["passes"][0]["los_utc_s"] = 201
            self.assertEqual(store.list_current(corrupted), [])
            with self.assertRaisesRegex(CandidateError, "revision_conflict"):
                store.save(corrupted, "yonsei-001", "a" * 64, 0,
                           ["HK_GET"], "planner01")
            self.assertEqual(store.list_current({"schedule_state": "absent"}), [])
            store.close()

    def test_shorter_refreshed_pass_keeps_draft_visible_for_offset_review(self):
        (ROOT / "out").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "out") as folder:
            store = MpsCandidateStore(Path(folder) / "drafts.db")
            steps = [{"offset_s": 90, "command_name": "hk_get", "parameters": {}}]
            store.save_sequence(schedule(), "yonsei-001", "a" * 64, 0,
                                steps, "planner01")
            shorter = schedule("b" * 64)
            shorter["passes"][0].update(pass_id="yonsei-002", aos_utc_s=103,
                                        los_utc_s=183)
            carried = store.list_sequences(shorter)
            self.assertEqual(carried[0]["revision"], 1)
            self.assertTrue(carried[0]["needs_review"])
            with self.assertRaisesRegex(CandidateError, "invalid_sequence"):
                store.save_sequence(shorter, "yonsei-002", "b" * 64, 1,
                                    steps, "planner01")
            revised = [{"offset_s": 70, "command_name": "hk_get", "parameters": {}}]
            self.assertEqual(store.save_sequence(shorter, "yonsei-002", "b" * 64, 1,
                                                 revised, "planner01")["revision"], 2)
            self.assertFalse(store.list_sequences(shorter)[0]["needs_review"])
            store.close()


if __name__ == "__main__":
    unittest.main()
