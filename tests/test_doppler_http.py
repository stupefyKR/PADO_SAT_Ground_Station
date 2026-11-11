"""Authenticated MPS Doppler reads use the FDS-published CSV unchanged."""

import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from services.webapp.auth import AuthStore
from services.webapp.main import AppServer, doppler_query, initialize_internal_token
from tests.test_doppler_profiles import make_run
from tests.test_stage9_http import FakeHasher
from tools.publish_fds_view import publish


ROOT = Path(__file__).resolve().parents[1]


class DopplerQueryTests(unittest.TestCase):
    def test_only_pass_digest_and_known_format_are_accepted(self):
        query = "pass_id=yonsei-001&schedule_sha256=" + "a" * 64
        self.assertEqual(doppler_query(query)["format"], "json")
        self.assertEqual(doppler_query(query + "&format=csv")["format"], "csv")
        for invalid in ("", query + "&pass_id=kari-001", query + "&path=/etc/passwd",
                        query + "&format=xml", query.replace("yonsei-001", "../secret"),
                        query.replace("a" * 64, "z" * 64), query + "&format=csv&format=json"):
            with self.subTest(query=invalid), self.assertRaises(ValueError):
                doppler_query(invalid)


class DopplerHttpTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "out").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="doppler-http-", dir=ROOT / "out")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        run, _, self.digest, self.raw = make_run(root)
        self.view = publish(run, root / "served")
        token = root / "token"
        initialize_internal_token(token)
        self.auth = AuthStore(root / "users.db", FakeHasher())
        self.addCleanup(self.auth.close)
        self.run_password = secrets.token_urlsafe(24)
        self.auth.create_user("audit01", "auditor", self.run_password)
        with patch.dict(os.environ, {"PADO_ENVIRONMENT": "legacy"}):
            self.server = AppServer(("127.0.0.1", 0), mode="portal", transport="http",
                internal_token_file=token, auth=self.auth, orbit_view=self.view)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, *, cookie=None, body=None):
        headers = {"Cookie": cookie} if cookie else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base + path, headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(request, timeout=4) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()
        except URLError as error:
            if isinstance(error.reason, PermissionError):
                self.skipTest("loopback denied locally; HTTP integration runs on Linux")
            raise

    def login(self):
        status, headers, _ = self.request("/api/login", body={
            "username": "audit01", "password": self.run_password})
        self.assertEqual(status, 200)
        return headers["Set-Cookie"].split(";", 1)[0]

    def url(self, identifier="yonsei-001", digest=None):
        return f"/api/fds/doppler?pass_id={identifier}&schedule_sha256={digest or self.digest}"

    def test_both_stations_json_and_exact_csv_require_login(self):
        self.assertEqual(self.request(self.url())[0], 401)
        self.assertEqual(self.request(self.url() + "&format=csv")[0], 401)
        cookie = self.login()
        for identifier in ("yonsei-001", "kari-001"):
            status, _, raw_json = self.request(self.url(identifier), cookie=cookie)
            self.assertEqual(status, 200, raw_json)
            payload = json.loads(raw_json)
            self.assertEqual(payload["schema"], "fds.doppler-profile.v1")
            self.assertEqual(payload["pass_id"], identifier)
            self.assertEqual(payload["schedule_sha256"], self.digest)
            self.assertGreaterEqual(len(payload["samples"]), 2)
            status, headers, csv = self.request(self.url(identifier) + "&format=csv", cookie=cookie)
            self.assertEqual(status, 200, csv)
            self.assertEqual(csv, self.raw[identifier])
            self.assertIn("text/csv", headers["Content-Type"])
            self.assertIn(identifier, headers["Content-Disposition"])
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(headers["X-Pado-Schedule-SHA256"], self.digest)
            self.assertEqual(headers["X-Pado-Profile-SHA256"], payload["profile_sha256"])

    def test_changed_schedule_invalid_selection_and_environment_do_not_leak_csv(self):
        cookie = self.login()
        status, _, body = self.request(self.url(digest="f" * 64), cookie=cookie)
        self.assertEqual(status, 409, body)
        self.assertEqual(json.loads(body)["error"], "schedule_changed")
        self.assertEqual(self.request(self.url("yonsei-999"), cookie=cookie)[0], 404)
        self.assertEqual(self.request(self.url() + "&path=../secrets", cookie=cookie)[0], 400)
        self.server.environment = "operations"
        status, _, body = self.request(self.url() + "&format=csv", cookie=cookie)
        self.assertEqual(status, 503, body)
        self.assertEqual(json.loads(body)["error"], "doppler_unavailable")


if __name__ == "__main__":
    unittest.main()
