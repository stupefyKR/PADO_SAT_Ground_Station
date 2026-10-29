"""Development portal and isolated subsystem APIs over local HTTP or TLS.

This is a non-operational development boundary. No flight TC, approval or upload
endpoint exists. Run each mode as a separate process/container.
"""

from __future__ import annotations

import argparse
import getpass
import http.client
import json
import os
import re
import secrets
import ssl
import stat
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from .auth import AuthError, AuthStore, ROLES
from .core import CoreUnavailable, mps_catalog, mps_passes, ros_summary
from .mps_workbench import CandidateError, MpsCandidateStore, draft_commands
from .ros_workbench import DraftError, RosDraftStore, normalize_sim_handoff
from .sim_tc_dispatch import SimTcDispatcher
from .tm_preview import MAX_SNAPSHOT_BYTES, PreviewError, load_beacon, load_snapshot
from .state_history import StateHistoryError, history_query, state_history
from services.sim_ground.archive import ArchiveError, list_events, list_packets, packet_detail
from tools.orbit_history import HISTORY_NAME, OrbitHistoryError, read_history as read_orbit_history


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
CORE_SOCKET = Path("/run/core/core.sock")
MAX_BODY = 4096
MAX_DRAFT_BODY = 40000
MAX_UPSTREAM = 65536
MAX_ORBIT_VIEW = 4 * 1024 * 1024
MAX_CAD_MESH = 4 * 1024 * 1024
MAX_TM_ARCHIVE_RESPONSE = 256 * 1024
ORBIT_TARGETS = {"synthetic", "iss", "operational"}
BRAND_ASSET = "/assets/pado-sat-badge-ec9020cb.png"
# These spacecraft geometry/appearance files share the authenticated CAD
# boundary. Image names are explicit so arbitrary uploaded files are never
# reachable through the static endpoint.
CAD_ASSETS = {
    "/assets/pado-sat-deployed-mesh.json": MAX_CAD_MESH,
    "/assets/pado-sat-appearance.json": 128 * 1024,
    "/assets/pado-sat-surface-front.png": 4 * 1024 * 1024,
    "/assets/pado-sat-surface-rear.png": 4 * 1024 * 1024,
    "/assets/pado-sat-surface-top.png": 4 * 1024 * 1024,
    "/assets/pado-sat-surface-end.png": 4 * 1024 * 1024,
}
ASSETS = {
    "/login": ("login/index.html", "text/html; charset=utf-8"),
    "/ops": ("ops/index.html", "text/html; charset=utf-8"),
    "/mps": ("mps/index.html", "text/html; charset=utf-8"),
    "/orbit": ("orbit/index.html", "text/html; charset=utf-8"),
    "/assets/style.css": ("common/style.css", "text/css; charset=utf-8"),
    BRAND_ASSET: ("assets/pado-sat-badge-ec9020cb.png", "image/png"),
    "/assets/app.js": ("common/app.js", "text/javascript; charset=utf-8"),
    "/assets/ros-workbench.js": ("ops/workbench.js", "text/javascript; charset=utf-8"),
    "/assets/ros-packet-browser.js": ("ops/packet_browser.js", "text/javascript; charset=utf-8"),
    "/assets/ros-attitude-math.js": ("ops/attitude_math.js", "text/javascript; charset=utf-8"),
    "/assets/ros-state-history.js": ("ops/state_history.js", "text/javascript; charset=utf-8"),
    "/assets/ros-attitude-gl.js": ("ops/attitude_gl.js", "text/javascript; charset=utf-8"),
    "/assets/ros-attitude-viewer.js": ("ops/attitude_viewer.js", "text/javascript; charset=utf-8"),
    "/assets/ros-workbench.css": ("ops/workbench.css", "text/css; charset=utf-8"),
    "/assets/pado-sat-deployed-mesh.json": ("assets/pado-sat-deployed-mesh.json", "application/json"),
    "/assets/pado-sat-appearance.json": ("assets/pado-sat-appearance.json", "application/json"),
    "/assets/pado-sat-surface-front.png": ("assets/pado-sat-surface-front.png", "image/png"),
    "/assets/pado-sat-surface-rear.png": ("assets/pado-sat-surface-rear.png", "image/png"),
    "/assets/pado-sat-surface-top.png": ("assets/pado-sat-surface-top.png", "image/png"),
    "/assets/pado-sat-surface-end.png": ("assets/pado-sat-surface-end.png", "image/png"),
    "/assets/mps-workflow.css": ("mps/workflow.css", "text/css; charset=utf-8"),
    "/assets/mps-live.js": ("mps/live.js", "text/javascript; charset=utf-8"),
    "/assets/orbit.js": ("orbit/orbit.js", "text/javascript; charset=utf-8"),
    "/assets/orbit.css": ("orbit/orbit.css", "text/css; charset=utf-8"),
    "/assets/earth-blue-marble-october.jpg": ("assets/earth-blue-marble-october.jpg", "image/jpeg"),
}
PROXY_ROUTES = {
    "/api/ros/overview": ("ros", "/overview"),
    "/api/ros/test-tm-snapshot": ("ros", "/test-tm-snapshot"),
    "/api/ros/test-beacon": ("ros", "/test-beacon"),
    "/api/ros/test-sequences": ("ros", "/test-sequences"),
    "/api/ros/test-reservations": ("ros", "/test-reservations"),
    "/api/ros/test-sim-sequences": ("ros", "/test-sim-sequences"),
    "/api/ros/test-sim-reservations": ("ros", "/test-sim-reservations"),
    "/api/ros/test-sim-tm-requests": ("ros", "/test-sim-tm-requests"),
    "/api/mps/catalog": ("mps", "/catalog"),
    "/api/mps/passes": ("mps", "/passes"),
    "/api/mps/test-candidates": ("mps", "/test-candidates"),
    "/api/mps/test-sequences": ("mps", "/test-sequences"),
    "/api/mps/test-ros-feedback": ("ros", "/test-n10-feedback"),
}


