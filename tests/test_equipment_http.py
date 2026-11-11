"""The equipment overview shares the authenticated, read-only ROS boundary."""

import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from services.sim_ground.archive import PacketArchive
from services.webapp.auth import AuthStore
from services.webapp.main import AppServer, initialize_internal_token
from tests.test_stage9_http import FakeHasher
from tools.pado_packet_codec import decode, encode_tm


ROOT = Path(__file__).resolve().parents[1]


class EquipmentHttpTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "out").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="equipment-http-", dir=ROOT / "out")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "spacecraft.json").write_bytes((ROOT / "config/spacecraft.example.json").read_bytes())
        self.layout = self.root / "equipment-layout.json"
        self.layout.write_bytes((ROOT / "config/equipment-layout.example.json").read_bytes())
        self.archive = PacketArchive(self.root / "received_tm.sqlite3")
        self.addCleanup(self.archive.close)
        self.now = int(time.time() * 1000)
        self.append(1, self.now - 2000, 5.1)
        token = self.root / "token"
        initialize_internal_token(token)
        self.auth = AuthStore(self.root / "users.db", FakeHasher())
        self.addCleanup(self.auth.close)
        self.run_password = secrets.token_urlsafe(24)
        self.auth.create_user("audit01", "auditor", self.run_password)
        with patch.dict(os.environ, {"PADO_ENVIRONMENT": "legacy"}):
            self.backend = AppServer(("127.0.0.1", 0), mode="ros", transport="http",
                internal_token_file=token, tm_preview=self.root / "test_tm_snapshot.json",
                settings_file=self.root / "spacecraft.json")
            self.portal = AppServer(("127.0.0.1", 0), mode="portal", transport="http",
                internal_token_file=token, auth=self.auth,
                ros_url=f"http://127.0.0.1:{self.backend.server_port}")
        self.threads = []
        for server in (self.backend, self.portal):
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            self.threads.append(worker)
        self.addCleanup(self.stop)

    def stop(self):
        for server in (self.portal, self.backend):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(2)

    def append(self, sequence, stamp, voltage):
        packet = encode_tm("EPS_PDU1", sequence, stamp, {"channel_enabled_bits": 3,
            "latchup_bits": 0, "bus_voltage_v": voltage, "temperature_c": 22., "uptime_s": 10})
        self.archive.append(packet, decode(packet), self.now)

    def request(self, server, path, *, headers=None, body=None):
        request = Request(f"http://127.0.0.1:{server.server_port}" + path,
            headers={**({"Content-Type": "application/json"} if body is not None else {}), **(headers or {})},
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(request, timeout=4) as response:
                return response.status, response.headers, json.loads(response.read())
        except HTTPError as error:
            return error.code, error.headers, json.loads(error.read())
        except URLError as error:
            if isinstance(error.reason, PermissionError):
                self.skipTest("local sandbox denies loopback; HTTP boundary runs on Linux")
            raise

    def test_proxy_measurement_order_and_live_configuration(self):
        path = "/api/ros/equipment-status"
        self.assertEqual(self.request(self.portal, path)[0], 401)
        self.assertEqual(self.request(self.backend, "/equipment-status", headers={
            "X-Pado-Actor": "audit01", "X-Pado-Role": "auditor"})[0], 403)
        status, headers, _ = self.request(self.portal, "/api/login", body={
            "username": "audit01", "password": self.run_password})
        self.assertEqual(status, 200)
        cookie = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        self.append(2, self.now - 60000, 9.9)
        status, _, body = self.request(self.portal, path, headers=cookie)
        self.assertEqual(status, 200, body)
        pdu = next(item for item in body["equipment"] if item["id"] == "pdu1")
        voltage = next(row for row in pdu["readings"] if row["id"] == "voltage")
        self.assertAlmostEqual(voltage["value"], 5.1, places=5)
        self.assertEqual(voltage["limit_state"], "IN_RANGE")
        self.assertEqual(next(item for item in body["equipment"] if item["id"] == "satnogs")["status"], "MISSING")
        document = json.loads(self.layout.read_text(encoding="utf-8"))
        document["equipment"][0]["anchor_body_mm"] = [4, 2, 90]
        self.layout.write_text(json.dumps(document), encoding="utf-8")
        status, _, body = self.request(self.portal, path, headers=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(body["equipment"][0]["anchor_body_mm"], [4, 2, 90])
        self.layout.write_text("{}", encoding="utf-8")
        status, _, body = self.request(self.portal, path, headers=cookie)
        self.assertEqual(status, 503)
        self.assertEqual(body["error"], "equipment_layout_invalid")
        self.assertEqual(self.request(self.portal, path + "?path=../secret", headers=cookie)[0], 400)


if __name__ == "__main__":
    unittest.main()
