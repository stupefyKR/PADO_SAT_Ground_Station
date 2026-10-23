"""Read-only Stage 9 bridge tests against the actual C parser and TM validator."""

import json
import hashlib
import os
import socket
import struct
import subprocess
import tempfile
import time
import unittest
import zlib
from pathlib import Path

from services.webapp.core import CoreUnavailable, mps_catalog, mps_passes, ros_summary
from tools.publish_mps_schedule import publish


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

    def start_service(self, mode, socket_path, source, schedule_root=None):
        process = subprocess.Popen(
            [str(CORE_BIN), "serve", mode, str(socket_path), str(source)] +
            ([str(schedule_root)] if schedule_root is not None else []),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        until = time.monotonic() + 3
        while time.monotonic() < until:
            if socket_path.exists():
                return process
            if process.poll() is not None:
                break
            time.sleep(0.02)
        if process.poll() is None:
            process.terminate()
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
                ros.stderr.close()
                mps.stderr.close()
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

    def test_validated_n13_schedule_is_reloaded_and_tampering_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "commands.json"
            source.write_bytes((ROOT / "interfaces/command_db/commands.json").read_bytes())
            schedule_root = root / "schedules"
            schedule_root.mkdir()
            run = root / "fds-20261023T100000Z-1234abcd"
            run.mkdir()
            body = (
                "schema,station_id,band,pass_id,aos_utc,los_utc,peak_utc,"
                "max_elevation_deg,minimum_elevation_deg,clipped_start,clipped_end\n"
                "fds.contact-schedule.v1,yonsei,UHF,yonsei-001,2026-10-23T10:00:00.125Z,"
                "2026-10-23T10:08:00.250Z,2026-10-23T10:04:00.000Z,42.5,10.0,false,false\n"
            ).encode("ascii")
            (run / "contact_schedule.csv").write_bytes(body)
            (run / "contact_schedule.csv.manifest.json").write_text(json.dumps({
                "file": "contact_schedule.csv", "type": "CONTACT_SCHEDULE",
                "from": "fds", "to": "mps", "version": 1,
                "created": "2026-10-23T10:00:00Z",
                "sha256": hashlib.sha256(body).hexdigest(),
            }), encoding="ascii")
            socket_path = root / "mps.sock"
            process = self.start_service("mps", socket_path, source, schedule_root)
            try:
                self.assertEqual(mps_passes(socket_path)["schedule_state"], "absent")
                published = publish(run, schedule_root)
                result = mps_passes(socket_path)
                self.assertEqual(result["schedule_state"], "validated")
                self.assertEqual(result["schedule_sha256"], hashlib.sha256(body).hexdigest())
                self.assertEqual(result["passes"][0]["station_id"], "yonsei")
                self.assertFalse(result["operational"])
                (published / "contact_schedule.csv").write_bytes(b"tampered")
                with self.assertRaises(CoreUnavailable):
                    mps_passes(socket_path)
                (schedule_root / "current.txt").write_text("../escape\n", encoding="ascii")
                with self.assertRaises(CoreUnavailable):
                    mps_passes(socket_path)
            finally:
                process.terminate()
                self.assertEqual(process.wait(timeout=3), 0)
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
