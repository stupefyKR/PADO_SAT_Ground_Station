import json
import hashlib
import os
import shutil
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from services.webapp.auth import AuthStore
from services.webapp.main import AppServer, ROOT
from tests.test_ros_workbench import pair as test_sequence_pair
from tests.test_tm_preview import snapshot as test_tm_snapshot
from tools.publish_mps_schedule import publish as publish_mps_schedule


CORE_BIN = Path(os.environ.get(
    "PADO_STAGE9_CORE_PROBE_TEST",
    Path(__file__).resolve().parents[1] / "build-linux-release" / "gs_stage9_core_probe",
))


class FakeHasher:
    def hash(self, password):
        return "$argon2id$test$" + password[::-1]

    def verify(self, encoded, password):
        return encoded == self.hash(password)


def run_openssl(*args):
    subprocess.run(["openssl", *map(str, args)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def make_cert(folder, ca_cert, ca_key, name):
    key = folder / f"{name}.key"
    csr = folder / f"{name}.csr"
    cert = folder / f"{name}.crt"
    ext = folder / f"{name}.ext"
    ext.write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\n"
                   "extendedKeyUsage=serverAuth,clientAuth\n", encoding="ascii")
    run_openssl("req", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
                "-out", csr, "-subj", f"/CN={name}")
    run_openssl("x509", "-req", "-in", csr, "-CA", ca_cert,
                "-CAkey", ca_key, "-CAcreateserial", "-out", cert,
                "-days", "1", "-extfile", ext)
    return cert, key


@unittest.skipUnless(shutil.which("openssl") and CORE_BIN.is_file(),
                     "openssl or Linux C core probe unavailable")
class WebBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        cls.ca_key = cls.folder / "ca.key"
        cls.ca_cert = cls.folder / "ca.crt"
        run_openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", cls.ca_key, "-out", cls.ca_cert,
                    "-days", "1", "-subj", "/CN=PADO-test-CA")
        cls.portal_cert, cls.portal_key = make_cert(
            cls.folder, cls.ca_cert, cls.ca_key, "pado-portal")
        cls.ros_cert, cls.ros_key = make_cert(
            cls.folder, cls.ca_cert, cls.ca_key, "pado-ros")
        cls.mps_cert, cls.mps_key = make_cert(
            cls.folder, cls.ca_cert, cls.ca_key, "pado-mps")
        cls.auth = AuthStore(cls.folder / "users.db", FakeHasher())
        cls.auth.create_user("planner01", "planner", "valid-password-123")
        cls.core_processes = []
        cls.core_sockets = {}
        cls.schedule_root = cls.folder / "mps-schedules"
        cls.schedule_root.mkdir()
        for mode, source in (("ros", cls.folder / "ros-tm.record"),
                             ("mps", ROOT / "interfaces/command_db/commands.json")):
            endpoint = cls.folder / f"{mode}.sock"
            process = subprocess.Popen(
                [str(CORE_BIN), "serve", mode, str(endpoint), str(source)] +
                ([str(cls.schedule_root)] if mode == "mps" else []),
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            cls.core_processes.append(process)
            cls.core_sockets[mode] = endpoint
            until = time.monotonic() + 3
            while not endpoint.exists() and time.monotonic() < until:
                if process.poll() is not None:
                    break
                time.sleep(0.02)
            if not endpoint.exists():
                raise RuntimeError(f"{mode} C reader did not start")
        cls.servers = []
        cls.threads = []
        for mode, cert, key in (("ros", cls.ros_cert, cls.ros_key),
                                ("mps", cls.mps_cert, cls.mps_key)):
            server = AppServer(("127.0.0.1", 0), mode=mode, cert=cert,
                               key=key, ca=cls.ca_cert,
                               core_socket=cls.core_sockets[mode],
                               tm_preview=cls.folder / "test_tm_snapshot.json" if mode == "ros" else None,
                               draft_db=cls.folder / "ros-drafts.db" if mode == "ros" else None,
                               candidate_db=cls.folder / "mps-candidates.db" if mode == "mps" else None)
            cls.servers.append(server)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cls.threads.append(thread)
        cls.portal = AppServer(
            ("127.0.0.1", 0), mode="portal", cert=cls.portal_cert,
            key=cls.portal_key, ca=cls.ca_cert, auth=cls.auth,
            ros_url=f"https://localhost:{cls.servers[0].server_port}",
            mps_url=f"https://localhost:{cls.servers[1].server_port}",
            client_cert=cls.portal_cert, client_key=cls.portal_key)
        cls.servers.append(cls.portal)
        thread = threading.Thread(target=cls.portal.serve_forever, daemon=True)
        thread.start()
        cls.threads.append(thread)
        cls.context = ssl.create_default_context(cafile=str(cls.ca_cert))
        cls.base = f"https://localhost:{cls.portal.server_port}"

    @classmethod
    def tearDownClass(cls):
        for server in cls.servers:
            server.shutdown()
            server.server_close()
            if server.drafts:
                server.drafts.close()
            if server.candidates:
                server.candidates.close()
        for thread in cls.threads:
            thread.join(timeout=3)
        for process in cls.core_processes:
            process.terminate()
            process.wait(timeout=3)
            process.stderr.close()
        cls.auth.close()
        cls.temp.cleanup()

    def request(self, path, *, method="GET", body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        extra = dict(headers or {})
        if body is not None:
            extra["Content-Type"] = "application/json"
        req = Request(self.base + path, data=data, headers=extra, method=method)
        try:
            response = urlopen(req, context=self.context, timeout=3)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, dict(response.headers), response.read()

    def test_login_proxy_and_logout(self):
        status, headers, _ = self.request("/ops")
        self.assertEqual(status, 200)  # urllib follows redirect to /login
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        status, _, body = self.request("/api/ros/overview")
        self.assertEqual(status, 401)
        status, _, _ = self.request("/api/login", method="POST", body={
            "username": "planner01", "password": "wrong"})
        self.assertEqual(status, 401)
        status, headers, body = self.request("/api/login", method="POST", body={
            "username": "planner01", "password": "valid-password-123"})
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Strict", cookie)
        token_cookie = cookie.split(";", 1)[0]
        csrf = json.loads(body)["csrf"]
        auth_header = {"Cookie": token_cookie}
        status, _, body = self.request("/api/ros/overview", headers=auth_header)
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["tc_send_available"])
        self.assertEqual(json.loads(body)["sim_frame_count"], 0)
        status, _, body = self.request("/api/mps/catalog", headers=auth_header)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["commands"], [])
        self.assertEqual(json.loads(body)["version"], 1)
        self.assertEqual(len(json.loads(body)["draft_commands"]), 58)
        self.assertFalse(json.loads(body)["operational"])
        status, _, body = self.request("/api/mps/passes", headers=auth_header)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["passes"], [])
        status, _, body = self.request("/api/mps/test-candidates", headers=auth_header)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["items"], [])
        status, _, _ = self.request("/api/ros/tc", headers=auth_header)
        self.assertEqual(status, 404)
        status, _, _ = self.request("/api/logout", method="POST", headers=auth_header)
        self.assertEqual(status, 403)
        status, _, _ = self.request("/api/logout", method="POST", headers={
            **auth_header, "X-CSRF-Token": csrf})
        self.assertEqual(status, 200)
        status, _, _ = self.request("/api/session", headers=auth_header)
        self.assertEqual(status, 401)

    def test_direct_backend_without_client_certificate_is_denied(self):
        req = Request(f"https://localhost:{self.servers[0].server_port}/overview",
                      headers={"X-Pado-Actor": "planner01", "X-Pado-Role": "planner"})
        with self.assertRaises((URLError, ConnectionError, ssl.SSLError)):
            urlopen(req, context=self.context, timeout=3)

    def test_test_only_ros_draft_auth_csrf_and_no_tc_route(self):
        raw, manifest = test_sequence_pair(int(time.time()) + 3600)
        status, _, _ = self.request("/api/ros/test-sequences", method="POST",
                                    body={"sequence_text": raw, "manifest_text": manifest})
        self.assertEqual(status, 403)
        status, headers, body = self.request("/api/login", method="POST", body={
            "username": "planner01", "password": "valid-password-123"})
        self.assertEqual(status, 200)
        auth = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        csrf = json.loads(body)["csrf"]
        payload = {"sequence_text": raw, "manifest_text": manifest}
        status, _, _ = self.request("/api/ros/test-sequences", method="POST",
                                    body=payload, headers=auth)
        self.assertEqual(status, 403)
        status, _, body = self.request("/api/ros/test-sequences", method="POST",
                                       body=payload, headers={**auth, "X-CSRF-Token": csrf})
        self.assertEqual(status, 200)
        sequence = json.loads(body)
        self.assertFalse(sequence["operational"])
        status, _, body = self.request("/api/ros/test-reservations", method="POST",
                                       body={"sha256": sequence["sha256"], "step_id": 1,
                                             "target_utc_s": sequence["aos_utc_s"] + 60},
                                       headers={**auth, "X-CSRF-Token": csrf})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "DRAFT_ONLY")
        status, _, body = self.request("/api/ros/test-reservations", headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["items"]), 1)
        status, _, _ = self.request("/api/ros/tc", method="POST", body={},
                                    headers={**auth, "X-CSRF-Token": csrf})
        self.assertEqual(status, 404)

    def test_mps_candidates_require_schedule_and_csrf(self):
        status, _, _ = self.request("/api/mps/test-candidates", method="POST", body={})
        self.assertEqual(status, 403)
        status, headers, body = self.request("/api/login", method="POST", body={
            "username": "planner01", "password": "valid-password-123"})
        self.assertEqual(status, 200)
        auth = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        csrf = json.loads(body)["csrf"]
        payload = {"pass_id": "yonsei-001", "schedule_sha256": "a" * 64,
                   "expected_revision": 0, "candidate_ids": ["HK_GET"]}
        status, _, _ = self.request("/api/mps/test-candidates", method="POST",
                                    body=payload, headers=auth)
        self.assertEqual(status, 403)
        status, _, _ = self.request("/api/mps/test-candidates", method="POST",
                                    body=payload, headers={**auth, "X-CSRF-Token": csrf})
        self.assertEqual(status, 409)
        run = self.folder / "fds-20261023T100000Z-1234abcd"
        run.mkdir(exist_ok=True)
        n13 = ("schema,station_id,band,pass_id,aos_utc,los_utc,peak_utc,"
               "max_elevation_deg,minimum_elevation_deg,clipped_start,clipped_end\n"
               "fds.contact-schedule.v1,yonsei,UHF,yonsei-001,2026-10-23T10:00:00.125Z,"
               "2026-10-23T10:08:00.250Z,2026-10-23T10:04:00.000Z,42.5,10.0,false,false\n")
        (run / "contact_schedule.csv").write_text(n13, encoding="ascii")
        digest = hashlib.sha256(n13.encode("ascii")).hexdigest()
        (run / "contact_schedule.csv.manifest.json").write_text(json.dumps({
            "file": "contact_schedule.csv", "type": "CONTACT_SCHEDULE", "from": "fds",
            "to": "mps", "version": 1, "created": "2026-10-23T10:00:00Z",
            "sha256": digest}), encoding="ascii")
        publish_mps_schedule(run, self.schedule_root)
        payload["schedule_sha256"] = digest
        status, _, body = self.request("/api/mps/passes", headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["passes"][0]["pass_id"], "yonsei-001")
        status, _, body = self.request("/api/mps/test-candidates", method="POST",
                                       body=payload, headers={**auth, "X-CSRF-Token": csrf})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["revision"], 1)
        status, _, body = self.request("/api/mps/test-candidates", headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["items"][0]["candidate_ids"], ["HK_GET"])
        status, _, _ = self.request("/api/mps/test-candidates", method="POST",
                                    body=payload, headers={**auth, "X-CSRF-Token": csrf})
        self.assertEqual(status, 409)

    def test_orbit_view_is_session_gated_and_read_only(self):
        status, _, _ = self.request("/api/fds/orbit")
        self.assertEqual(status, 401)
        status, _, body = self.request("/orbit")
        self.assertEqual(status, 200)  # unauthenticated request redirects to /login
        self.assertIn(b"login", body.lower())
        status, headers, _ = self.request("/api/login", method="POST", body={
            "username": "planner01", "password": "valid-password-123"})
        self.assertEqual(status, 200)
        auth = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        source = self.folder / "orbit_view.json"
        source.write_text('{"schema":"fds.orbit-view.v1"}', encoding="utf-8")
        self.portal.orbit_view = source
        try:
            status, headers, body = self.request("/api/fds/orbit", headers=auth)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["schema"], "fds.orbit-view.v1")
            self.assertEqual(headers["Cache-Control"], "no-store")
            status, _, body = self.request("/orbit", headers=auth)
            self.assertEqual(status, 200)
            self.assertIn(b"orbit-earth", body)
            source.unlink()
            status, _, _ = self.request("/api/fds/orbit", headers=auth)
            self.assertEqual(status, 503)
        finally:
            self.portal.orbit_view = None
            source.unlink(missing_ok=True)

    def test_ros_published_test_tm_is_session_gated_and_fail_closed(self):
        source = self.folder / "test_tm_snapshot.json"
        status, _, _ = self.request("/api/ros/test-tm-snapshot")
        self.assertEqual(status, 401)
        status, headers, _ = self.request("/api/login", method="POST", body={
            "username": "planner01", "password": "valid-password-123"})
        self.assertEqual(status, 200)
        auth = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        try:
            status, _, _ = self.request("/api/ros/test-tm-snapshot", headers=auth)
            self.assertEqual(status, 503)
            source.write_text(json.dumps(test_tm_snapshot()), encoding="utf-8")
            status, headers, body = self.request("/api/ros/test-tm-snapshot", headers=auth)
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)["non_operational"])
            self.assertEqual(headers["Cache-Control"], "no-store")
            invalid = test_tm_snapshot()
            invalid["non_operational"] = False
            source.write_text(json.dumps(invalid), encoding="utf-8")
            status, _, _ = self.request("/api/ros/test-tm-snapshot", headers=auth)
            self.assertEqual(status, 503)
        finally:
            source.unlink(missing_ok=True)

    def test_validated_ros_record_reaches_portal_and_corruption_fails_closed(self):
        status, headers, _ = self.request("/api/login", method="POST", body={
            "username": "planner01", "password": "valid-password-123"})
        self.assertEqual(status, 200)
        auth = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        source = self.folder / "ros-tm.record"
        header = (b"PSTSIM03" + bytes([1, 1, 0x7e, 0x57]) +
                  struct.pack(">H", 3) + struct.pack(">I", 77) + b"abc")
        frame = header + struct.pack(">I", zlib.crc32(header))
        record = bytes([1]) + struct.pack(">I", len(frame)) + frame
        try:
            source.write_bytes(record)
            source.chmod(0o600)
            status, _, body = self.request("/api/ros/overview", headers=auth)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["last_sequence"], 77)
            source.write_bytes(record[:-1] + bytes([record[-1] ^ 1]))
            status, _, _ = self.request("/api/ros/overview", headers=auth)
            self.assertEqual(status, 502)
        finally:
            source.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
