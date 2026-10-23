import json
import struct
import unittest
from pathlib import Path
from unittest.mock import patch

from services.webapp.core import CoreUnavailable, mps_catalog, ros_summary


class FakeSocket:
    def __init__(self, body, failure=None):
        payload = json.dumps(body).encode() if isinstance(body, dict) else body
        self.stream = bytearray(struct.pack(">I", len(payload)) + payload)
        self.failure = failure
        self.sent = b""
        self.path = ""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def settimeout(self, _):
        pass

    def connect(self, path):
        if self.failure:
            raise self.failure
        self.path = path

    def sendall(self, data):
        self.sent = data

    def recv(self, length):
        result = bytes(self.stream[:length])
        del self.stream[:length]
        return result


class CoreAdapterTests(unittest.TestCase):
    @staticmethod
    def query_with(body, function):
        connection = FakeSocket(body)
        with patch("services.webapp.core.socket.socket", return_value=connection):
            result = function(Path("/core.sock"))
        return result, connection

    def test_ros_validated_summary_uses_bounded_frame(self):
        result, connection = self.query_with(
            {"sim_frame_count": 2, "last_sequence": 42,
             "record_state": "validated"}, ros_summary)
        self.assertEqual(result["last_sequence"], 42)
        self.assertEqual(connection.path, str(Path("/core.sock")))
        self.assertEqual(connection.sent,
                         struct.pack(">I", 17) + b'{"op":"overview"}')

    def test_invalid_core_result_fails_closed(self):
        for body in (
            {"sim_frame_count": True, "last_sequence": 1, "record_state": "validated"},
            {"sim_frame_count": 1, "last_sequence": None, "record_state": "validated"},
            {"sim_frame_count": 0, "last_sequence": None, "record_state": "validated"},
            {"error": "core_unavailable"},
        ):
            with self.assertRaises(CoreUnavailable):
                self.query_with(body, ros_summary)
        for body in (
            {"version": 1, "command_count": 1, "operational": False},
            {"version": 1, "command_count": 0, "operational": True},
        ):
            with self.assertRaises(CoreUnavailable):
                self.query_with(body, mps_catalog)

    def test_unavailable_or_malformed_socket_fails_closed(self):
        connection = FakeSocket({}, OSError("unavailable"))
        with patch("services.webapp.core.socket.socket", return_value=connection):
            with self.assertRaises(CoreUnavailable):
                mps_catalog(Path("/core.sock"))
        for body in (b"not-json", b"[]", b"" ):
            with self.assertRaises(CoreUnavailable):
                self.query_with(body, ros_summary)


if __name__ == "__main__":
    unittest.main()
