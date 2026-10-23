"""Synthetic TM ingress validates wire frames and updates ROS/FDS test contracts."""

import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.sim_ground.receiver import (
    ATTITUDE_FRAME,
    ReceiverError,
    TmReceiver,
    load_limits,
    prune_simulator_n11,
    read_framed_pdu,
)
from services.webapp.tm_preview import load_snapshot
from tools.pado_packet_codec import PacketError, encode_tc, encode_tm
from tools.run_fds_pipeline import verified_pair


STAMP = 1_800_000_000_000
LIMITS = Path(__file__).resolve().parents[1] / "services/sim_ground/test_limits.json"
EPS = {"channel_enabled_bits": 3, "latchup_bits": 0,
       "bus_voltage_v": 5.1, "temperature_c": 22.0, "uptime_s": 10}
AOCS = {"adcs_state": 1, "control_mode": 1, "boot_index": 1,
        "sram_latchup_count": 0, "edac_error_count": 0,
        "q_w": 1.0, "q_x": 0.0, "q_y": 0.0, "q_z": 0.0,
        "body_rate_x_rad_s": 0.0, "body_rate_y_rad_s": 0.0,
        "body_rate_z_rad_s": 0.0}
GNSS = {"norad_id": 99999, "solution_status": 1, "receiver_age_ms": 0,
        "x_m": 6_878_000.0, "y_m": 0.0, "z_m": 0.0,
        "vx_mps": 0.0, "vy_mps": 7_600.0, "vz_mps": 0.0}


class _SocketBytes:
    """Tiny socket double that deliberately splits reads across recv calls."""

    def __init__(self, raw: bytes, chunk: int = 3):
        self.raw = raw
        self.chunk = chunk

    def recv(self, size: int) -> bytes:
        data = self.raw[:min(size, self.chunk)]
        self.raw = self.raw[len(data):]
        return data


