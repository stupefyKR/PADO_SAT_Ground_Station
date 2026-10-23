"""HTTP boundary for the MPS draft → ROS simulation handoff."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from services.webapp.auth import AuthStore
from services.webapp.main import AppServer, initialize_internal_token


class FakeHasher:
    def hash(self, password):
        return "$argon2id$test$" + password[::-1]

    def verify(self, encoded, password):
        return encoded == self.hash(password)


class SimTcHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.token = self.folder / "internal-token"
        initialize_internal_token(self.token)
        self.auth = AuthStore(self.folder / "users.db", FakeHasher())
        self.auth.create_user("planner01", "planner", "valid-password-123")
        now = int(time.time())
        self.schedule = {
            "schedule_state": "validated", "schedule_sha256": "a" * 64,
            "operational": False, "passes": [{
                "pass_id": "yonsei-001", "station_id": "yonsei", "band": "UHF",
                "aos_utc_s": now + 180, "los_utc_s": now + 780,
                "peak_utc_s": now + 480, "minimum_elevation_deg": 10,
                "max_elevation_deg": 42, "clipped_start": False,
                "clipped_end": False,
            }],
        }
        self.schedule_patch = patch("services.webapp.main.mps_passes",
                                    return_value=self.schedule)
        self.schedule_patch.start()
        self.mps = AppServer(("127.0.0.1", 0), mode="mps",
                             transport="http", internal_token_file=self.token,
                             candidate_db=self.folder / "mps.db")
        self.ros = AppServer(("127.0.0.1", 0), mode="ros",
                             transport="http", internal_token_file=self.token,
                             draft_db=self.folder / "ros.db")
        self.portal = AppServer(
            ("127.0.0.1", 0), mode="portal", transport="http",
            internal_token_file=self.token, auth=self.auth,
            mps_url=f"http://127.0.0.1:{self.mps.server_port}",
            ros_url=f"http://127.0.0.1:{self.ros.server_port}")
        self.threads = []
        for server in (self.mps, self.ros, self.portal):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.threads.append(thread)

    def tearDown(self):
        for server in (self.portal, self.ros, self.mps):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)
        self.mps.candidates.close()
        self.ros.drafts.close()
        self.auth.close()
        self.schedule_patch.stop()
        self.temp.cleanup()

    def request(self, path, *, body=None, headers=None):
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        req = Request(f"http://127.0.0.1:{self.portal.server_port}" + path,
                      data=payload, headers={
                          **({"Content-Type": "application/json"} if payload else {}),
                          **(headers or {})})
        try:
            response = urlopen(req, timeout=3)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, dict(response.headers), json.loads(response.read())

    def test_authenticated_server_side_handoff_and_reservation_lifecycle(self):
        reference = {"pass_id": "yonsei-001", "schedule_sha256": "a" * 64,
                     "expected_revision": 1}
        try:
            status, _, _ = self.request("/api/ros/test-sim-sequences")
        except URLError as exc:
            if isinstance(exc.reason, PermissionError):
                self.skipTest("local sandbox denies loopback connections")
            raise
        self.assertEqual(status, 401)
        status, _, _ = self.request("/api/mps/test-sequences/handoff", body=reference)
        self.assertEqual(status, 403)
        status, headers, login = self.request("/api/login", body={
            "username": "planner01", "password": "valid-password-123"})
        self.assertEqual(status, 200)
        cookie = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        authorized = {**cookie, "X-CSRF-Token": login["csrf"]}
        saved_steps = [{"offset_s": 20, "command_name": "hk_get", "parameters": {}}]
        save = {**reference, "expected_revision": 0, "steps": saved_steps}
        status, _, _ = self.request("/api/mps/test-sequences", body=save,
                                    headers=cookie)
        self.assertEqual(status, 403)
        status, _, saved = self.request("/api/mps/test-sequences", body=save,
                                        headers=authorized)
        self.assertEqual(status, 200)
        self.assertEqual(saved["revision"], 1)

        status, _, _ = self.request("/api/mps/test-sequences/handoff",
                                    body=reference, headers=cookie)
        self.assertEqual(status, 403)
        status, _, _ = self.request("/api/mps/test-sequences/handoff",
                                    body={**reference, "steps": [{
                                        "offset_s": 20, "command_name": "mm_get_mode",
                                        "parameters": {}}]}, headers=authorized)
        self.assertEqual(status, 400)
        status, _, _ = self.request("/api/mps/test-sequences/handoff",
                                    body={**reference, "expected_revision": 2},
                                    headers=authorized)
        self.assertEqual(status, 409)
        status, _, handoff = self.request("/api/mps/test-sequences/handoff",
                                           body=reference, headers=authorized)
        self.assertEqual(status, 200)
        self.assertEqual(handoff["source"], "SIM_MPS_HANDOFF")
        self.assertEqual(handoff["steps"][0]["command_name"], "hk_get")
        self.assertEqual(handoff["steps"][0]["offset_s"], 20)
        self.assertFalse(handoff["operational"])
        status, _, listed = self.request("/api/ros/test-sim-sequences", headers=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(listed["items"][0]["sha256"], handoff["sha256"])

        reservation = {"sha256": handoff["sha256"], "step_id": 1}
        status, _, _ = self.request("/api/ros/test-sim-reservations",
                                    body=reservation, headers=cookie)
        self.assertEqual(status, 403)
        status, _, created = self.request("/api/ros/test-sim-reservations",
                                           body=reservation, headers=authorized)
        self.assertEqual(status, 200)
        self.assertEqual(created["status"], "SCHEDULED")
        self.assertEqual(created["target_utc_s"], self.schedule["passes"][0]["aos_utc_s"] + 20)
        self.assertEqual(created["command_name"], "hk_get")
        status, _, listed = self.request("/api/ros/test-sim-reservations", headers=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(listed["items"][0]["id"], created["id"])
        status, _, cancelled = self.request("/api/ros/test-sim-reservations/cancel",
                                             body={"id": created["id"]},
                                             headers=authorized)
        self.assertEqual(status, 200)
        self.assertEqual(cancelled["status"], "CANCELLED")
        status, _, listed = self.request("/api/ros/test-sim-reservations", headers=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(listed["items"][0]["status"], "CANCELLED")
        self.schedule["schedule_sha256"] = "b" * 64
        status, _, stale = self.request("/api/ros/test-sim-reservations",
                                        body=reservation, headers=authorized)
        self.assertEqual(status, 409)
        self.assertEqual(stale["error"], "stale_schedule")
        status, _, _ = self.request("/api/ros/test-sim-handoffs",
                                    body=handoff, headers=authorized)
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
