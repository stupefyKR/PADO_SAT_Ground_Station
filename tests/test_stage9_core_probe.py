"""Read-only Stage 9 bridge tests against the actual C parser and TM validator."""

import json
import os
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path


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
