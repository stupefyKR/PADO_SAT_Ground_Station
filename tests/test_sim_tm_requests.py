"""Immediate simulator dump requests are durable, bounded and claimed once."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.webapp.ros_workbench import DraftError, RosDraftStore
from services.webapp.tm_preview import PreviewError, load_beacon


class SimTmRequestTests(unittest.TestCase):
    def test_claim_ack_reopen_and_no_mps_feedback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ros.db"
            store = RosDraftStore(path)
            with patch("services.webapp.ros_workbench.time.time", return_value=1800000000):
                first = store.request_sim_tm("operator01")
                with self.assertRaisesRegex(DraftError, "pending_or_too_recent"):
                    store.request_sim_tm("operator01")
                ticket = store.claim_sim_tm_request(1800000000)
                self.assertEqual(ticket["parameters"], {"start_utc_ms": 0, "end_utc_ms": 0})
                self.assertEqual(ticket["command_name"], "hk_dump")
                self.assertIsNone(store.claim_sim_tm_request(1800000000))
                ack = {"command_id": 4116, "command_sequence": ticket["command_sequence"],
                       "result": 0, "reason": 0, "executed_at_utc_ms": 1800000000000}
                with self.assertRaisesRegex(DraftError, "invalid_sim_ack"):
                    store.finish_sim_tm_request(first["id"], "ACKED", {**ack, "command_id": 4112})
                store.finish_sim_tm_request(first["id"], "ACKED", ack)
                self.assertEqual(store.n10_test_ack_items(), [])
                self.assertEqual(store.list_sim_reservations(), [])
                self.assertEqual(store.list_sim_sequences(), [])
                self.assertEqual(store.list_sim_tm_requests()[0]["status"], "ACKED")
            store.close()
            store = RosDraftStore(path)
            self.assertEqual(store.list_sim_tm_requests()[0]["ack"], ack)
            store.close()

    def test_expiration_rejection_and_uncertain_restart_never_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ros.db"
            store = RosDraftStore(path)
            with patch("services.webapp.ros_workbench.time.time", return_value=1800000000):
                store.request_sim_tm("planner01")
            self.assertIsNone(store.claim_sim_tm_request(1800000031))
            self.assertEqual(store.list_sim_tm_requests()[0]["status"], "MISSED")
            with patch("services.webapp.ros_workbench.time.time", return_value=1800000040):
                store.request_sim_tm("planner01")
            ticket = store.claim_sim_tm_request(1800000040)
            store.finish_sim_tm_request(ticket["id"], "REJECTED", {
                "command_id": 4116, "command_sequence": ticket["command_sequence"],
                "result": 1, "reason": 5, "executed_at_utc_ms": 0})
            with patch("services.webapp.ros_workbench.time.time", return_value=1800000050):
                store.request_sim_tm("planner01")
            store.claim_sim_tm_request(1800000050)
            store.close()
            store = RosDraftStore(path)
            self.assertEqual(store.list_sim_tm_requests()[0]["status"], "UNKNOWN")
            self.assertIsNone(store.claim_sim_tm_request(1800000051))
            store.close()

    def test_beacon_file_contract_is_separate_and_bounded(self):
        value = {"type": "TEST_ONLY_BEACON", "non_operational": True,
                 "sampled_utc": "2026-10-27T01:00:00Z", "received_utc": "2026-10-27T01:00:01Z",
                 "sequence": 11, "fields": {"mode": 1, "beacon_counter": 10,
                     "beacon_period_s": 5, "boot_count": 1, "reset_cause": 0, "fsw_image": 0,
                     "crash_count": 0, "watchdog_state": 1, "nor_usage_pct": 1,
                     "fdir_level": 0, "fault_bitmap": 0}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test_beacon.json"
            snapshot = path.with_name("test_tm_snapshot.json")
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(load_beacon(snapshot), value)
            value["fields"]["beacon_period_s"] = 0
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(PreviewError):
                load_beacon(snapshot)
            path.write_text(" " * 4097, encoding="utf-8")
            with self.assertRaises(PreviewError):
                load_beacon(snapshot)


if __name__ == "__main__":
    unittest.main()