def archive_query(query: str, *, events: bool = False) -> dict:
    """Strict small query contract, shared by the portal and ROS boundary."""
    if not query:
        return {}
    if len(query) > 512:
        raise ValueError("invalid_query")
    pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True,
                      max_num_fields=3)
    values = dict(pairs)
    allowed = {"limit"} if events else {"limit", "before_id", "name"}
    if len(values) != len(pairs) or not set(values) <= allowed:
        raise ValueError("invalid_query")
    result = {}
    for key in ("limit", "before_id"):
        if key in values:
            value = values[key]
            maximum = 100 if key == "limit" else 0x7fffffffffffffff
            if not re.fullmatch(r"[1-9][0-9]{0,18}", value) or not 1 <= int(value) <= maximum:
                raise ValueError("invalid_query")
            result[key] = int(value)
    if "name" in values:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", values["name"]):
            raise ValueError("invalid_query")
        result["name"] = values["name"]
    return result


def archive_route(path: str, *, portal: bool = False) -> str | None:
    prefix = "/api/ros" if portal else ""
    if path == prefix + "/received-tm":
        return "packets"
    if path == prefix + "/tm-events":
        return "events"
    match = re.fullmatch(re.escape(prefix) + r"/received-tm/([1-9][0-9]{0,18})", path)
    if match and int(match[1]) <= 0x7fffffffffffffff:
        return "detail"
    return None


def test_n10_feedback(drafts: RosDraftStore, tm_preview: Path | None,
                      now_s: int | None = None) -> dict:
    """Bounded ACK/SOH projection; it is not an operational N10 exchange."""
    now_s = int(time.time()) if now_s is None else now_s
    result = {"schema": "pado.sim-n10-feedback.v1", "source": "ROS_SIM_ONLY",
              "non_operational": True, "ack_items": drafts.n10_test_ack_items(),
              "bus_soh_state": "UNAVAILABLE", "bus_soh": None}
    try:
        snapshot = load_snapshot(tm_preview)
    except PreviewError as exc:
        if str(exc) not in ("snapshot_unavailable", "snapshot_not_configured"):
            result["bus_soh_state"] = "INVALID"
        return result
    now = datetime.fromtimestamp(now_s, timezone.utc)
    received = datetime.fromisoformat(
        snapshot["received_utc"].replace("Z", "+00:00"))
    age = (now - received).total_seconds()
    if age < -30:
        result["bus_soh_state"] = "INVALID"
        return result
    sensors = []
    stale = age >= 300
    for sensor in snapshot["sensors"]:
        # Only the explicitly configured EPS test sensors are bus SOH here.
        if sensor["group"] != "EPS":
            continue
        sample = sensor["samples"][-1]
        sample_time = datetime.fromisoformat(
            sample["utc"].replace("Z", "+00:00"))
        if (now - sample_time).total_seconds() >= 300:
            stale = True
        sensors.append({"id": sensor["id"], "name": sensor["name"],
                        "unit": sensor["unit"], "sample_utc": sample["utc"],
                        "value": sample["value"], "lower": sensor["lower"],
                        "upper": sensor["upper"]})
    if not sensors:
        return result
    state = "STALE" if stale else "CURRENT"
    for sensor in sensors:
        sensor["limit_state"] = ("INDETERMINATE" if stale else
                                 "IN_RANGE" if sensor["lower"] <= sensor["value"] <=
                                 sensor["upper"] else "OUT_OF_RANGE")
    result["bus_soh_state"] = state
    result["bus_soh"] = {"kind": "BUS_SOH", "source": "TEST_ONLY_TM_SNAPSHOT",
                         "received_utc": snapshot["received_utc"],
                         "state": state, "sensors": sensors}
    return result


def initialize_internal_token(path: Path) -> None:
    """Create one private development-only portal-to-backend token, idempotently."""
    if path.is_symlink():
        raise ValueError("internal_token_symlink")
    if path.exists():
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode) or (os.name == "posix" and mode & 0o077):
            raise ValueError("internal_token_not_private")
        token = path.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", token):
            raise ValueError("invalid_internal_token")
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as target:
            target.write(secrets.token_hex(32) + "\n")
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address: tuple[str, int], *, mode: str,
                 cert: Path | None = None, key: Path | None = None,
                 ca: Path | None = None, auth: AuthStore | None = None,
                 ros_url: str = "", mps_url: str = "", client_cert: Path | None = None,
                 client_key: Path | None = None, static_root: Path = WEB,
                 core_socket: Path = CORE_SOCKET,
                 orbit_view: Path | None = None,
                 synthetic_orbit_view: Path | None = None,
                 iss_orbit_view: Path | None = None,
                 operational_orbit_view: Path | None = None,
                 tm_preview: Path | None = None,
                 draft_db: Path | None = None,
                 candidate_db: Path | None = None,
                 sim_tc_socket: Path | None = None,
                 transport: str = "tls", internal_token_file: Path | None = None) -> None:
        if transport not in ("tls", "http"):
            raise ValueError("invalid_transport")
        if transport == "tls" and (cert is None or key is None or ca is None or
                                    (mode == "portal" and
                                     (client_cert is None or client_key is None))):
            raise ValueError("tls_certificates_required")
        if transport == "http":
            if internal_token_file is None:
                raise ValueError("internal_token_file_required")
            token = internal_token_file.read_text(encoding="ascii").strip()
            if not re.fullmatch(r"[0-9a-f]{64}", token):
                raise ValueError("invalid_internal_token")
        else:
            token = ""
        self.mode = mode
        self.transport = transport
        self.internal_token = token
        self.auth = auth
        self.ros_url = ros_url
        self.mps_url = mps_url
        self.client_cert = client_cert
        self.client_key = client_key
        self.ca = ca
        self.core_socket = core_socket
        self.static_root = static_root
        self.orbit_view = orbit_view
        self.synthetic_orbit_view = synthetic_orbit_view
        self.iss_orbit_view = iss_orbit_view
        self.operational_orbit_view = operational_orbit_view
        self.tm_preview = tm_preview
        self.drafts = RosDraftStore(draft_db) if mode == "ros" and draft_db else None
        self.candidates = MpsCandidateStore(candidate_db) if mode == "mps" and candidate_db else None
        self.sim_tc_socket = sim_tc_socket if mode == "ros" else None
        self.login_attempts: dict[str, list[float]] = {}
        self.login_lock = threading.Lock()
        super().__init__(address, Handler)
        if transport == "tls":
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(cert), str(key))
            if mode != "portal":
                context.verify_mode = ssl.CERT_REQUIRED
                context.load_verify_locations(cafile=str(ca))
            self.socket = context.wrap_socket(self.socket, server_side=True)


