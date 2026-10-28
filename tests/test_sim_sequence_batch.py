"""One AOS-relative sequence start, atomic reservation and ACK-ordered sends."""

import json
import os
import secrets
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from services.webapp.ros_workbench import DraftError, RosDraftStore, normalize_sim_handoff
from tests import test_sim_tc_web as sim_http
from tests.test_ros_sim_handoff import handoff
from tools.pado_packet_codec import load_catalog


class SequenceBatchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)/"ros.db"
        self.store = RosDraftStore(self.path)
        self.addCleanup(lambda: self.store.close() if self.store is not None else None)
        self.aos = int(time.time())+3600

    def imported(self, *, revision=1, offsets=None):
        envelope = handoff(self.aos, commands=["mm_get_mode", "eps_get_hk", "hk_dump"])
        envelope["revision"] = revision
        for index, step in enumerate(envelope["steps"]):
            step["offset_s"] = offsets[index] if offsets else 10
            if step["command_name"] == "hk_dump":
                step["parameters"] = {"start_utc_ms": 0, "end_utc_ms": 0}
        return self.store.import_sim_handoff(envelope, "planner01")

    @staticmethod
    def ack(ticket, *, accepted=True):
        command_id = next(item["id"] for item in load_catalog()["commands"]
                          if item["name"] == ticket["command_name"])
        return {"command_id": command_id, "command_sequence": ticket["command_sequence"],
                "result": 0 if accepted else 1, "reason": 0 if accepted else 1,
                "executed_at_utc_ms": ticket["target_utc_s"]*1000 if accepted else 0}

    def test_shared_start_and_each_matching_ack_release_next_command(self):
        sequence = self.imported()
        batch = self.store.reserve_sim_batch(sequence["sha256"], "operator01")
        self.assertEqual(batch["count"], 3)
        self.assertEqual(batch["start_offset_s"], 10)
        self.assertEqual({row["target_utc_s"] for row in batch["reservations"]}, {self.aos+10})
        self.assertIsNone(self.store.claim_due(self.aos+9))
        names = []
        for index in range(1, 4):
            ticket = self.store.claim_due(self.aos+10)
            self.assertEqual(ticket["step_id"], index)
            self.assertEqual(ticket["batch_id"], batch["batch_id"])
            names.append(ticket["command_name"])
            self.assertIsNone(self.store.claim_due(self.aos+10))
            self.store.finish_sim(ticket["id"], "ACKED", self.ack(ticket))
        self.assertEqual(names, ["mm_get_mode", "eps_get_hk", "hk_dump"])
        self.assertIsNone(self.store.claim_due(self.aos+10))
        rows = self.store.list_sim_reservations()
        self.assertTrue(all(row["status"] == "ACKED" and row["batch_size"] == 3 for row in rows))

    def test_later_commands_wait_for_ack_without_reusing_first_start_grace(self):
        batch = self.store.reserve_sim_batch(self.imported()["sha256"], "operator01")
        first = self.store.claim_due(self.aos+10)
        self.store.finish_sim(first["id"], "ACKED", self.ack(first))
        second = self.store.claim_due(self.aos+80)
        self.assertEqual(second["step_id"], 2)
        self.store.finish_sim(second["id"], "ACKED", self.ack(second))
        third = self.store.claim_due(self.aos+590)
        self.assertEqual(third["batch_id"], batch["batch_id"])

    def test_missed_first_start_and_los_stop_remaining_commands(self):
        for at_los in (False, True):
            with self.subTest(at_los=at_los):
                sequence = self.imported(revision=2 if at_los else 1)
                batch = self.store.reserve_sim_batch(sequence["sha256"], "operator01")
                if at_los:
                    first = self.store.claim_due(self.aos+10)
                    self.store.finish_sim(first["id"], "ACKED", self.ack(first))
                self.assertIsNone(self.store.claim_due(self.aos+600 if at_los else self.aos+41))
                rows = [r for r in self.store.list_sim_reservations() if r["batch_id"] == batch["batch_id"]]
                self.assertIn("MISSED", {r["status"] for r in rows})
                self.assertFalse(any(r["status"] == "SCHEDULED" for r in rows))
                self.assertTrue(all(r["batch_stop_reason"] == "MISSED" for r in rows))

    def test_failure_and_rejection_stop_remaining_without_automatic_retry(self):
        for revision, status in enumerate(("REJECTED", "TIMEOUT", "SEND_FAILED"), 1):
            with self.subTest(status=status):
                batch = self.store.reserve_sim_batch(self.imported(revision=revision)["sha256"], "operator01")
                first = self.store.claim_due(self.aos+10)
                self.store.finish_sim(first["id"], status,
                    self.ack(first, accepted=False) if status == "REJECTED" else None)
                self.assertIsNone(self.store.claim_due(self.aos+11))
                rows = [r for r in self.store.list_sim_reservations() if r["batch_id"] == batch["batch_id"]]
                self.assertEqual(sum(r["status"] == "CANCELLED" for r in rows), 2)
                self.assertTrue(all(r["batch_stop_reason"] == status and r["blocked_by_step_id"] == 1 for r in rows))

    def test_restart_marks_uncertain_send_and_stops_the_rest(self):
        batch = self.store.reserve_sim_batch(self.imported()["sha256"], "operator01")
        self.assertEqual(self.store.claim_due(self.aos+10)["step_id"], 1)
        self.store.close()
        self.store = RosDraftStore(self.path)
        rows = self.store.list_sim_reservations()
        self.assertEqual({r["step_id"]: r["status"] for r in rows},
                         {1: "UNKNOWN", 2: "CANCELLED", 3: "CANCELLED"})
        self.assertTrue(all(r["batch_id"] == batch["batch_id"] and r["batch_stop_reason"] == "UNKNOWN" for r in rows))
        self.assertIsNone(self.store.claim_due(self.aos+20))

    def test_restart_after_ack_preserves_order_and_shared_start(self):
        self.store.reserve_sim_batch(self.imported()["sha256"], "operator01")
        first = self.store.claim_due(self.aos+10)
        self.store.finish_sim(first["id"], "ACKED", self.ack(first))
        self.store.close()
        self.store = RosDraftStore(self.path)
        second = self.store.claim_due(self.aos+120)
        self.assertEqual(second["step_id"], 2)
        self.assertEqual(second["target_utc_s"], self.aos+10)

    def test_conflicting_single_or_duplicate_batch_is_atomic(self):
        sequence = self.imported()
        self.store.reserve_sim(sequence["sha256"], 2, "operator01")
        with self.assertRaisesRegex(DraftError, "sim_already_reserved"):
            self.store.reserve_sim_batch(sequence["sha256"], "operator01")
        self.assertEqual(len(self.store.list_sim_reservations()), 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM sim_reservation_batches").fetchone()[0], 0)
        sequence = self.imported(revision=2)
        self.store.reserve_sim_batch(sequence["sha256"], "operator01")
        with self.assertRaisesRegex(DraftError, "sim_already_reserved"):
            self.store.reserve_sim_batch(sequence["sha256"], "operator01")
        self.assertEqual(len(self.store.list_sim_reservations()), 4)

    def test_failed_second_insert_rolls_back_every_row_and_batch(self):
        sequence = self.imported()
        self.store.db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON sim_reservations "
            "WHEN NEW.step_id=2 BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        with self.assertRaises(DraftError):
            self.store.reserve_sim_batch(sequence["sha256"], "operator01")
        self.assertEqual(self.store.list_sim_reservations(), [])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM sim_reservation_batches").fetchone()[0], 0)

    def test_batch_cancel_is_owned_and_keeps_inflight_outcome(self):
        batch = self.store.reserve_sim_batch(self.imported()["sha256"], "operator01")
        with self.assertRaises(DraftError):
            self.store.cancel_sim_batch(batch["batch_id"], "other")
        first = self.store.claim_due(self.aos+10)
        cancelled = self.store.cancel_sim_batch(batch["batch_id"], "operator01")
        self.assertEqual(cancelled["count"], 2)
        self.store.finish_sim(first["id"], "ACKED", self.ack(first))
        self.assertIsNone(self.store.claim_due(self.aos+11))
        rows = self.store.list_sim_reservations()
        self.assertEqual(sum(r["status"] == "ACKED" for r in rows), 1)
        self.assertTrue(all(r["batch_stop_reason"] == "OPERATOR_CANCELLED" for r in rows))

    def test_same_target_batches_do_not_interleave_even_while_ack_is_pending(self):
        first_batch = self.store.reserve_sim_batch(self.imported()["sha256"], "operator01")
        second_batch = self.store.reserve_sim_batch(self.imported(revision=2)["sha256"], "operator01")
        sent = []
        for batch in (first_batch, second_batch):
            for step_id in (1, 2, 3):
                with ThreadPoolExecutor(max_workers=2) as workers:
                    claims = list(workers.map(self.store.claim_due, [self.aos+10]*2))
                self.assertEqual(sum(ticket is not None for ticket in claims), 1)
                ticket = next(ticket for ticket in claims if ticket)
                sent.append((ticket["batch_id"], ticket["step_id"]))
                self.assertEqual((ticket["batch_id"],ticket["step_id"]), (batch["batch_id"],step_id))
                self.store.finish_sim(ticket["id"], "ACKED", self.ack(ticket))
        self.assertEqual(len(sent), 6)

    def test_immediate_dump_does_not_interleave_active_batch(self):
        self.store.reserve_sim_batch(self.imported()["sha256"], "operator01")
        with patch("services.webapp.ros_workbench.time.time", return_value=self.aos+10):
            self.store.request_sim_tm("operator01")
        ticket = self.store.claim_due(self.aos+10)
        self.assertIsNone(self.store.claim_sim_tm_request(self.aos+10))
        self.store.finish_sim(ticket["id"], "ACKED", self.ack(ticket))
        self.assertIsNone(self.store.claim_sim_tm_request(self.aos+10))

    def test_separate_store_workers_cannot_claim_two_commands_while_ack_pending(self):
        second_store = RosDraftStore(self.path)
        self.addCleanup(second_store.close)
        batch = self.store.reserve_sim_batch(self.imported()["sha256"], "operator01")
        with ThreadPoolExecutor(max_workers=2) as workers:
            claims = list(workers.map(lambda store: store.claim_due(self.aos+10),
                                     [self.store, second_store]))
        self.assertEqual(sum(ticket is not None for ticket in claims), 1)
        ticket = next(ticket for ticket in claims if ticket)
        self.assertEqual(ticket["batch_id"],batch["batch_id"])
        self.assertIsNone(self.store.claim_due(self.aos+10))
        self.assertIsNone(second_store.claim_due(self.aos+10))
        self.store.finish_sim(ticket["id"], "ACKED", self.ack(ticket))
        self.assertEqual(second_store.claim_due(self.aos+10)["step_id"],2)

    def test_decision_reads_hold_write_lock_against_cross_worker_batch_race(self):
        """Try worker 2 exactly after worker 1 read its active-batch decision.

        Without BEGIN IMMEDIATE, worker 2 commits batch 1's first command here
        and worker 1 can select batch 2 using its now-stale active-batch result.
        """
        second_store = RosDraftStore(self.path)
        self.addCleanup(second_store.close)
        second_store.db.execute("PRAGMA busy_timeout=0")
        first = self.store.reserve_sim_batch(self.imported()["sha256"], "operator01")
        self.store.reserve_sim_batch(self.imported(revision=2)["sha256"], "operator01")
        observed = []
        trace = []
        raw = self.store.db
        raw.set_trace_callback(trace.append)

        class ObservedCursor:
            def __init__(self, cursor):
                self.cursor = cursor

            def fetchone(self):
                result = self.cursor.fetchone()
                try:
                    observed.append(second_store.claim_due(self_aos+10))
                except sqlite3.OperationalError as exc:
                    observed.append(str(exc))
                return result

        class ObservedConnection:
            def __init__(self):
                self.once = False

            def __enter__(self):
                raw.__enter__()
                return self

            def __exit__(self, *args):
                return raw.__exit__(*args)

            def execute(self, sql, *args):
                cursor = raw.execute(sql, *args)
                if not self.once and sql.startswith("SELECT b.id FROM sim_reservation_batches"):
                    self.once = True
                    return ObservedCursor(cursor)
                return cursor

            def __getattr__(self, name):
                return getattr(raw, name)

        self_aos = self.aos
        self.store.db = ObservedConnection()
        claimed = self.store.claim_due(self.aos+10)
        self.assertEqual(trace[0], "BEGIN IMMEDIATE")
        self.assertEqual(observed, ["database is locked"])
        self.assertEqual(claimed["batch_id"], first["batch_id"])
        self.assertEqual(sum(row["status"] == "SENDING" for row in self.store.list_sim_reservations()), 1)

    def test_immediate_claim_begins_before_reads_and_excludes_new_batch(self):
        second_store = RosDraftStore(self.path)
        self.addCleanup(second_store.close)
        batch = self.store.reserve_sim_batch(self.imported()["sha256"], "operator01")
        with patch("services.webapp.ros_workbench.time.time", return_value=self.aos+10):
            self.store.request_sim_tm("operator01")
        trace = []
        self.store.db.set_trace_callback(trace.append)
        immediate = self.store.claim_sim_tm_request(self.aos+10)
        self.assertEqual(trace[0], "BEGIN IMMEDIATE")
        self.assertEqual(immediate["command_name"], "hk_dump")
        self.assertIsNone(second_store.claim_due(self.aos+10))
        self.assertIsNone(second_store.claim_sim_tm_request(self.aos+10))
        self.store.finish_sim_tm_request(immediate["id"], "ACKED", {
            "command_id":4116, "command_sequence":immediate["command_sequence"],
            "result":0, "reason":0, "executed_at_utc_ms":(self.aos+10)*1000})
        claimed = second_store.claim_due(self.aos+10)
        self.assertEqual(claimed["batch_id"],batch["batch_id"])
        self.assertIsNone(self.store.claim_sim_tm_request(self.aos+10))

    def test_new_batch_waits_for_an_inflight_legacy_individual_reservation(self):
        legacy = self.imported()
        self.store.reserve_sim(legacy["sha256"], 1, "operator01")
        batch = self.store.reserve_sim_batch(self.imported(revision=2)["sha256"], "operator01")
        first = self.store.claim_due(self.aos+10)
        self.assertIsNone(first["batch_id"])
        self.assertIsNone(self.store.claim_due(self.aos+10))
        self.store.finish_sim(first["id"], "ACKED", self.ack(first))
        self.assertEqual(self.store.claim_due(self.aos+10)["batch_id"],batch["batch_id"])

    def test_mixed_legacy_offsets_require_explicit_resave(self):
        sequence = self.imported(offsets=[10, 20, 30])
        with self.assertRaisesRegex(DraftError, "sim_sequence_mixed_start"):
            self.store.reserve_sim_batch(sequence["sha256"], "operator01")
        self.assertEqual(self.store.list_sim_reservations(), [])

    def test_old_database_migration_preserves_existing_reservations(self):
        self.store.close()
        self.store = None
        legacy = Path(self.folder.name)/"legacy.db"
        sequence = normalize_sim_handoff(handoff(self.aos))
        with sqlite3.connect(legacy) as db:
            db.execute("CREATE TABLE sim_sequences (sha256 TEXT PRIMARY KEY,pass_id TEXT NOT NULL,"
                "schedule_sha256 TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,"
                "imported_by TEXT NOT NULL,imported_at INTEGER NOT NULL,"
                "UNIQUE(pass_id,schedule_sha256,revision))")
            db.execute("CREATE TABLE sim_reservations (id INTEGER PRIMARY KEY,sha256 TEXT NOT NULL,"
                "step_id INTEGER NOT NULL,target_utc_s INTEGER NOT NULL,created_by TEXT NOT NULL,"
                "created_at INTEGER NOT NULL,status TEXT NOT NULL,ack TEXT,finished_at INTEGER)")
            db.execute("INSERT INTO sim_sequences VALUES (?,?,?,?,?,?,?)",(sequence["sha256"],
                sequence["pass_id"],sequence["schedule_sha256"],1,json.dumps(sequence),"planner01",int(time.time())))
            db.execute("INSERT INTO sim_reservations VALUES (501,?,?,?,?,?,'SCHEDULED',NULL,NULL)",
                (sequence["sha256"],1,self.aos+60,"operator01",int(time.time())))
        db.close()
        if os.name == "posix":
            legacy.chmod(0o600)
        self.store = RosDraftStore(legacy)
        record = self.store.list_sim_reservations()[0]
        self.assertEqual(record["id"], 501)
        self.assertEqual(record["status"], "SCHEDULED")
        self.assertIsNone(record["batch_id"])
        self.assertEqual(record["start_offset_s"], 60)


class SequenceBatchHttpTests(unittest.TestCase):
    setUp = sim_http.SimTcHttpTests.setUp
    tearDown = sim_http.SimTcHttpTests.tearDown
    request = sim_http.SimTcHttpTests.request

    def authorization(self, role="planner"):
        name = "batch"+role
        passphrase = secrets.token_urlsafe(24)
        self.auth.create_user(name, role, passphrase)
        status, headers, login = self.request("/api/login",body={"username":name,"password":passphrase})
        self.assertEqual(status, 200)
        cookie = {"Cookie":headers["Set-Cookie"].split(";",1)[0]}
        return cookie, {**cookie,"X-CSRF-Token":login["csrf"]}

    def saved_handoff(self, authorized):
        reference = {"pass_id":"yonsei-001","schedule_sha256":"a"*64,"expected_revision":0}
        steps = [{"offset_s":10,"command_name":name,"parameters":{}}
                 for name in ("mm_get_mode","eps_get_hk")]
        status, _, _ = self.request("/api/mps/test-sequences",body={**reference,"steps":steps},headers=authorized)
        self.assertEqual(status, 200)
        status, _, sequence = self.request("/api/mps/test-sequences/handoff",
            body={**reference,"expected_revision":1},headers=authorized)
        self.assertEqual(status, 200)
        return sequence

    def test_whole_sequence_http_authorization_csrf_and_batch_cancel(self):
        try:
            status, _, _ = self.request("/api/ros/test-sim-reservations",body={"sha256":"a"*64})
        except URLError as exc:
            if isinstance(exc.reason, PermissionError):
                self.skipTest("local sandbox denies loopback connections")
            raise
        self.assertEqual(status, 403)
        cookie, authorized = self.authorization()
        sequence = self.saved_handoff(authorized)
        body = {"sha256":sequence["sha256"]}
        self.assertEqual(self.request("/api/ros/test-sim-reservations",body=body,headers=cookie)[0],403)
        _, audit = self.authorization("auditor")
        self.assertEqual(self.request("/api/ros/test-sim-reservations",body=body,headers=audit)[0],403)
        status, _, batch = self.request("/api/ros/test-sim-reservations",body=body,headers=authorized)
        self.assertEqual(status, 200)
        self.assertEqual(batch["count"],2)
        self.assertEqual({r["batch_id"] for r in batch["reservations"]},{batch["batch_id"]})
        status, _, result = self.request("/api/ros/test-sim-reservations/cancel",
            body={"batch_id":batch["batch_id"]},headers=authorized)
        self.assertEqual(status,200)
        self.assertEqual(result["count"],2)

    def test_whole_sequence_reservation_rechecks_current_fds_and_mps_revision(self):
        try:
            _, authorized = self.authorization()
        except URLError as exc:
            if isinstance(exc.reason, PermissionError):
                self.skipTest("local sandbox denies loopback connections")
            raise
        sequence = self.saved_handoff(authorized)
        self.schedule["schedule_sha256"] = "b"*64
        status, _, _ = self.request("/api/ros/test-sim-reservations",
            body={"sha256":sequence["sha256"]},headers=authorized)
        self.assertEqual(status,409)
        self.assertEqual(self.ros.drafts.list_sim_reservations(),[])


if __name__ == "__main__":
    unittest.main()