class SyntheticTmReceiverTests(unittest.TestCase):
    def setUp(self):
        out = Path(__file__).resolve().parents[1] / "out"
        out.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=out)
        root = Path(self.temp.name)
        self.snapshot = root / "test_tm_snapshot.json"
        self.inbox = root / "n11"
        self.receiver = TmReceiver(self.snapshot, self.inbox, load_limits(LIMITS))

    def tearDown(self):
        self.temp.cleanup()

    def _eps(self, seq=0, stamp=STAMP, **fields):
        return encode_tm("EPS_PDU1", seq, stamp, {**EPS, **fields})

    def _aocs(self, seq=1, stamp=STAMP, **fields):
        return encode_tm("AOCS_STATE", seq, stamp, {**AOCS, **fields})

    def _gnss(self, seq=2, stamp=STAMP, **fields):
        return encode_tm("GNSS_PVT", seq, stamp, {**GNSS, **fields})

    def test_framing_handles_partial_reads_and_rejects_truncation(self):
        pdu = self._eps()
        framed = struct.pack(">I", len(pdu)) + pdu
        self.assertEqual(read_framed_pdu(_SocketBytes(framed)), pdu)
        self.assertIsNone(read_framed_pdu(_SocketBytes(b"")))
        for bad in (struct.pack(">I", 23),
                    struct.pack(">I", 201),
                    struct.pack(">I", len(pdu)) + pdu[:-1],
                    b"\x00\x00"):
            with self.subTest(raw=bad[:8]), self.assertRaises(ReceiverError):
                read_framed_pdu(_SocketBytes(bad))

    def test_one_tcp_stream_decodes_all_three_pdus(self):
        packets = (self._eps(), self._aocs(), self._gnss())
        stream = b"".join(struct.pack(">I", len(pdu)) + pdu for pdu in packets)
        with patch("services.sim_ground.receiver.time.time", return_value=STAMP / 1000):
            accepted = self.receiver.process_connection(_SocketBytes(stream, chunk=7))
        self.assertEqual(accepted, 3)
        self.assertEqual(load_snapshot(self.snapshot)["attitude"]["frame"],
                         ATTITUDE_FRAME)
        self.assertEqual(len(list(self.inbox.glob("*.manifest.json"))), 1)

    def test_ros_snapshot_attitude_and_throttled_n11_pair(self):
        self.receiver.ingest(self._eps(), received_ms=STAMP)
        preview = load_snapshot(self.snapshot)
        self.assertTrue(preview["non_operational"])
        self.assertEqual(len(preview["sensors"]), 2)
        self.assertIsNone(preview["attitude"])

        self.receiver.ingest(self._aocs(), received_ms=STAMP)
        self.receiver.ingest(self._gnss(), received_ms=STAMP)
        preview = load_snapshot(self.snapshot)
        self.assertEqual(preview["attitude"]["frame"], ATTITUDE_FRAME)
        self.assertEqual(preview["attitude"]["quaternion_wxyz"],
                         [1.0, 0.0, 0.0, 0.0])
        manifests = list(self.inbox.glob("*.manifest.json"))
        self.assertEqual(len(manifests), 1)
        manifest = json.loads(manifests[0].read_text())
        self.assertEqual(manifest["type"], "GNSS")
        body = verified_pair(self.inbox / manifest["file"], manifests[0], "gnss")
        self.assertIn(b"norad_id=99999\n", body)

        self.receiver.ingest(self._eps(3, STAMP + 5000,
                                       bus_voltage_v=5.65), received_ms=STAMP + 5000)
        self.receiver.ingest(self._aocs(4, STAMP + 5000),
                             received_ms=STAMP + 5000)
        self.receiver.ingest(self._gnss(5, STAMP + 5000),
                             received_ms=STAMP + 5000)
        self.assertEqual(len(list(self.inbox.glob("*.manifest.json"))), 1)
        preview = load_snapshot(self.snapshot)
        voltage = next(s for s in preview["sensors"] if s["id"] == "sim_bus_voltage")
        self.assertGreater(voltage["samples"][-1]["value"], voltage["upper"])

        self.receiver.ingest(self._eps(180, STAMP + 300_000),
                             received_ms=STAMP + 300_000)
        self.receiver.ingest(self._aocs(181, STAMP + 300_000),
                             received_ms=STAMP + 300_000)
        self.receiver.ingest(self._gnss(182, STAMP + 300_000),
                             received_ms=STAMP + 300_000)
        self.assertEqual(len(list(self.inbox.glob("*.manifest.json"))), 2)

    def test_replay_wrong_identity_bad_crc_and_tc_are_rejected(self):
        first = self._eps(65535)
        self.receiver.ingest(first, received_ms=STAMP)
        with self.assertRaises(ReceiverError):
            self.receiver.ingest(first, received_ms=STAMP)
        self.receiver.ingest(self._aocs(0), received_ms=STAMP)  # u16 wrap.
        with self.assertRaises(ReceiverError):
            self.receiver.ingest(self._gnss(1, norad_id=25544),
                                 received_ms=STAMP)
        with self.assertRaises(ReceiverError):
            self.receiver.ingest(self._gnss(1, stamp=STAMP - 121_000),
                                 received_ms=STAMP)
        with self.assertRaises(PacketError):
            self.receiver.ingest(self._gnss(1, solution_status=0),
                                 received_ms=STAMP)
        tampered = bytearray(self._gnss(1))
        tampered[25] ^= 1
        with self.assertRaises(PacketError):
            self.receiver.ingest(bytes(tampered), received_ms=STAMP)
        with self.assertRaises(ReceiverError):
            self.receiver.ingest(encode_tc("mm_get_mode", 1, STAMP, {}),
                                 received_ms=STAMP)
        self.receiver.ingest(self._gnss(1), received_ms=STAMP)

    def test_invalid_attitude_does_not_poison_following_batch(self):
        self.receiver.ingest(self._eps(), received_ms=STAMP)
        with self.assertRaises(ReceiverError):
            self.receiver.ingest(self._aocs(q_w=0.5), received_ms=STAMP)
        self.receiver.ingest(self._aocs(), received_ms=STAMP)
        self.assertEqual(load_snapshot(self.snapshot)["attitude"]["frame"],
                         ATTITUDE_FRAME)

    def test_n11_retention_removes_only_older_verified_simulator_pairs(self):
        self.inbox.mkdir()
        unrelated = self.inbox / "draft-gnss-not-a-digest.properties.manifest.json"
        unrelated.write_text("leave me", encoding="ascii")
        with patch("services.sim_ground.receiver.MAX_N11_PAIRS", 2):
            for index in range(3):
                stamp = STAMP + index * 300_000
                self.receiver.ingest(self._gnss(index, stamp), received_ms=stamp)
        manifests = list(self.inbox.glob("draft-gnss-*.properties.manifest.json"))
        complete = [path for path in manifests if path != unrelated]
        self.assertEqual(len(complete), 2)
        self.assertTrue(unrelated.exists())
        self.assertEqual(len(list(self.inbox.glob("draft-gnss-*.properties"))), 2)
        latest = max(complete, key=lambda path: path.stat().st_mtime_ns)
        self.assertIn(b"epoch_utc=2027-01-15T08:10:00Z",
                      verified_pair(self.inbox / latest.name.removesuffix(".manifest.json"),
                                    latest, "gnss"))

    def test_n11_retention_never_deletes_new_bridge_even_if_mtime_is_old(self):
        for index in range(3):
            stamp = STAMP + index * 300_000
            self.receiver.ingest(self._gnss(index, stamp), received_ms=stamp)
        manifests = list(self.inbox.glob("*.manifest.json"))
        self.assertEqual(len(manifests), 3)
        newest = max(manifests, key=lambda path: path.stat().st_mtime_ns)
        os.utime(newest, ns=(1, 1))
        self.assertEqual(prune_simulator_n11(self.inbox, newest, keep=2), 1)
        self.assertTrue(newest.exists())
        self.assertTrue((self.inbox / newest.name.removesuffix(".manifest.json")).exists())
        self.assertEqual(len(list(self.inbox.glob("*.manifest.json"))), 2)


if __name__ == "__main__":
    unittest.main()