class Handler(BaseHTTPRequestHandler):
    server: AppServer
    server_version = "PADO-Web/0"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        # Avoid request paths, cookies, credentials and raw TM in stdout.
        pass

    def setup(self) -> None:
        self.request.settimeout(5)
        super().setup()

    def _send(self, status: int, body: bytes = b"", content_type: str =
              "application/json; charset=utf-8", extra: dict[str, str] | None = None,
              *, cache_control: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if self.server.transport == "tls":
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; object-src 'none'; frame-ancestors 'none'; "
                         "form-action 'self'; base-uri 'none'")
        if extra:
            for name, value in extra.items():
                self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, value: object, extra: dict[str, str] | None = None) -> None:
        self._send(status, json.dumps(value, separators=(",", ":"),
                                      ensure_ascii=False).encode("utf-8"), extra=extra)

    def _error(self, status: int, code: str) -> None:
        self._json(status, {"error": code})

    def _path(self) -> str:
        try:
            parsed = urlsplit(self.path)
        except ValueError:
            return ""
        if parsed.fragment or not parsed.path.startswith("/"):
            return ""
        if parsed.query:
            kind = archive_route(parsed.path, portal=self.server.mode == "portal")
            if parsed.path in ("/api/ros/state-history", "/state-history", "/api/fds/orbit-history"):
                try:
                    history_query(parsed.query,orbit=parsed.path=="/api/fds/orbit-history")
                except ValueError:
                    return ""
            elif kind in ("packets", "events"):
                try:
                    archive_query(parsed.query, events=kind == "events")
                except ValueError:
                    return ""
            elif not (parsed.path == "/api/fds/orbit" and
                      parsed.query in {f"target={target}" for target in ORBIT_TARGETS}):
                return ""
        return parsed.path

    def _cookie_token(self) -> str:
        try:
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            return cookie["pado_session"].value if "pado_session" in cookie else ""
        except Exception:
            return ""

    def _session(self):
        return self.server.auth.session(self._cookie_token()) if self.server.auth else None

    def _backend_identity(self) -> bool:
        if self.server.transport == "http":
            provided = self.headers.get("X-Pado-Internal-Token", "")
            return len(provided) == 64 and secrets.compare_digest(
                provided, self.server.internal_token)
        cert = self.connection.getpeercert()
        if not cert:
            return False
        return any(key == "commonName" and value == "pado-portal"
                   for group in cert.get("subject", ()) for key, value in group)

    def do_GET(self) -> None:
        path = self._path()
        if not path:
            self._error(HTTPStatus.BAD_REQUEST, "bad_path")
            return
        if self.server.mode != "portal":
            self._backend_get(path)
        else:
            self._portal_get(path)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        path = self._path()
        if self.server.mode == "portal":
            if path == "/api/login":
                self._login()
            elif path == "/api/logout":
                self._logout()
            elif path == "/api/mps/test-sequences/handoff":
                self._portal_sim_handoff()
            elif path == "/api/ros/test-sim-reservations":
                self._portal_sim_reserve()
            elif path in ("/api/ros/test-sequences", "/api/ros/test-reservations",
                          "/api/ros/test-reservations/cancel", "/api/mps/test-candidates",
                          "/api/mps/test-sequences",
                          "/api/ros/test-sim-reservations/cancel",
                          "/api/ros/test-sim-tm-requests"):
                self._portal_draft_post(path)
            else:
                self._error(HTTPStatus.NOT_FOUND, "not_found")
        elif self.server.mode == "ros":
            self._backend_draft_post(path)
        elif self.server.mode == "mps" and path in ("/test-candidates", "/test-sequences",
                                                    "/test-sequences/handoff"):
            self._backend_candidate_post(path)
        else:
            self._error(HTTPStatus.NOT_FOUND, "not_found")

    def _read_json(self, maximum: int = MAX_BODY) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", ""))
            if length < 0 or length > maximum or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return None
            value = json.loads(self.rfile.read(length))
            return value if isinstance(value, dict) else None
        except (ValueError, UnicodeDecodeError):
            return None

    def _portal_draft_post(self, path: str) -> None:
        session = self._session()
        if not session or not secrets.compare_digest(
                session.csrf, self.headers.get("X-CSRF-Token", "")):
            self._error(HTTPStatus.FORBIDDEN, "csrf_or_session")
            return
        if session.role not in ("operator", "lead", "planner"):
            self._error(HTTPStatus.FORBIDDEN, "role_denied")
            return
        body = self._read_json(MAX_DRAFT_BODY)
        if body is None:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_request")
            return
        route = ("mps", path.removeprefix("/api/mps")) if path.startswith("/api/mps/") else \
                ("ros", path.removeprefix("/api/ros"))
        self._proxy(route, session.username, session.role, "POST", body)

    def _portal_sim_handoff(self) -> None:
        """Get a fresh MPS draft on the server side before forwarding to ROS.

        The browser supplies only a pass reference, never command bytes or
        handoff contents. Both backend requests retain the authenticated actor.
        """
        session = self._session()
        if not session or not secrets.compare_digest(
                session.csrf, self.headers.get("X-CSRF-Token", "")):
            self._error(HTTPStatus.FORBIDDEN, "csrf_or_session")
            return
        if session.role not in ("operator", "lead", "planner"):
            self._error(HTTPStatus.FORBIDDEN, "role_denied")
            return
        body = self._read_json()
        if body is None or set(body) != {"pass_id", "schedule_sha256", "expected_revision"}:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_request")
            return
        response = self._proxy(("mps", "/test-sequences/handoff"), session.username,
                               session.role, "POST", body, deliver=False)
        if response is None:
            return
        status, data = response
        if status != HTTPStatus.OK:
            self._send(status, data)
            return
        try:
            envelope = json.loads(data)
            if not isinstance(envelope, dict) or len(data) > MAX_DRAFT_BODY:
                raise ValueError("invalid MPS handoff")
        except (UnicodeDecodeError, ValueError):
            self._error(HTTPStatus.BAD_GATEWAY, "invalid_mps_handoff")
            return
        self._proxy(("ros", "/test-sim-handoffs"), session.username,
                    session.role, "POST", envelope)

    def _portal_sim_reserve(self) -> None:
        """Recheck the saved MPS revision against today's FDS schedule."""
        session = self._session()
        if not session or not secrets.compare_digest(
                session.csrf, self.headers.get("X-CSRF-Token", "")):
            self._error(HTTPStatus.FORBIDDEN, "csrf_or_session")
            return
        if session.role not in ("operator", "lead", "planner"):
            self._error(HTTPStatus.FORBIDDEN, "role_denied")
            return
        body = self._read_json()
        if body is None or set(body) not in ({"sha256"}, {"sha256", "step_id"}) or \
                not isinstance(body["sha256"], str) or \
                re.fullmatch(r"[0-9a-f]{64}", body["sha256"]) is None:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_request")
            return
        listing = self._proxy(("ros", "/test-sim-sequences"), session.username,
                              session.role, deliver=False)
        if listing is None:
            return
        try:
            rows = json.loads(listing[1])["items"]
            sequence = next((row for row in rows if row["sha256"] == body["sha256"]), None)
            if sequence is None:
                self._error(HTTPStatus.CONFLICT, "sim_sequence_not_current")
                return
            reference = {"pass_id": sequence["pass_id"],
                         "schedule_sha256": sequence["schedule_sha256"],
                         "expected_revision": sequence["revision"]}
        except (KeyError, TypeError, ValueError):
            self._error(HTTPStatus.BAD_GATEWAY, "invalid_ros_handoff")
            return
        current = self._proxy(("mps", "/test-sequences/handoff"), session.username,
                              session.role, "POST", reference, deliver=False)
        if current is None:
            return
        if current[0] != HTTPStatus.OK:
            self._send(current[0], current[1])
            return
        try:
            refreshed = normalize_sim_handoff(json.loads(current[1]))
        except (DraftError, TypeError, ValueError):
            self._error(HTTPStatus.BAD_GATEWAY, "invalid_mps_handoff")
            return
        if refreshed["sha256"] != body["sha256"]:
            self._error(HTTPStatus.CONFLICT, "sim_sequence_stale")
            return
        self._proxy(("ros", "/test-sim-reservations"), session.username,
                    session.role, "POST", body)

    def _login(self) -> None:
        address = self.client_address[0]
        now = time.monotonic()
        with self.server.login_lock:
            if len(self.server.login_attempts) > 1024:
                self.server.login_attempts = {
                    ip: times for ip, times in self.server.login_attempts.items()
                    if any(now - at < 60 for at in times)
                }
            attempts = [at for at in self.server.login_attempts.get(address, [])
                        if now - at < 60]
            limited = len(attempts) >= 10
            if not limited:
                attempts.append(now)
                self.server.login_attempts[address] = attempts
        if limited:
            self._error(HTTPStatus.TOO_MANY_REQUESTS, "rate_limited")
            return
        body = self._read_json()
        if body is None or any(not isinstance(body.get(key, ""), str)
                               for key in ("username", "password", "totp")):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_request")
            return
        username = body.get("username", "")
        provided = body.get("password", "")
        code = body.get("totp", "")
        if max(len(username), len(provided), len(code)) > 1024:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_request")
            return
        try:
            session = self.server.auth.login(username, provided, code)
        except AuthError:
            self._error(HTTPStatus.UNAUTHORIZED, "invalid_credentials")
            return
        self._json(HTTPStatus.OK,
                   {"username": session.username, "role": session.role,
                    "csrf": session.csrf, "operational": False},
                   {"Set-Cookie": "pado_session=" + session.token +
                   "; Path=/; HttpOnly; " +
                   ("Secure; " if self.server.transport == "tls" else "") +
                   "SameSite=Strict"})

    def _logout(self) -> None:
        session = self._session()
        token = self._cookie_token()
        csrf = self.headers.get("X-CSRF-Token", "")
        if not session or not secrets.compare_digest(session.csrf, csrf):
            self._error(HTTPStatus.FORBIDDEN, "csrf_or_session")
            return
        self.server.auth.logout(token)
        self._json(HTTPStatus.OK, {"logged_out": True},
                   {"Set-Cookie": "pado_session=; Path=/; Max-Age=0; HttpOnly; " +
                    ("Secure; " if self.server.transport == "tls" else "") +
                    "SameSite=Strict"})

    def _portal_get(self, path: str) -> None:
        if path == "/":
            self._send(HTTPStatus.FOUND, extra={"Location": "/ops"})
            return
        if path == "/api/session":
            session = self._session()
            if not session:
                self._error(HTTPStatus.UNAUTHORIZED, "login_required")
            else:
                self._json(HTTPStatus.OK,
                           {"username": session.username, "role": session.role,
                            "csrf": session.csrf, "operational": False})
            return
        if path == "/api/ros/state-history":
            session = self._session()
            if not session:
                self._error(HTTPStatus.UNAUTHORIZED,"login_required")
                return
            query = history_query(urlsplit(self.path).query)
            self._proxy(("ros","/state-history?"+urlencode(query)),session.username,session.role)
            return
        if path == "/api/fds/orbit-history":
            if not self._session():
                self._error(HTTPStatus.UNAUTHORIZED,"login_required")
                return
            try:
                query = history_query(urlsplit(self.path).query,orbit=True)
                target = query.pop("target")
                source = {"synthetic":self.server.synthetic_orbit_view or self.server.orbit_view,
                    "iss":self.server.iss_orbit_view,"operational":self.server.operational_orbit_view}[target]
                if source is not None and (source.name!="orbit_view.json" or source.is_symlink()):
                    raise OrbitHistoryError("orbit_history_unavailable")
                payload = read_orbit_history(source.with_name(HISTORY_NAME) if source else None,
                    norad_id=99999 if target=="synthetic" else 25544 if target=="iss" else None,**query)
                if target=="operational" and payload["norad_id"] in (99999,25544):
                    raise OrbitHistoryError("orbit_history_unavailable")
                self._json(HTTPStatus.OK,payload)
            except (StateHistoryError,OrbitHistoryError,OSError,ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST if str(error)=="invalid_query" else
                            HTTPStatus.SERVICE_UNAVAILABLE,str(error))
            return
        if path == "/api/fds/orbit":
            if not self._session():
                self._error(HTTPStatus.UNAUTHORIZED, "login_required")
                return
            query = urlsplit(self.path).query
            target = query.removeprefix("target=") if query else None
            source = (self.server.orbit_view if target is None else {
                "synthetic": self.server.synthetic_orbit_view,
                "iss": self.server.iss_orbit_view,
                "operational": self.server.operational_orbit_view,
            }[target])
            try:
                if source is None or source.name != "orbit_view.json" or source.is_symlink() or not source.is_file():
                    raise OSError("orbit view absent")
                if source.stat().st_size > MAX_ORBIT_VIEW:
                    raise OSError("orbit view too large")
                data = source.read_bytes()
                if len(data) > MAX_ORBIT_VIEW:
                    raise OSError("orbit view too large")
                if target is not None:
                    payload = json.loads(data)
                    if not isinstance(payload, dict):
                        raise ValueError("orbit target payload must be an object")
                    norad_id = payload.get("norad_id")
                    if (payload.get("schema") != "fds.orbit-view.v1" or
                            type(norad_id) is not int or not 1 <= norad_id <= 99999 or
                            (target == "synthetic" and norad_id != 99999) or
                            (target == "iss" and norad_id != 25544) or
                            (target == "operational" and norad_id in (25544, 99999))):
                        raise ValueError("orbit target differs from published data")
                self._send(HTTPStatus.OK, data)
            except (OSError, ValueError, TypeError):
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "fds_orbit_unavailable")
            return
        if archive_route(path, portal=True):
            session = self._session()
            if not session:
                self._error(HTTPStatus.UNAUTHORIZED, "login_required")
                return
            kind = archive_route(path, portal=True)
            query = archive_query(urlsplit(self.path).query, events=kind == "events")
            backend_path = path.removeprefix("/api/ros")
            if query:
                backend_path += "?" + urlencode(query)
            self._proxy(("ros", backend_path), session.username, session.role)
            return
        if path in PROXY_ROUTES:
            session = self._session()
            if not session:
                self._error(HTTPStatus.UNAUTHORIZED, "login_required")
                return
            self._proxy(PROXY_ROUTES[path], session.username, session.role)
            return
        if path in ASSETS:
            if (path in ("/ops", "/mps", "/orbit") or path in CAD_ASSETS) and not self._session():
                if path in CAD_ASSETS:
                    self._error(HTTPStatus.UNAUTHORIZED, "login_required")
                else:
                    self._send(HTTPStatus.FOUND, extra={"Location": "/login"})
                return
            relative, content_type = ASSETS[path]
            source = self.server.static_root / relative
            try:
                limit = CAD_ASSETS.get(path)
                if limit is not None and (source.is_symlink() or not source.is_file() or
                                          not 1 <= source.stat().st_size <= limit):
                    raise OSError("CAD asset is absent or oversized")
                data = source.read_bytes()
                if limit is not None and not 1 <= len(data) <= limit:
                    raise OSError("CAD asset is oversized")
            except OSError:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "asset_unavailable")
                return
            # The filename is content-fingerprinted; only this public badge is
            # browser-cached. Session pages, APIs and private CAD stay no-store.
            cache_control = ("private, max-age=604800, immutable"
                             if path == BRAND_ASSET else "no-store")
            self._send(HTTPStatus.OK, data, content_type, cache_control=cache_control)
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found")

    def _proxy(self, route: tuple[str, str], username: str, role: str,
               method: str = "GET", body: dict | None = None, *,
               deliver: bool = True) -> tuple[int, bytes] | None:
        service, path = route
        base = self.server.ros_url if service == "ros" else self.server.mps_url
        address = urlsplit(base)
        expected_scheme = "https" if self.server.transport == "tls" else "http"
        if address.scheme != expected_scheme or not address.hostname or not address.port:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "backend_configuration")
            return
        try:
            if self.server.transport == "tls":
                context = ssl.create_default_context(cafile=str(self.server.ca))
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                context.load_cert_chain(str(self.server.client_cert),
                                        str(self.server.client_key))
                connection = http.client.HTTPSConnection(address.hostname, address.port,
                                                         context=context, timeout=3)
            else:
                connection = http.client.HTTPConnection(address.hostname, address.port,
                                                        timeout=3)
            headers = {"X-Pado-Actor": username, "X-Pado-Role": role}
            if self.server.transport == "http":
                headers["X-Pado-Internal-Token"] = self.server.internal_token
            payload = None
            if body is not None:
                headers["Content-Type"] = "application/json"
                payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            base_path = urlsplit(path).path
            maximum = (MAX_TM_ARCHIVE_RESPONSE if archive_route(base_path) or base_path=="/state-history" else
                       MAX_SNAPSHOT_BYTES if path == "/test-tm-snapshot" else
                       2 * 1024 * 1024 if path == "/test-sim-sequences" else
                       1024 * 1024 if path == "/test-sequences" and service == "mps"
                       else MAX_UPSTREAM)
            data = response.read(maximum + 1)
            captured = None
            if len(data) > maximum or (method == "GET" and not archive_route(base_path) and base_path!="/state-history" and path not in
                                            ("/test-sequences", "/test-reservations",
                                             "/test-candidates",
                                             "/test-tm-snapshot", "/test-beacon") and
                                            response.status != HTTPStatus.OK):
                self._error(HTTPStatus.BAD_GATEWAY, "backend_failure")
            elif deliver:
                self._send(response.status, data)
            else:
                captured = (response.status, data)
            connection.close()
            return captured
        except (OSError, ssl.SSLError, http.client.HTTPException):
            self._error(HTTPStatus.BAD_GATEWAY, "backend_unavailable")

    def _backend_get(self, path: str) -> None:
        if not self._backend_identity():
            self._error(HTTPStatus.FORBIDDEN, "client_identity")
            return
        actor = self.headers.get("X-Pado-Actor", "")
        role = self.headers.get("X-Pado-Role", "")
        if role not in ROLES or len(actor) > 64 or not actor:
            self._error(HTTPStatus.FORBIDDEN, "portal_identity")
            return
        if self.server.mode=="ros" and path=="/state-history":
            try:
                query = history_query(urlsplit(self.path).query)
                archive = self.server.tm_preview.with_name("received_tm.sqlite3") if self.server.tm_preview else None
                self._json(HTTPStatus.OK,state_history(archive,**query))
            except StateHistoryError as error:
                self._error(HTTPStatus.BAD_REQUEST if str(error)=="invalid_query" else
                            HTTPStatus.SERVICE_UNAVAILABLE,str(error))
            return
        kind = archive_route(path) if self.server.mode == "ros" else None
        if kind:
            try:
                if self.server.tm_preview is None:
                    raise ArchiveError("archive_unavailable")
                archive = self.server.tm_preview.with_name("received_tm.sqlite3")
                query = archive_query(urlsplit(self.path).query, events=kind == "events")
                if kind == "packets":
                    payload = list_packets(archive, **query)
                elif kind == "detail":
                    payload = packet_detail(archive, int(path.rsplit("/", 1)[1]))
                else:
                    payload = list_events(archive, limit=query.get("limit", 30))
                if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_TM_ARCHIVE_RESPONSE:
                    raise ArchiveError("archive_invalid")
            except (ArchiveError, ValueError) as error:
                code = str(error)
                status = (HTTPStatus.NOT_FOUND if code == "packet_not_found" else
                          HTTPStatus.BAD_REQUEST if code == "invalid_query" else
                          HTTPStatus.SERVICE_UNAVAILABLE)
                self._error(status, code if code in {"packet_not_found", "invalid_query"} else
                            "tm_archive_unavailable")
                return
            self._json(HTTPStatus.OK, payload)
            return
        if self.server.mode == "ros" and path == "/test-n10-feedback":
            if self.server.drafts is None:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "draft_store_unavailable")
                return
            try:
                feedback = test_n10_feedback(self.server.drafts,
                                             self.server.tm_preview)
            except DraftError:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "test_feedback_invalid")
                return
            self._json(HTTPStatus.OK, feedback)
            return
        if self.server.mode == "ros" and path == "/test-tm-snapshot":
            try:
                snapshot = load_snapshot(self.server.tm_preview)
            except PreviewError:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "test_tm_unavailable")
                return
            if len(json.dumps(snapshot, separators=(",", ":"),
                              ensure_ascii=False).encode("utf-8")) > MAX_SNAPSHOT_BYTES:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "test_tm_unavailable")
                return
            self._json(HTTPStatus.OK, snapshot)
            return
        if self.server.mode == "ros" and path == "/test-beacon":
            try:
                beacon = load_beacon(self.server.tm_preview)
            except PreviewError:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "test_beacon_unavailable")
                return
            self._json(HTTPStatus.OK, beacon)
            return
        if self.server.mode == "ros" and path in ("/test-sequences", "/test-reservations",
                                                   "/test-sim-sequences",
                                                   "/test-sim-reservations",
                                                   "/test-sim-tm-requests"):
            if self.server.drafts is None:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "draft_store_unavailable")
                return
            items = ({"/test-sequences": self.server.drafts.list_sequences,
                      "/test-reservations": self.server.drafts.reservations,
                      "/test-sim-sequences": self.server.drafts.list_sim_sequences,
                      "/test-sim-reservations": self.server.drafts.list_sim_reservations,
                      "/test-sim-tm-requests": self.server.drafts.list_sim_tm_requests}[path]())
            self._json(HTTPStatus.OK, {"items": items, "operational": False,
                       "sim_tc_available": self.server.sim_tc_socket is not None})
            return
        if self.server.mode == "ros" and path == "/overview":
            try:
                summary = ros_summary(self.server.core_socket)
            except CoreUnavailable:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "ros_core_unavailable")
                return
            self._json(HTTPStatus.OK, {
                "mode": "SIM_ONLY", "operational": False,
                "tm_icd": "TBD(ICD)", "tc_send_available": False,
                "sim_tc_available": self.server.sim_tc_socket is not None,
                "message": "Flight TC sending is unavailable; simulator loopback is test-only.",
                **summary})
            return
        if self.server.mode == "mps" and path == "/catalog":
            try:
                catalog = mps_catalog(self.server.core_socket)
                proposals = draft_commands()
            except (CoreUnavailable, CandidateError, OSError, ValueError):
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "mps_core_unavailable")
                return
            self._json(HTTPStatus.OK, {
                "version": catalog["version"], "commands": [],
                "draft_commands": proposals,
                "operational": False, "status": "DRAFT_NOT_FLIGHT_APPROVED"})
            return
        if self.server.mode == "mps" and path == "/passes":
            try:
                schedule = mps_passes(self.server.core_socket)
            except CoreUnavailable:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "mps_schedule_unavailable")
                return
            self._json(HTTPStatus.OK, {**schedule,
                "status": "VALIDATED_N13" if schedule["schedule_state"] == "validated"
                          else "N13_NOT_RECEIVED"})
            return
        if self.server.mode == "mps" and path in ("/test-candidates", "/test-sequences"):
            if self.server.candidates is None:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "candidate_store_unavailable")
                return
            try:
                schedule = mps_passes(self.server.core_socket)
            except CoreUnavailable:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "mps_schedule_unavailable")
                return
            items = (self.server.candidates.list_sequences(schedule) if path == "/test-sequences"
                     else self.server.candidates.list_current(schedule))
            self._json(HTTPStatus.OK, {"items": items,
                                       "operational": False})
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found")

    def _backend_candidate_post(self, path: str) -> None:
        if not self._backend_identity() or self.server.candidates is None:
            self._error(HTTPStatus.FORBIDDEN, "client_identity")
            return
        actor = self.headers.get("X-Pado-Actor", "")
        role = self.headers.get("X-Pado-Role", "")
        if not actor or len(actor) > 64 or role not in ("operator", "lead", "planner"):
            self._error(HTTPStatus.FORBIDDEN, "portal_identity")
            return
        body = self._read_json(MAX_DRAFT_BODY)
        content_key = "steps" if path == "/test-sequences" else "candidate_ids"
        expected = ({"pass_id", "schedule_sha256", "expected_revision"} if
                    path == "/test-sequences/handoff" else
                    {"pass_id", "schedule_sha256", "expected_revision", content_key})
        if body is None or set(body) != expected:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_request")
            return
        try:
            schedule = mps_passes(self.server.core_socket)
            if path == "/test-sequences/handoff":
                result = self.server.candidates.handoff(
                    schedule, body["pass_id"], body["schedule_sha256"],
                    body["expected_revision"], actor)
            elif path == "/test-sequences":
                result = self.server.candidates.save_sequence(
                    schedule, body["pass_id"], body["schedule_sha256"],
                    body["expected_revision"], body["steps"], actor)
            else:
                result = self.server.candidates.save(schedule, body["pass_id"],
                                                     body["schedule_sha256"],
                                                     body["expected_revision"],
                                                     body["candidate_ids"], actor)
        except CoreUnavailable:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "mps_schedule_unavailable")
            return
        except CandidateError as exc:
            code = str(exc)
            self._error(HTTPStatus.CONFLICT if code in ("stale_schedule", "revision_conflict")
                        else HTTPStatus.BAD_REQUEST, code)
            return
        self._json(HTTPStatus.OK, result)

    def _backend_draft_post(self, path: str) -> None:
        if not self._backend_identity() or self.server.drafts is None:
            self._error(HTTPStatus.FORBIDDEN, "client_identity")
            return
        actor = self.headers.get("X-Pado-Actor", "")
        role = self.headers.get("X-Pado-Role", "")
        if not actor or len(actor) > 64 or role not in ("operator", "lead", "planner"):
            self._error(HTTPStatus.FORBIDDEN, "portal_identity")
            return
        body = self._read_json(MAX_DRAFT_BODY)
        if body is None:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_request")
            return
        try:
            if path == "/test-sequences" and set(body) == {"sequence_text", "manifest_text"}:
                result = self.server.drafts.import_pair(
                    body["sequence_text"], body["manifest_text"], actor)
            elif path == "/test-reservations" and set(body) == {"sha256", "step_id", "target_utc_s"}:
                result = self.server.drafts.reserve(body["sha256"], body["step_id"],
                                                    body["target_utc_s"], actor)
            elif path == "/test-reservations/cancel" and set(body) == {"id"}:
                self.server.drafts.cancel(body["id"], actor)
                result = {"status": "CANCELLED"}
            elif path == "/test-sim-handoffs":
                result = self.server.drafts.import_sim_handoff(body, actor)
            elif path == "/test-sim-reservations" and set(body) == {"sha256", "step_id"}:
                result = self.server.drafts.reserve_sim(body["sha256"], body["step_id"], actor)
            elif path == "/test-sim-reservations" and set(body) == {"sha256"}:
                result = self.server.drafts.reserve_sim_batch(body["sha256"], actor)
            elif path == "/test-sim-reservations/cancel" and set(body) == {"id"}:
                self.server.drafts.cancel_sim(body["id"], actor)
                result = {"status": "CANCELLED"}
            elif path == "/test-sim-reservations/cancel" and set(body) == {"batch_id"}:
                result = self.server.drafts.cancel_sim_batch(body["batch_id"], actor)
            elif path == "/test-sim-tm-requests" and not body:
                if self.server.sim_tc_socket is None:
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, "sim_tc_unavailable")
                    return
                result = self.server.drafts.request_sim_tm(actor)
            else:
                raise DraftError("invalid_request")
        except DraftError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._json(HTTPStatus.OK, {**result, "operational": False})


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="mode", required=True)
    user = sub.add_parser("add-user", help="provision one local portal user")
    user.add_argument("--db", type=Path, required=True)
    user.add_argument("--username", required=True)
    user.add_argument("--role", choices=sorted(ROLES), required=True)
    account = sub.add_parser("account-status", help="report whether any portal user exists")
    account.add_argument("--db", type=Path, required=True)
    token = sub.add_parser("init-internal-token", help="create local HTTP backend token")
    token.add_argument("--path", type=Path, required=True)
    for mode in ("portal", "ros", "mps"):
        service = sub.add_parser(mode)
        service.add_argument("--bind", default="127.0.0.1")
        service.add_argument("--port", type=int, required=True)
        service.add_argument("--transport", choices=("tls", "http"), default="tls")
        service.add_argument("--internal-token-file", type=Path)
        service.add_argument("--cert", type=Path)
        service.add_argument("--key", type=Path)
        service.add_argument("--ca", type=Path)
        if mode == "portal":
            service.add_argument("--db", type=Path, required=True)
            service.add_argument("--ros-url", required=True)
            service.add_argument("--mps-url", required=True)
            service.add_argument("--client-cert", type=Path)
            service.add_argument("--client-key", type=Path)
            service.add_argument("--static-root", type=Path, default=WEB)
            service.add_argument("--orbit-view", type=Path)
            service.add_argument("--synthetic-orbit-view", type=Path)
            service.add_argument("--iss-orbit-view", type=Path)
            service.add_argument("--operational-orbit-view", type=Path)
        if mode in ("ros", "mps"):
            service.add_argument("--core-socket", type=Path, default=CORE_SOCKET)
        if mode == "ros":
            service.add_argument("--draft-db", type=Path)
            service.add_argument("--tm-preview", type=Path)
            service.add_argument("--sim-tc-socket", type=Path)
        if mode == "mps":
            service.add_argument("--candidate-db", type=Path)
    return result


