"""Read-only ROS simulator ACK/SOH feedback shown on the MPS workbench."""

import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from services.webapp.auth import AuthStore
from services.webapp.main import AppServer, initialize_internal_token, test_n10_feedback
from services.webapp.ros_workbench import DraftError, RosDraftStore
from tools.pado_packet_codec import load_catalog


class FakeHasher:
    def hash(self, password):
        return "$argon2id$test$" + password[::-1]

    def verify(self, encoded, password):
        return encoded == self.hash(password)


def utc(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class N10FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.preview = self.folder / "test_tm_snapshot.json"
        self.store = RosDraftStore(self.folder / "ros.db")
        self.addCleanup(self.store.close)
        self.now = int(time.time())

    def snapshot(self, received, sample, value=5.0):
        self.preview.write_text(json.dumps({
            "type": "TEST_ONLY_TM_SNAPSHOT", "non_operational": True,
            "received_utc": utc(received), "attitude": None,
            "sensors": [{"id": "bus_v", "name": "시험 버스 전압", "group": "EPS",
                         "unit": "V", "lower": 4.5, "upper": 5.5,
                         "samples": [{"utc": utc(sample), "value": value}]}]
        }), encoding="utf-8")

    def acknowledged_reservation(self):
        envelope = {"schema": "pado.sim-sequence.v1",
                    "source": "MPS_TEST_DRAFT", "non_operational": True,
                    "pass_id": "yonsei-001", "station_id": "yonsei", "band": "UHF",
                    "schedule_sha256": "a" * 64,
                    "aos_utc_s": self.now + 120,
                    "los_utc_s": self.now + 720, "revision": 1,
                    "steps": [{"offset_s": 20, "command_name": "hk_get",
                               "parameters": {}}]}
        handoff = self.store.import_sim_handoff(envelope, "planner01")
        reserved = self.store.reserve_sim(handoff["sha256"], 1, "planner01")
        claimed = self.store.claim_due(reserved["target_utc_s"])
        self.assertEqual(claimed["id"], reserved["id"])
        command_id = next(item["id"] for item in load_catalog()["commands"]
                          if item["name"] == "hk_get")
        ack = {"command_id": command_id,
               "command_sequence": (reserved["id"] - 1) % 65535 + 1,
               "result": 0, "reason": 0,
               "executed_at_utc_ms": reserved["target_utc_s"] * 1000}
        self.store.finish_sim(reserved["id"], "ACKED", ack)
        return reserved

    def test_ack_and_soh_are_bounded_test_only_projection(self):
        reserved = self.acknowledged_reservation()
        self.snapshot(self.now, self.now, value=5.6)
        feedback = test_n10_feedback(self.store, self.preview, now_s=self.now)
        self.assertEqual(feedback["schema"], "pado.sim-n10-feedback.v1")
        self.assertTrue(feedback["non_operational"])
        self.assertEqual(feedback["source"], "ROS_SIM_ONLY")
        self.assertEqual(feedback["bus_soh_state"], "CURRENT")
        self.assertEqual(feedback["bus_soh"]["sensors"][0]["limit_state"],
                         "OUT_OF_RANGE")
        item = feedback["ack_items"][0]
        self.assertEqual(item["reservation_id"], reserved["id"])
        self.assertEqual(item["pass_id"], "yonsei-001")
        self.assertEqual(item["status"], "ACKED")
        self.assertEqual(item["kind"], "ACK")
        self.assertNotIn("parameters", json.dumps(feedback))

    def test_missing_stale_and_corrupt_soh_never_look_normal(self):
        absent = test_n10_feedback(self.store, self.preview, now_s=self.now)
        self.assertEqual(absent["bus_soh_state"], "UNAVAILABLE")
        self.assertIsNone(absent["bus_soh"])
        self.snapshot(self.now - 301, self.now - 301)
        stale = test_n10_feedback(self.store, self.preview, now_s=self.now)
        self.assertEqual(stale["bus_soh_state"], "STALE")
        self.assertEqual(stale["bus_soh"]["sensors"][0]["limit_state"],
                         "INDETERMINATE")
        self.preview.write_text('{"type":"bad"}', encoding="utf-8")
        corrupt = test_n10_feedback(self.store, self.preview, now_s=self.now)
        self.assertEqual(corrupt["bus_soh_state"], "INVALID")
        self.assertIsNone(corrupt["bus_soh"])

    def test_tampered_ack_command_identity_is_rejected(self):
        reservation = self.acknowledged_reservation()
        with self.store.db:
            row = self.store.db.execute(
                "SELECT ack FROM sim_reservations WHERE id=?",
                (reservation["id"],)).fetchone()
            ack = json.loads(row[0])
            ack["command_id"] += 1
            self.store.db.execute("UPDATE sim_reservations SET ack=? WHERE id=?",
                                  (json.dumps(ack), reservation["id"]))
        with self.assertRaises(DraftError):
            self.store.n10_test_ack_items()

    def test_portal_read_only_route_requires_session_and_backend_identity(self):
        token = self.folder / "internal-token"
        initialize_internal_token(token)
        auth = AuthStore(self.folder / "users.db", FakeHasher())
        auth.create_user("planner01", "planner", "valid-password-123")
        ros = AppServer(("127.0.0.1", 0), mode="ros", transport="http",
                        internal_token_file=token, tm_preview=self.preview,
                        draft_db=self.folder / "ros-http.db")
        portal = AppServer(("127.0.0.1", 0), mode="portal", transport="http",
                           internal_token_file=token, auth=auth,
                           ros_url=f"http://127.0.0.1:{ros.server_port}")
        threads = []
        try:
            for server in (ros, portal):
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                threads.append(thread)

            def request(base, path, body=None, headers=None):
                data = json.dumps(body).encode() if body is not None else None
                req = Request(base + path, data=data,
                              headers={**({"Content-Type": "application/json"} if data else {}),
                                       **(headers or {})})
                try:
                    response = urlopen(req, timeout=3)
                except HTTPError as exc:
                    response = exc
                with response:
                    return response.status, dict(response.headers), json.loads(response.read())

            base = f"http://127.0.0.1:{portal.server_port}"
            try:
                status, _, _ = request(base, "/api/mps/test-ros-feedback")
            except URLError as exc:
                if isinstance(exc.reason, PermissionError):
                    self.skipTest("local sandbox denies loopback connections")
                raise
            self.assertEqual(status, 401)
            status, _, _ = request(f"http://127.0.0.1:{ros.server_port}",
                                   "/test-n10-feedback")
            self.assertEqual(status, 403)
            status, headers, _ = request(base, "/api/login", body={
                "username": "planner01", "password": "valid-password-123"})
            self.assertEqual(status, 200)
            cookie = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
            self.snapshot(self.now, self.now)
            status, _, feedback = request(base, "/api/mps/test-ros-feedback",
                                          headers=cookie)
            self.assertEqual(status, 200)
            self.assertEqual(feedback["bus_soh_state"], "CURRENT")
            self.assertEqual(feedback["ack_items"], [])
            status, _, _ = request(base, "/api/mps/test-ros-feedback",
                                   body={"foo": "bar"}, headers=cookie)
            self.assertEqual(status, 404)
        finally:
            for server in (portal, ros):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=2)
            ros.drafts.close()
            auth.close()


if __name__ == "__main__":
    unittest.main()
