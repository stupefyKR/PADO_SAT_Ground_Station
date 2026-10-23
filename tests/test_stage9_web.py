import json
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from services.webapp.auth import AuthStore
from services.webapp.main import AppServer, COMMAND_DB


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


@unittest.skipUnless(shutil.which("openssl"), "openssl command unavailable")
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
        cls.servers = []
        cls.threads = []
        for mode, cert, key in (("ros", cls.ros_cert, cls.ros_key),
                                ("mps", cls.mps_cert, cls.mps_key)):
            server = AppServer(("127.0.0.1", 0), mode=mode, cert=cert,
                               key=key, ca=cls.ca_cert, command_db=COMMAND_DB)
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
        for thread in cls.threads:
            thread.join(timeout=3)
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
        status, _, body = self.request("/api/mps/catalog", headers=auth_header)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["commands"], [])
        status, _, body = self.request("/api/mps/passes", headers=auth_header)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["passes"], [])
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


if __name__ == "__main__":
    unittest.main()