def main() -> None:
    args = parser().parse_args()
    os.umask(0o077)
    if args.mode == "init-internal-token":
        initialize_internal_token(args.path)
        return
    if args.mode == "account-status":
        store = AuthStore(args.db)
        try:
            print("configured" if store.db.execute("SELECT 1 FROM users LIMIT 1").fetchone()
                  else "empty")
        finally:
            store.close()
        return
    if args.mode == "add-user":
        entered = getpass.getpass("Password (minimum 12 characters): ")
        repeat = getpass.getpass("Repeat password: ")
        if entered != repeat:
            raise SystemExit("passwords differ")
        totp_seed = secrets.token_bytes(20) if args.role == "lead" else None
        if isinstance(totp_seed, bytes):
            import base64
            totp_seed = base64.b32encode(totp_seed).decode("ascii")
        store = AuthStore(args.db)
        try:
            store.create_user(args.username, args.role, entered, totp_seed)
        finally:
            store.close()
        if totp_seed:
            print("TOTP secret (record offline once):", totp_seed)
        return
    auth = AuthStore(args.db) if args.mode == "portal" else None
    server = AppServer((args.bind, args.port), mode=args.mode,
                       cert=args.cert, key=args.key, ca=args.ca,
                       auth=auth, ros_url=getattr(args, "ros_url", ""),
                       mps_url=getattr(args, "mps_url", ""),
                       client_cert=getattr(args, "client_cert", None),
                       client_key=getattr(args, "client_key", None),
                       static_root=getattr(args, "static_root", WEB),
                       core_socket=getattr(args, "core_socket", CORE_SOCKET),
                       orbit_view=getattr(args, "orbit_view", None),
                       synthetic_orbit_view=getattr(args, "synthetic_orbit_view", None),
                       iss_orbit_view=getattr(args, "iss_orbit_view", None),
                       operational_orbit_view=getattr(args, "operational_orbit_view", None),
                       tm_preview=getattr(args, "tm_preview", None),
                       draft_db=getattr(args, "draft_db", None),
                       candidate_db=getattr(args, "candidate_db", None),
                       sim_tc_socket=getattr(args, "sim_tc_socket", None),
                       transport=args.transport,
                       internal_token_file=args.internal_token_file)
    dispatcher = (SimTcDispatcher(server.drafts, args.sim_tc_socket)
                  if args.mode == "ros" and server.drafts and args.sim_tc_socket else None)
    if dispatcher:
        dispatcher.start()
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        if dispatcher:
            dispatcher.stop()
        server.server_close()
        if server.drafts:
            server.drafts.close()
        if server.candidates:
            server.candidates.close()
        if auth:
            auth.close()


if __name__ == "__main__":
    main()
