"""Read-only Stage 9 bridge tests against the actual C parser and TM validator."""

import json
import os
import socket
import struct
import subprocess
import tempfile
import time
import unittest
import zlib
from pathlib import Path

from services.webapp.core import CoreUnavailable, mps_catalog, ros_summary


ROOT = Path(__file__).resolve().parents[1]
CORE_BIN = Path(os.environ.get(
    "PADO_STAGE9_CORE_PROBE_TEST",
    ROOT / "build-linux-release" / "gs_stage9_core_probe",
))


@unittest.skipUnless(os.name == "posix" and CORE_BIN.is_file(),
                     "Linux C core probe unavailable")
class CoreProbeTests(unittest.TestCase):
    def call(self, command, path):
        return subprocess.run([str(CORE_BIN), command, str(path)],
                              capture_output=True, timeout=2, check=False)

    def start_service(self, mode, socket_path, source):
        process = subprocess.Popen(
            [str(CORE_BIN), "serve", mode, str(socket_path), str(source)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        until = time.monotonic() + 3
        while time.monotonic() < until:
            if socket_path.exists():
                return process
            if process.poll() is not None:
                break
            time.sleep(0.02)
        error = process.communicate(timeout=2)[1]
        self.fail(f"C reader did not start: {error!r}")

    def test_resident_services_validate_each_request_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ros_socket = root / "ros.sock"
            mps_socket = root / "mps.sock"
            record = root / "ros.record"
            catalog_file = root / "commands.json"
            catalog_file.write_bytes(
                (ROOT / "interfaces/command_db/commands.json").read_bytes())
            ros = self.start_service("ros", ros_socket, record)
            mps = self.start_service("mps", mps_socket, catalog_file)
            try:
                self.assertEqual(ros_socket.stat().st_mode & 0o777, 0o600)
                self.assertEqual(mps_socket.stat().st_mode & 0o777, 0o600)
                self.assertEqual(ros_summary(ros_socket)["record_state"], "absent")
                self.assertEqual(mps_catalog(mps_socket)["version"], 1)
                header = (b"PSTSIM03" + bytes([1, 1, 0x7e, 0x57]) +
                          struct.pack(">H", 3) + struct.pack(">I", 51) + b"abc")
                frame = header + struct.pack(">I", zlib.crc32(header))
                content = bytes([1]) + struct.pack(">I", len(frame)) + frame
                record.write_bytes(content)
                record.chmod(0o600)
                self.assertEqual(ros_summary(ros_socket)["last_sequence"], 51)
                record.write_bytes(content[:-1])
                with self.assertRaises(CoreUnavailable):
                    ros_summary(ros_socket)
                catalog_file.write_text("{}", encoding="utf-8")
                with self.assertRaises(CoreUnavailable):
                    mps_catalog(mps_socket)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                    peer.settimeout(1)
                    peer.connect(str(ros_socket))
                    request = b'{"op":"catalog"}'
                    peer.sendall(struct.pack(">I", len(request)) + request)
                    self.assertEqual(peer.recv(1), b"")
            finally:
                ros.terminate()
                mps.terminate()
                self.assertEqual(ros.wait(timeout=3), 0)
                self.assertEqual(mps.wait(timeout=3), 0)
            with self.assertRaises(CoreUnavailable):
                ros_summary(ros_socket)

    def test_mps_catalog_uses_c_parser_and_rejects_modified_envelope(self):
        valid = self.call("mps-catalog", ROOT / "interfaces/command_db/commands.json")
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertEqual(json.loads(valid.stdout), {
            "version": 1, "command_count": 0, "operational": False})
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "commands.json"
            source.write_text('{"version":1,"version":1,"operational":false,'
                              '"status":"TBD(ICD)","source":"test","commands":[]}',
                              encoding="utf-8")
            self.assertNotEqual(self.call("mps-catalog", source).returncode, 0)
            source.write_bytes(b"{" + b" " * 4096 + b"}")
            self.assertNotEqual(self.call("mps-catalog", source).returncode, 0)

    def test_ros_record_counts_only_validated_simulator_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            record = Path(folder) / "ros.record"
            absent = self.call("ros-summary", record)
            self.assertEqual(json.loads(absent.stdout)["record_state"], "absent")
            header = (b"PSTSIM03" + bytes([1, 1, 0x7e, 0x57]) +
                      struct.pack(">H", 3) + struct.pack(">I", 42) + b"abc")
            frame = header + struct.pack(">I", zlib.crc32(header))
            content = bytes([1]) + struct.pack(">I", len(frame)) + frame
            record.write_bytes(content)
            record.chmod(0o600)
            valid = self.call("ros-summary", record)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertEqual(json.loads(valid.stdout), {
                "sim_frame_count": 1, "last_sequence": 42,
                "record_state": "validated"})
            changed = bytearray(content)
            changed[-1] ^= 1
            record.write_bytes(changed)
            self.assertNotEqual(self.call("ros-summary", record).returncode, 0)
            record.write_bytes(content[:-1])
            self.assertNotEqual(self.call("ros-summary", record).returncode, 0)
            link = Path(folder) / "link.record"
            link.symlink_to(record)
            self.assertNotEqual(self.call("ros-summary", link).returncode, 0)


if __name__ == "__main__":
    unittest.main()
