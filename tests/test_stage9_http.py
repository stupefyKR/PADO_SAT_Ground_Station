"""Windows-portable local HTTP portal/API contract; no Docker or TLS required."""

import hashlib
import base64
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from services.webapp.auth import AuthStore
from services.webapp.main import AppServer, BRAND_ASSET, initialize_internal_token


class FakeHasher:
    def hash(self, password):
        return "$argon2id$test$" + password[::-1]

    def verify(self, encoded, password):
        return encoded == self.hash(password)


SCHEDULE = {"schedule_state": "validated", "schedule_sha256": "a" * 64,
            "operational": False, "passes": [{
                "pass_id": "yonsei-001", "station_id": "yonsei", "band": "UHF",
                "aos_utc_s": 100, "los_utc_s": 200, "peak_utc_s": 150,
                "minimum_elevation_deg": 10, "max_elevation_deg": 42,
                "clipped_start": False, "clipped_end": False}]}


class LocalHttpWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.token = self.folder / "token"
        initialize_internal_token(self.token)
        original = self.token.read_text(encoding="ascii")
        initialize_internal_token(self.token)
        self.assertEqual(self.token.read_text(encoding="ascii"), original)
        self.auth = AuthStore(self.folder / "users.db", FakeHasher())
        self.auth.create_user("planner01", "planner", "valid-password-123")
        self.patches = [patch("services.webapp.main.mps_passes", return_value=SCHEDULE),
                        patch("services.webapp.main.mps_catalog", return_value={"version": 1})]
        for item in self.patches:
            item.start()
        self.backend = AppServer(("127.0.0.1", 0), mode="mps",
                                 transport="http", internal_token_file=self.token,
                                 candidate_db=self.folder / "drafts.db")
        self.portal = AppServer(("127.0.0.1", 0), mode="portal", transport="http",
                                internal_token_file=self.token, auth=self.auth,
                                mps_url=f"http://127.0.0.1:{self.backend.server_port}")
        self.threads = []
        for server in (self.backend, self.portal):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.threads.append(thread)

    def tearDown(self):
        for server in (self.portal, self.backend):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)
        self.backend.candidates.close()
        self.auth.close()
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    @staticmethod
    def request(url, *, data=None, headers=None):
        request = Request(url, data=json.dumps(data).encode("utf-8") if data is not None else None,
                          headers={**({"Content-Type": "application/json"} if data is not None else {}),
                                   **(headers or {})})
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, response.headers, json.loads(response.read())
        except HTTPError as error:
            return error.code, error.headers, json.loads(error.read())

    def test_http_login_proxy_sequence_and_backend_boundary(self):
        base = f"http://127.0.0.1:{self.portal.server_port}"
        backend = f"http://127.0.0.1:{self.backend.server_port}"
        try:
            status, _, _ = self.request(base + "/api/mps/passes")
        except URLError as error:
            if isinstance(error.reason, PermissionError):
                self.skipTest("local sandbox denies loopback connections")
            raise
        self.assertEqual(status, 401)
        status, _, _ = self.request(backend + "/passes",
                                    headers={"X-Pado-Actor": "planner01", "X-Pado-Role": "planner"})
        self.assertEqual(status, 403)
        status, headers, body = self.request(base + "/api/login", data={
            "username": "planner01", "password": "valid-password-123"})
        self.assertEqual(status, 200)
        csrf = body["csrf"]
        self.assertNotIn("Secure", headers["Set-Cookie"])
        self.assertNotIn("Strict-Transport-Security", headers)
        cookie = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        status, _, body = self.request(base + "/api/mps/passes", headers=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(body["passes"][0]["pass_id"], "yonsei-001")
        sequence = {"pass_id": "yonsei-001", "schedule_sha256": "a" * 64,
                    "expected_revision": 0, "steps": [{
                        "offset_s": 10, "command_name": "hk_get", "parameters": {}}]}
        status, _, _ = self.request(base + "/api/mps/test-sequences",
                                    data=sequence, headers=cookie)
        self.assertEqual(status, 403)
        status, _, saved = self.request(base + "/api/mps/test-sequences",
                                        data=sequence,
                                        headers={**cookie, "X-CSRF-Token": csrf})
        self.assertEqual(status, 200)
        self.assertEqual(saved["revision"], 1)
        status, _, stored = self.request(base + "/api/mps/test-sequences", headers=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(stored["items"][0]["steps"], sequence["steps"])

    def test_orbit_targets_are_separate_and_session_gated(self):
        base = f"http://127.0.0.1:{self.portal.server_port}"
        try:
            status, _, _ = self.request(base + "/api/fds/orbit?target=synthetic")
        except URLError as error:
            if isinstance(error.reason, PermissionError):
                self.skipTest("local sandbox denies loopback connections")
            raise
        self.assertEqual(status, 401)
        status, headers, _ = self.request(base + "/api/login", data={
            "username": "planner01", "password": "valid-password-123"})
        self.assertEqual(status, 200)
        cookie = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        views = {}
        for target, norad_id in (("synthetic", 99999), ("iss", 25544),
                                  ("operational", 12345)):
            view = self.folder / target / "orbit_view.json"
            view.parent.mkdir()
            view.write_text(json.dumps({"schema": "fds.orbit-view.v1",
                                        "norad_id": norad_id}), encoding="utf-8")
            views[target] = view
        self.portal.orbit_view = views["synthetic"]
        self.portal.synthetic_orbit_view = views["synthetic"]
        self.portal.iss_orbit_view = views["iss"]
        try:
            for target, norad_id in (("synthetic", 99999), ("iss", 25544)):
                status, response_headers, body = self.request(
                    base + "/api/fds/orbit?target=" + target, headers=cookie)
                self.assertEqual(status, 200)
                self.assertEqual(body["norad_id"], norad_id)
                self.assertEqual(response_headers["Cache-Control"], "no-store")
            status, _, body = self.request(base + "/api/fds/orbit", headers=cookie)
            self.assertEqual(status, 200)
            self.assertEqual(body["norad_id"], 99999)
            status, _, body = self.request(
                base + "/api/fds/orbit?target=operational", headers=cookie)
            self.assertEqual(status, 503)
            self.portal.operational_orbit_view = views["operational"]
            status, _, body = self.request(
                base + "/api/fds/orbit?target=operational", headers=cookie)
            self.assertEqual(status, 200)
            self.assertEqual(body["norad_id"], 12345)
            views["iss"].write_text(json.dumps({"schema": "fds.orbit-view.v1",
                                                "norad_id": 99999}), encoding="utf-8")
            status, _, _ = self.request(base + "/api/fds/orbit?target=iss", headers=cookie)
            self.assertEqual(status, 503)
            status, _, _ = self.request(
                base + "/api/fds/orbit?target=synthetic&target=iss", headers=cookie)
            self.assertEqual(status, 400)
        finally:
            self.portal.orbit_view = None
            self.portal.synthetic_orbit_view = None
            self.portal.iss_orbit_view = None
            self.portal.operational_orbit_view = None

    def test_cad_mesh_asset_requires_login_and_is_bounded(self):
        base = f"http://127.0.0.1:{self.portal.server_port}"
        mesh_path = self.folder / "assets" / "pado-sat-deployed-mesh.json"
        mesh_path.parent.mkdir()
        mesh_path.write_text(json.dumps({"schema": "pado.cad-mesh.v1",
                                         "vertices": [], "triangles": []}), encoding="utf-8")
        self.portal.static_root = self.folder
        try:
            status, _, _ = self.request(base + "/assets/pado-sat-deployed-mesh.json")
        except URLError as error:
            if isinstance(error.reason, PermissionError):
                self.skipTest("local sandbox denies loopback connections")
            raise
        self.assertEqual(status, 401)
        status, headers, _ = self.request(base + "/api/login", data={
            "username": "planner01", "password": "valid-password-123"})
        self.assertEqual(status, 200)
        cookie = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        status, _, body = self.request(base + "/assets/pado-sat-deployed-mesh.json",
                                       headers=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(body["schema"], "pado.cad-mesh.v1")
        mesh_path.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
        status, _, _ = self.request(base + "/assets/pado-sat-deployed-mesh.json",
                                    headers=cookie)
        self.assertEqual(status, 503)

    def test_cad_appearance_and_surface_images_are_private_and_bounded(self):
        base = f"http://127.0.0.1:{self.portal.server_port}"
        assets = self.folder / "assets"
        assets.mkdir()
        manifest = assets / "pado-sat-appearance.json"
        manifest.write_text(json.dumps({"schema": "pado.cad-appearance.v1"}),
                            encoding="utf-8")
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aQF8AAAAASUVORK5CYII=")
        images = [assets / f"pado-sat-surface-{face}.png"
                  for face in ("front", "rear", "top", "end")]
        for image in images:
            image.write_bytes(png)
        self.portal.static_root = self.folder
        private_paths = ["/assets/pado-sat-appearance.json"] + [
            "/assets/" + image.name for image in images]
        try:
            for path in private_paths:
                status, _, _ = self.request(base + path)
                self.assertEqual(status, 401, path)
        except URLError as error:
            if isinstance(error.reason, PermissionError):
                self.skipTest("local sandbox denies loopback connections")
            raise
        status, headers, _ = self.request(base + "/api/login", data={
            "username": "planner01", "password": "valid-password-123"})
        self.assertEqual(status, 200)
        cookie = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        status, headers, body = self.request(base + private_paths[0], headers=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(body["schema"], "pado.cad-appearance.v1")
        for path in private_paths[1:]:
            with urlopen(Request(base + path, headers=cookie), timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Content-Type"], "image/png")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(response.read(), png)
        for path in ("/assets/pado-sat-surface-unlisted.png",
                     "/assets/%2e%2e/pado-sat-surface-front.png"):
            status, _, _ = self.request(base + path, headers=cookie)
            self.assertEqual(status, 404)
        images[0].write_bytes(b"x" * (4 * 1024 * 1024 + 1))
        status, _, _ = self.request(base + private_paths[1], headers=cookie)
        self.assertEqual(status, 503)
        images[1].unlink()
        status, _, _ = self.request(base + private_paths[2], headers=cookie)
        self.assertEqual(status, 503)

    def test_public_badge_is_exact_and_only_badge_is_browser_cached(self):
        base = f"http://127.0.0.1:{self.portal.server_port}"
        try:
            with urlopen(base + BRAND_ASSET, timeout=3) as response:
                badge = response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Content-Type"], "image/png")
                self.assertEqual(response.headers["Cache-Control"],
                                 "private, max-age=604800, immutable")
        except URLError as error:
            if isinstance(error.reason, PermissionError):
                self.skipTest("local sandbox denies loopback connections")
            raise
        self.assertEqual(hashlib.sha256(badge).hexdigest(),
                         "ec9020cbb16ce13101b8c7912764ca9c86f25fcafdfa72fe172385f898428150")
        with urlopen(base + "/login", timeout=3) as response:
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        for page in ("login", "ops", "mps", "orbit"):
            html = (Path(__file__).resolve().parents[1] / "web" / page /
                    "index.html").read_text(encoding="utf-8")
            self.assertIn(f'rel="icon" type="image/png" href="{BRAND_ASSET}"', html)
            self.assertIn(f'src="{BRAND_ASSET}"', html)
