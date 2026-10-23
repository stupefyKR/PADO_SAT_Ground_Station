"""Bounded test-only MPS handoff and ROS reservation lifecycle."""

import copy
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from services.webapp.ros_workbench import DraftError, RosDraftStore, normalize_sim_handoff


def handoff(aos: int, *, commands: list[str] | None = None) -> dict:
    return {"schema": "pado.sim-sequence.v1", "source": "MPS_TEST_DRAFT",
            "non_operational": True, "pass_id": "yonsei-001", "station_id": "yonsei",
            "band": "UHF", "schedule_sha256": "a" * 64,
            "aos_utc_s": aos, "los_utc_s": aos + 600, "revision": 1,
            "steps": [{"offset_s": 60 + index * 40, "command_name": name,
                       "parameters": {}}
                      for index, name in enumerate(commands or ["hk_get", "mm_get_mode"])]}


class RosSimHandoffTests(unittest.TestCase):
    def test_handoff_reservation_ack_and_reopen(self):
        aos = int(time.time()) + 3600
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ros.db"
            store = RosDraftStore(path)
            payload = store.import_sim_handoff(handoff(aos), "planner01")
            self.assertEqual(payload["source"], "SIM_MPS_HANDOFF")
            self.assertEqual(payload["steps"][0], {"step_id": 1, "offset_s": 60,
                                                  "command_name": "hk_get", "parameters": {}})
            self.assertEqual(store.import_sim_handoff(handoff(aos), "planner01"), payload)
            self.assertEqual(len(store.list_sim_sequences()), 1)
            reserved = store.reserve_sim(payload["sha256"], 1, "operator01")
            self.assertEqual(reserved["target_utc_s"], aos + 60)
            with self.assertRaisesRegex(DraftError, "sim_already_reserved"):
                store.reserve_sim(payload["sha256"], 1, "operator01")
            self.assertIsNone(store.claim_due(aos + 59))
            ticket = store.claim_due(aos + 60)
            self.assertEqual(ticket["id"], reserved["id"])
            self.assertEqual(ticket["command_name"], "hk_get")
            self.assertEqual(ticket["command_sequence"], 1)
            self.assertIsNone(store.claim_due(aos + 60))
            with self.assertRaisesRegex(DraftError, "invalid_sim_ack"):
                store.finish_sim(ticket["id"], "ACKED", {
                    "command_id": 4112, "command_sequence": 9, "result": 0,
                    "reason": 0, "executed_at_utc_ms": (aos + 60) * 1000})
            ack = {"command_id": 4112, "command_sequence": 1, "result": 0,
                   "reason": 0, "executed_at_utc_ms": (aos + 60) * 1000}
            result = store.finish_sim(ticket["id"], "ACKED", ack)
            self.assertEqual(result["ack"], ack)
            self.assertEqual(store.list_sim_reservations()[0]["status"], "ACKED")
            with self.assertRaisesRegex(DraftError, "sim_not_sending"):
                store.finish_sim(ticket["id"], "ACKED", ack)
            store.close()
            reopened = RosDraftStore(path)
            self.assertEqual(reopened.list_sim_reservations()[0]["ack"], ack)
            reopened.close()

    def test_cancel_missed_and_unknown_on_restart(self):
        aos = int(time.time()) + 3600
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ros.db"
            store = RosDraftStore(path)
            digest = store.import_sim_handoff(handoff(aos), "planner01")["sha256"]
            first = store.reserve_sim(digest, 1, "operator01")
            with self.assertRaises(DraftError):
                store.cancel_sim(first["id"], "other")
            store.cancel_sim(first["id"], "operator01")
            with self.assertRaises(DraftError):
                store.cancel_sim(first["id"], "operator01")
            second = store.reserve_sim(digest, 2, "operator01")
            self.assertIsNone(store.claim_due(aos + 131))
            self.assertEqual(store.list_sim_reservations()[0]["status"], "MISSED")
            store.close()

            newer = copy.deepcopy(handoff(aos))
            newer["revision"] = 2
            newer["steps"] = [{"offset_s": 180, "command_name": "eps_get_hk",
                               "parameters": {}}]
            store = RosDraftStore(path)
            newer_sha = store.import_sim_handoff(newer, "planner01")["sha256"]
            third = store.reserve_sim(newer_sha, 1, "operator01")
            self.assertIsNotNone(store.claim_due(aos + 180))
            store.close()
            store = RosDraftStore(path)
            rows = {row["id"]: row for row in store.list_sim_reservations()}
            self.assertEqual(rows[first["id"]]["status"], "CANCELLED")
            self.assertEqual(rows[second["id"]]["status"], "MISSED")
            self.assertEqual(rows[third["id"]]["status"], "UNKNOWN")
            self.assertIsNone(store.claim_due(aos + 180))
            store.close()

    def test_handoff_rejects_unsafe_or_stale_inputs(self):
        aos = int(time.time()) + 3600
        valid = handoff(aos)
        for key, value in (("source", "FLIGHT"), ("non_operational", False),
                           ("band", "S_BAND"), ("station_id", "kari"),
                           ("schedule_sha256", "x" * 64), ("revision", True)):
            invalid = copy.deepcopy(valid)
            invalid[key] = value
            with self.subTest(key=key), self.assertRaises(DraftError):
                normalize_sim_handoff(invalid)
        for steps in (
                [{"offset_s": 60, "command_name": "crest_on", "parameters": {}}],
                [{"offset_s": 60, "command_name": "hk_get", "parameters": {"node": 1}}],
                [{"offset_s": 600, "command_name": "hk_get", "parameters": {}}],
                []):
            invalid = copy.deepcopy(valid)
            invalid["steps"] = steps
            with self.subTest(steps=steps), self.assertRaises(DraftError):
                normalize_sim_handoff(invalid)
        stale = handoff(int(time.time()) - 700)
        with self.assertRaisesRegex(DraftError, "stale_sim_pass"):
            normalize_sim_handoff(stale)

    def test_saved_step_order_and_same_second_are_preserved(self):
        aos = int(time.time()) + 3600
        envelope = handoff(aos)
        envelope["steps"] = [
            {"offset_s": 120, "command_name": "hk_get", "parameters": {}},
            {"offset_s": 60, "command_name": "eps_get_hk", "parameters": {}},
            {"offset_s": 60, "command_name": "mm_get_mode", "parameters": {}},
        ]
        with tempfile.TemporaryDirectory() as folder:
            store = RosDraftStore(Path(folder) / "ros.db")
            payload = store.import_sim_handoff(envelope, "planner01")
            for step_id in (1, 2, 3):
                store.reserve_sim(payload["sha256"], step_id, "operator01")
            first = store.claim_due(aos + 60)
            self.assertEqual(first["step_id"], 2)
            second = store.claim_due(aos + 60)
            self.assertEqual(second["step_id"], 3)
            self.assertIsNone(store.claim_due(aos + 60))
            store.close()

    def test_cancelled_step_can_be_reserved_again(self):
        aos = int(time.time()) + 3600
        with tempfile.TemporaryDirectory() as folder:
            store = RosDraftStore(Path(folder) / "ros.db")
            digest = store.import_sim_handoff(handoff(aos), "planner01")["sha256"]
            first = store.reserve_sim(digest, 1, "operator01")
            store.cancel_sim(first["id"], "operator01")
            second = store.reserve_sim(digest, 1, "operator01")
            self.assertGreater(second["id"], first["id"])
            self.assertEqual(second["status"], "SCHEDULED")
            store.close()

    def test_revision_conflict_and_atomic_claim(self):
        aos = int(time.time()) + 3600
        with tempfile.TemporaryDirectory() as folder:
            store = RosDraftStore(Path(folder) / "ros.db")
            digest = store.import_sim_handoff(handoff(aos, commands=["hk_get"]),
                                              "planner01")["sha256"]
            other = handoff(aos, commands=["mm_get_mode"])
            with self.assertRaisesRegex(DraftError, "sim_revision_conflict"):
                store.import_sim_handoff(other, "planner01")
            reserved = store.reserve_sim(digest, 1, "operator01")
            with ThreadPoolExecutor(max_workers=2) as pool:
                attempts = list(pool.map(store.claim_due, [aos + 60, aos + 60]))
            self.assertEqual(sum(value is not None for value in attempts), 1)
            self.assertEqual(next(value for value in attempts if value)["id"], reserved["id"])
            store.close()


if __name__ == "__main__":
    unittest.main()
