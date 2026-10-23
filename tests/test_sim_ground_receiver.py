"""Synthetic TM ingress validates wire frames and updates ROS/FDS test contracts."""

import json
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from services.sim_ground.receiver import (
    ATTITUDE_FRAME,
    ReceiverError,
    TmReceiver,
    _PendingTc,
    load_limits,
    prune_simulator_n11,
    read_framed_pdu,
    send_framed_pdu,
)
from services.webapp.tm_preview import load_snapshot
from tools.pado_packet_codec import PacketError, decode, encode_tc, encode_tm
from services.sim_sat.emulator import build_command_result, TcReplayState
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

    def test_ros_graph_keeps_sixty_minutes_of_five_second_samples(self):
        def utc(milliseconds):
            return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")

        limits = load_limits(LIMITS)
        limits["sensors"]["AOCS_STATE.control_mode"] = {
            "id": "sim_control_mode", "name": "가상 제어 모드", "group": "AOCS",
            "unit": "mode", "lower": 0, "upper": 4,
        }
        self.receiver = TmReceiver(self.snapshot, self.inbox, limits)
        sequence = 0
        for index in range(721):
            stamp = STAMP + index * 5000
            self.receiver.ingest(self._eps(sequence, stamp), received_ms=stamp)
            sequence += 1
            if index % 12 == 0:  # One attitude sample per minute across the hour.
                self.receiver.ingest(self._aocs(sequence, stamp), received_ms=stamp)
                sequence += 1
        preview = load_snapshot(self.snapshot)
        self.assertLessEqual(self.snapshot.stat().st_size, 1024 * 1024)
        for sensor in preview["sensors"]:
            self.assertEqual(len(sensor["samples"]),
                             61 if sensor["id"] == "sim_control_mode" else 721)
            self.assertEqual(sensor["samples"][0]["utc"],
                             utc(STAMP))
            self.assertEqual(sensor["samples"][-1]["utc"],
                             utc(STAMP + 3_600_000))
        self.assertEqual(preview["attitude"]["utc"], utc(STAMP + 3_600_000))
        self.assertEqual(preview["attitude"]["frame"], ATTITUDE_FRAME)

        self.receiver.ingest(self._eps(sequence, STAMP + 721 * 5000),
                             received_ms=STAMP + 721 * 5000)
        preview = load_snapshot(self.snapshot)
        self.assertEqual(len(preview["sensors"][0]["samples"]), 721)
        self.assertEqual(preview["sensors"][0]["samples"][0]["utc"],
                         utc(STAMP + 5000))
        self.assertLessEqual(len(self.receiver.sensor_history["EPS_PDU1"]), 721)

    def test_ros_graph_expires_old_samples_and_restarts_with_new_history(self):
        self.receiver.ingest(self._eps(0, STAMP), received_ms=STAMP)
        self.receiver.ingest(self._eps(1, STAMP + 5000),
                             received_ms=STAMP + 5000)
        after_gap = STAMP + 3_600_000 + 10_000
        self.receiver.ingest(self._eps(2, after_gap), received_ms=after_gap)
        preview = load_snapshot(self.snapshot)
        self.assertEqual(len(preview["sensors"][0]["samples"]), 1)

        restarted = TmReceiver(self.snapshot, self.inbox, load_limits(LIMITS))
        restarted.ingest(self._eps(0, after_gap + 5000),
                         received_ms=after_gap + 5000)
        preview = load_snapshot(self.snapshot)
        self.assertEqual(len(preview["sensors"][0]["samples"]), 1)
        self.assertEqual(len(restarted.sensor_history["EPS_PDU1"]), 1)

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

    @unittest.skipIf(os.name == "nt", "Windows sandbox blocks socket.socketpair")
    def test_duplex_tc_roundtrip_and_ack_correlation(self):
        satellite, ground = socket.socketpair()
        satellite.settimeout(3)
        ground.settimeout(3)
        stream_errors = []
        def process():
            try:
                self.receiver.process_connection(ground)
            except Exception as error:
                stream_errors.append(error)
            finally:
                ground.close()
        worker = threading.Thread(target=process, daemon=True)
        worker.start()
        try:
            for _ in range(100):
                if self.receiver._satellite_connected:
                    break
                time.sleep(0.01)
            self.assertTrue(self.receiver._satellite_connected)
            stamp = int(time.time()) * 1000
            tc = encode_tc("hk_get", 43, stamp, {})
            result = {}
            def submit():
                try:
                    result["packet"] = self.receiver.submit_tc(tc, timeout_s=3)
                except Exception as error:
                    result["error"] = error
            submitter = threading.Thread(target=submit, daemon=True)
            submitter.start()
            self.assertEqual(read_framed_pdu(satellite), tc)
            ack = build_command_result(tc, 500, TcReplayState(),
                                       received_ms=int(time.time() * 1000))
            send_framed_pdu(satellite, ack)
            submitter.join(timeout=3)
            self.assertFalse(submitter.is_alive())
            self.assertNotIn("error", result)
            self.assertEqual(result["packet"], ack)
            with self.assertRaises(ReceiverError):
                self.receiver.submit_tc(tc, timeout_s=0.1)
        finally:
            satellite.close()
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(stream_errors, [])

    def test_tc_bridge_rejects_bad_crc_scheduled_and_stale_before_uplink(self):
        stamp = int(time.time()) * 1000
        tc = encode_tc("hk_get", 1, stamp, {})
        corrupted = bytearray(tc)
        corrupted[-1] ^= 1
        with self.assertRaises(PacketError):
            self.receiver.submit_tc(bytes(corrupted), now_ms=stamp)
        scheduled = encode_tc("hk_get", 1, stamp, {},
                              execute_at_utc_ms=stamp + 5000)
        with self.assertRaises(ReceiverError):
            self.receiver.submit_tc(scheduled, now_ms=stamp)
        with self.assertRaises(ReceiverError):
            self.receiver.submit_tc(tc, now_ms=stamp + 121_000)

    def test_command_result_requires_matching_pending_id_sequence_and_status(self):
        stamp = int(time.time()) * 1000
        tc = encode_tc("hk_get", 33, stamp, {})
        decoded = decode(tc)
        request = _PendingTc(tc, decoded["id"], decoded["sequence"])
        request.sent = True
        self.receiver._tc_pending = request
        wrong_command = encode_tc("mm_get_mode", 33, stamp, {})
        ack = build_command_result(wrong_command, 50, TcReplayState(),
                                   received_ms=stamp)
        with self.assertRaisesRegex(ReceiverError, "unmatched"):
            self.receiver._ingest_command_result(ack, decode(ack), received_ms=stamp)
        self.assertIs(self.receiver._tc_pending, request)
        good_ack = build_command_result(tc, 50, TcReplayState(), received_ms=stamp)
        self.receiver._ingest_command_result(good_ack, decode(good_ack),
                                             received_ms=stamp)
        self.assertEqual(request.response, good_ack)
        self.assertTrue(request.done.is_set())

    @unittest.skipIf(os.name == "nt", "Unix bridge exercised by Linux CI")
    def test_private_unix_tc_bridge_roundtrip(self):
        satellite, ground = socket.socketpair()
        satellite.settimeout(4)
        ground.settimeout(4)
        stop = threading.Event()
        with tempfile.TemporaryDirectory(prefix="pado-sim-tc-") as folder:
            path = Path(folder) / "bridge.sock"
            tm_thread = threading.Thread(target=self.receiver.process_connection,
                                         args=(ground,), daemon=True)
            bridge_thread = threading.Thread(target=self.receiver.serve_tc_socket,
                                             args=(path,), kwargs={"stop": stop},
                                             daemon=True)
            tm_thread.start()
            bridge_thread.start()
            try:
                for _ in range(200):
                    if path.is_socket() and self.receiver._satellite_connected:
                        break
                    time.sleep(0.01)
                self.assertTrue(path.is_socket())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                issued_ms = int(time.time() * 1000)
                tc = encode_tc("mm_get_mode", 41, issued_ms, {})
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(4)
                    client.connect(str(path))
                    send_framed_pdu(client, tc)
                    self.assertEqual(read_framed_pdu(satellite), tc)
                    ack = build_command_result(tc, 700, TcReplayState(),
                                               received_ms=int(time.time() * 1000))
                    send_framed_pdu(satellite, ack)
                    self.assertEqual(read_framed_pdu(client), ack)
            finally:
                stop.set()
                satellite.close()
                tm_thread.join(timeout=3)
                bridge_thread.join(timeout=3)
                ground.close()
            self.assertFalse(tm_thread.is_alive())
            self.assertFalse(bridge_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
