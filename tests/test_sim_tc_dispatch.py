"""ROS simulator dispatcher validates and correlates the private ACK."""

import stat
import struct
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.webapp.ros_workbench import RosDraftStore
from services.webapp.sim_tc_dispatch import SimDispatchError, SimTcDispatcher, send_sim_tc
from tests.test_ros_sim_handoff import handoff
from tools.pado_packet_codec import decode, encode_tm


class FakeSocketPath:
    def is_symlink(self):
        return False

    def is_socket(self):
        return True

    def stat(self):
        return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600)

    def __str__(self):
        return "/run/sim-tc/bridge.sock"


class FakeBridge:
    def __init__(self, *, wrong_id=False, result=0, reason=0):
        self.wrong_id = wrong_id
        self.result = result
        self.reason = reason
        self.response = bytearray()
        self.sent = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def settimeout(self, timeout):
        assert timeout == 15.0

    def connect(self, path):
        assert path == "/run/sim-tc/bridge.sock"

    def sendall(self, frame):
        size = struct.unpack(">I", frame[:4])[0]
        assert size == len(frame) - 4
        self.sent = decode(frame[4:])
        fields = {
            "command_id": self.sent["id"] + int(self.wrong_id),
            "command_sequence": self.sent["sequence"],
            "result": self.result,
            "reason": self.reason,
            "executed_at_utc_ms": self.sent["utc_ms"] if self.result == 0 else 0,
        }
        ack = encode_tm("COMMAND_RESULT", 4, self.sent["utc_ms"], fields)
        self.response = bytearray(struct.pack(">I", len(ack)) + ack)

    def recv(self, size):
        chunk = bytes(self.response[:size])
        del self.response[:size]
        return chunk


class SimTcDispatchTests(unittest.TestCase):
    def setUp(self):
        # Windows Python can omit AF_UNIX; the connection is a protocol fake.
        unix_family = patch("services.webapp.sim_tc_dispatch.socket.AF_UNIX", 1,
                            create=True)
        unix_family.start()
        self.addCleanup(unix_family.stop)

    def test_immediate_draft_tc_and_matching_ack(self):
        bridge = FakeBridge()
        with patch("services.webapp.sim_tc_dispatch.socket.socket", return_value=bridge):
            ack = send_sim_tc(FakeSocketPath(), {
                "command_name": "hk_get", "command_sequence": 7, "parameters": {},
            }, now_ms=1_800_000_000_000)
        self.assertEqual(bridge.sent["name"], "hk_get")
        self.assertFalse(bridge.sent["operational"])
        self.assertEqual(bridge.sent["execute_at_utc_ms"], 0)
        self.assertEqual(ack["command_sequence"], 7)
        self.assertEqual(ack["result"], 0)

    def test_rejects_mismatched_ack_and_mutating_command(self):
        with patch("services.webapp.sim_tc_dispatch.socket.socket",
                   return_value=FakeBridge(wrong_id=True)):
            with self.assertRaisesRegex(SimDispatchError, "does not match"):
                send_sim_tc(FakeSocketPath(), {
                    "command_name": "hk_get", "command_sequence": 7,
                    "parameters": {},
                }, now_ms=1_800_000_000_000)
        with self.assertRaisesRegex(SimDispatchError, "read-only"):
            send_sim_tc(FakeSocketPath(), {
                "command_name": "mm_set_mode", "command_sequence": 8,
                "parameters": {"mode": 1},
            }, now_ms=1_800_000_000_000)

    def test_inconsistent_success_is_rejected(self):
        with patch("services.webapp.sim_tc_dispatch.socket.socket",
                   return_value=FakeBridge(result=0, reason=1)):
            with self.assertRaisesRegex(SimDispatchError, "inconsistent"):
                send_sim_tc(FakeSocketPath(), {
                    "command_name": "mm_get_mode", "command_sequence": 2,
                    "parameters": {},
                }, now_ms=1_800_000_000_000)

    def test_rejected_ack_is_recordable(self):
        with patch("services.webapp.sim_tc_dispatch.socket.socket",
                   return_value=FakeBridge(result=1, reason=1)):
            ack = send_sim_tc(FakeSocketPath(), {
                "command_name": "mm_get_mode", "command_sequence": 2,
                "parameters": {},
            }, now_ms=1_800_000_000_000)
        self.assertEqual((ack["result"], ack["reason"], ack["executed_at_utc_ms"]),
                         (1, 1, 0))

    def test_due_reservation_fails_if_simulator_socket_is_down(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = RosDraftStore(root / "ros.db")
            target = int(time.time()) + 3600
            envelope = handoff(target)
            envelope["steps"] = [{"offset_s": 0, "command_name": "hk_get",
                                  "parameters": {}}]
            digest = store.import_sim_handoff(envelope, "planner01")["sha256"]
            reservation = store.reserve_sim(digest, 1, "planner01")
            dispatcher = SimTcDispatcher(store, root / "absent.sock")
            with patch("services.webapp.sim_tc_dispatch.time.time",
                       return_value=target):
                dispatcher.start()
                try:
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        current = next(row for row in store.list_sim_reservations()
                                       if row["id"] == reservation["id"])
                        if current["status"] == "SEND_FAILED":
                            break
                        time.sleep(0.02)
                    self.assertEqual(current["status"], "SEND_FAILED")
                finally:
                    dispatcher.stop()
                    store.close()


if __name__ == "__main__":
    unittest.main()
