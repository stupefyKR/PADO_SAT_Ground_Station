"""Authenticated read-only packet archive: pagination, exact raw PDU and fields."""

import time
import unittest
from urllib.error import URLError

from services.sim_ground.archive import PacketArchive
from tests import test_sim_tc_web as web_fixture
from tools.pado_packet_codec import decode, encode_tm


class ReceivedTmWebTests(unittest.TestCase):
    setUp = web_fixture.SimTcHttpTests.setUp
    tearDown = web_fixture.SimTcHttpTests.tearDown
    request = web_fixture.SimTcHttpTests.request

    def login(self):
        try:
            status, _, _ = self.request("/api/ros/received-tm?limit=50")
        except URLError as error:
            if isinstance(error.reason, PermissionError):
                self.skipTest("sandbox denies loopback connections")
            raise
        self.assertEqual(status, 401)
        status, headers, _ = self.request("/api/login", body={
            "username": "planner01", "password": "valid-password-123"})
        self.assertEqual(status, 200)
        self.ros.tm_preview = self.folder / "test_tm_snapshot.json"
        return {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}

    def populate(self):
        stamp = int(time.time()) * 1000
        archive = PacketArchive(self.folder / "received_tm.sqlite3")
        packets = []
        try:
            for index in range(3):
                packet = encode_tm("EPS_PDU1", index, stamp - (3 - index) * 5000, {
                    "channel_enabled_bits": 3, "latchup_bits": 0,
                    "bus_voltage_v": 5.0 + index * 0.1,
                    "temperature_c": 22.0, "uptime_s": index * 5})
                archive.append(packet, decode(packet), stamp)
                packets.append(packet)
            event = encode_tm("FDIR_EVENT", 4, stamp, {"level": 1,
                "source_node": 2, "fault_code": 17, "action_code": 0, "recovery_state": 0})
            archive.append(event, decode(event), stamp)
        finally:
            archive.close()
        return packets

    def test_exact_raw_fields_pagination_and_events(self):
        cookie = self.login()
        packets = self.populate()
        status, _, page = self.request(
            "/api/ros/received-tm?limit=2&name=EPS_PDU1", headers=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(page["total"], 3)
        self.assertEqual([row["id"] for row in page["items"]], [3, 2])
        self.assertEqual(page["next_before_id"], 2)
        status, _, older = self.request(
            "/api/ros/received-tm?limit=2&before_id=2&name=EPS_PDU1", headers=cookie)
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in older["items"]], [1])
        status, _, detail = self.request("/api/ros/received-tm/1", headers=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(detail["label"], "TM0001")
        self.assertEqual(detail["raw_hex"], packets[0].hex())
        self.assertEqual(detail["header"]["packet_id"], 8209)
        self.assertAlmostEqual(detail["fields"]["bus_voltage_v"], 5.0)
        self.assertFalse(detail["operational"])
        status, _, events = self.request("/api/ros/tm-events?limit=10", headers=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(events["items"][0]["fields"]["fault_code"], 17)
        status, _, _ = self.request("/api/ros/received-tm/99999", headers=cookie)
        self.assertEqual(status, 404)

    def test_query_validation_and_archive_absence(self):
        cookie = self.login()
        status, _, _ = self.request("/api/ros/received-tm", headers=cookie)
        self.assertEqual(status, 503)
        for query in ("limit=101", "limit=0", "limit=2&limit=3", "name=../private",
                      "before_id=-1", "unknown=1", "limit=", "limit=1%262", "limit=01"):
            status, _, _ = self.request("/api/ros/received-tm?" + query, headers=cookie)
            self.assertEqual(status, 400, query)
        status, _, _ = self.request("/api/ros/tm-events?name=EPS_PDU1", headers=cookie)
        self.assertEqual(status, 400)
        status, _, _ = self.request("/api/ros/received-tm/1?limit=1", headers=cookie)
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
