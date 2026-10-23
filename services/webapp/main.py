"""TLS portal and mutually authenticated read-only subsystem APIs.

This is a non-operational development boundary. No TC, approval or upload
endpoint exists. Run each mode as a separate process/container.
"""

from __future__ import annotations

import argparse
import getpass
import http.client
import json
import os
import secrets
import ssl
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .auth import AuthError, AuthStore, ROLES


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
COMMAND_DB = ROOT / "interfaces" / "command_db" / "commands.json"
MAX_BODY = 4096
MAX_UPSTREAM = 65536
ASSETS = {
    "/login": ("login/index.html", "text/html; charset=utf-8"),
    "/ops": ("ops/index.html", "text/html; charset=utf-8"),
    "/mps": ("mps/index.html", "text/html; charset=utf-8"),
    "/assets/style.css": ("common/style.css", "text/css; charset=utf-8"),
    "/assets/app.js": ("common/app.js", "text/javascript; charset=utf-8"),
}
PROXY_ROUTES = {
    "/api/ros/overview": ("ros", "/overview"),
    "/api/mps/catalog": ("mps", "/catalog"),
    "/api/mps/passes": ("mps", "/passes"),
}


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address: tuple[str, int], *, mode: str,
                 cert: Path, key: Path, ca: Path, auth: AuthStore | None = None,
                 ros_url: str = "", mps_url: str = "", client_cert: Path | None = None,
                 client_key: Path | None = None, command_db: Path = COMMAND_DB,
                 tm_record: Path | None = None, static_root: Path = WEB) -> None:
        self.mode = mode
        self.auth = auth
        self.ros_url = ros_url
        self.mps_url = mps_url
        self.client_cert = client_cert
        self.client_key = client_key
        self.ca = ca
        self.command_db = command_db
        self.tm_record = tm_record
        self.static_root = static_root
        self.login_attempts: dict[str, list[float]] = {}
        self.login_lock = threading.Lock()
        super().__init__(address, Handler)
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
              "application/json; charset=utf-8", extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
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
        if parsed.query or parsed.fragment or not parsed.path.startswith("/"):
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
        if self.server.mode != "portal" or path not in ("/api/login", "/api/logout"):
            self._error(HTTPStatus.NOT_FOUND, "not_found")
            return
        if path == "/api/login":
            self._login()
        else:
            self._logout()

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", ""))
            if length < 0 or length > MAX_BODY or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return None
            value = json.loads(self.rfile.read(length))
            return value if isinstance(value, dict) else None
        except (ValueError, UnicodeDecodeError):
            return None

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
                    "; Path=/; HttpOnly; Secure; SameSite=Strict"})

    def _logout(self) -> None:
        session = self._session()
        token = self._cookie_token()
        csrf = self.headers.get("X-CSRF-Token", "")
        if not session or not secrets.compare_digest(session.csrf, csrf):
            self._error(HTTPStatus.FORBIDDEN, "csrf_or_session")
            return
        self.server.auth.logout(token)
        self._json(HTTPStatus.OK, {"logged_out": True},
                   {"Set-Cookie": "pado_session=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict"})

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
        if path in PROXY_ROUTES:
            session = self._session()
            if not session:
                self._error(HTTPStatus.UNAUTHORIZED, "login_required")
                return
            self._proxy(PROXY_ROUTES[path], session.username, session.role)
            return
        if path in ASSETS:
            if path in ("/ops", "/mps") and not self._session():
                self._send(HTTPStatus.FOUND, extra={"Location": "/login"})
                return
            relative, content_type = ASSETS[path]
            source = self.server.static_root / relative
            try:
                data = source.read_bytes()
            except OSError:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "asset_unavailable")
                return
            self._send(HTTPStatus.OK, data, content_type)
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found")

    def _proxy(self, route: tuple[str, str], username: str, role: str) -> None:
        service, path = route
        base = self.server.ros_url if service == "ros" else self.server.mps_url
        address = urlsplit(base)
        if address.scheme != "https" or not address.hostname or not address.port:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "backend_configuration")
            return
        context = ssl.create_default_context(cafile=str(self.server.ca))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(self.server.client_cert),
                                str(self.server.client_key))
        try:
            connection = http.client.HTTPSConnection(address.hostname, address.port,
                                                     context=context, timeout=3)
            connection.request("GET", path, headers={"X-Pado-Actor": username,
                                                     "X-Pado-Role": role})
            response = connection.getresponse()
            data = response.read(MAX_UPSTREAM + 1)
            if len(data) > MAX_UPSTREAM or response.status != HTTPStatus.OK:
                self._error(HTTPStatus.BAD_GATEWAY, "backend_failure")
            else:
                self._send(HTTPStatus.OK, data)
            connection.close()
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
        if self.server.mode == "ros" and path == "/overview":
            self._json(HTTPStatus.OK, {
                "mode": "SIM_ONLY", "operational": False,
                "tm_icd": "TBD(ICD)", "tc_send_available": False,
                "message": "Actual TM parameters and TC sending are unavailable."})
            return
        if self.server.mode == "mps" and path == "/catalog":
            try:
                raw = self.server.command_db.read_bytes()
                if len(raw) > 4096:
                    raise ValueError("oversized command DB")
                catalog = json.loads(raw)
                if (catalog.get("operational") is not False or
                        catalog.get("commands") != [] or
                        type(catalog.get("version")) is not int or
                        catalog["version"] < 1):
                    raise ValueError("unsupported command DB")
            except (OSError, ValueError, UnicodeError, AttributeError, TypeError):
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "command_db_unavailable")
                return
            self._json(HTTPStatus.OK, {
                "version": catalog["version"], "commands": [],
                "operational": False, "status": "TBD(ICD)"})
            return
        if self.server.mode == "mps" and path == "/passes":
            self._json(HTTPStatus.OK, {
                "passes": [], "operational": False,
                "status": "TBD(FDS Contact Schedule ICD)"})
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="mode", required=True)
    user = sub.add_parser("add-user", help="provision one local portal user")
    user.add_argument("--db", type=Path, required=True)
    user.add_argument("--username", required=True)
    user.add_argument("--role", choices=sorted(ROLES), required=True)
    for mode in ("portal", "ros", "mps"):
        service = sub.add_parser(mode)
        service.add_argument("--bind", default="127.0.0.1")
        service.add_argument("--port", type=int, required=True)
        service.add_argument("--cert", type=Path, required=True)
        service.add_argument("--key", type=Path, required=True)
        service.add_argument("--ca", type=Path, required=True)
        if mode == "portal":
            service.add_argument("--db", type=Path, required=True)
            service.add_argument("--ros-url", required=True)
            service.add_argument("--mps-url", required=True)
            service.add_argument("--client-cert", type=Path, required=True)
            service.add_argument("--client-key", type=Path, required=True)
            service.add_argument("--static-root", type=Path, default=WEB)
        if mode == "mps":
            service.add_argument("--command-db", type=Path, default=COMMAND_DB)
    return result


def main() -> None:
    args = parser().parse_args()
    os.umask(0o077)
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
                       command_db=getattr(args, "command_db", COMMAND_DB),
                       static_root=getattr(args, "static_root", WEB))
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        if auth:
            auth.close()


if __name__ == "__main__":
    main()
