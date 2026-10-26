"""The virtual satellite keeps out-of-contact TM and drains it in sample order."""

import json
import struct
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from services.sim_sat.emulator import (TmSpool, flush_pending, parse_orbit_view)
from services.sim_ground.receiver import TmReceiver, load_limits
from services.webapp.tm_preview import load_snapshot
from tests.test_sim_sat_emulator import START, _view_document
from tools.pado_packet_codec import decode


class _Gate:
    def __init__(self, permitted: int = 0):
        self.permitted = permitted

    def active(self, _now_ms: int) -> bool:
        if self.permitted <= 0:
            return False
        self.permitted -= 1
        return True


class _Wire:
    def __init__(self, fail_after: int | None = None):
        self.frames: list[bytes] = []
        self.fail_after = fail_after

    def sendall(self, frame: bytes) -> None:
        if self.fail_after is not None and len(self.frames) >= self.fail_after:
            raise OSError("test link closed")
        (length,) = struct.unpack(">I", frame[:4])
        assert length == len(frame) - 4
        self.frames.append(frame[4:])


class _Stream:
    def __init__(self, packets: list[bytes]):
        self.data = b"".join(struct.pack(">I", len(packet)) + packet for packet in packets)

    def recv(self, size: int) -> bytes:
        data, self.data = self.data[:size], self.data[size:]
        return data


class SimSatStoreForwardTests(unittest.TestCase):
    def setUp(self):
        # Windows sandbox atomic replace is reliable under the workspace.
        output = Path(__file__).resolve().parents[1] / "out"
        output.mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=output)
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "tm-spool.sqlite"
        self.view = parse_orbit_view(json.dumps(_view_document()).encode())

    def _queue(self, spool: TmSpool, *offsets: int) -> None:
        for offset in offsets:
            self.assertTrue(spool.queue_tick(
                self.view, START + timedelta(seconds=offset), seed=7))

    def test_no_contact_buffers_then_pass_drains_sampled_tm_in_order(self):
        spool = TmSpool(self.path, interval_s=5)
        self.addCleanup(spool.close)
        self._queue(spool, 0, 5, 10)
        self.assertFalse(spool.queue_tick(self.view, START + timedelta(seconds=10), seed=7))
        wire = _Wire()
        self.assertEqual(flush_pending(spool, wire, _Gate(), clock_ms=lambda: 1), 0)
        self.assertEqual(spool.pending_count(), 9)
        self.assertEqual(wire.frames, [])

        self.assertEqual(flush_pending(spool, wire, _Gate(20), clock_ms=lambda: 2), 9)
        self.assertEqual(spool.pending_count(), 0)
        decoded = [decode(packet) for packet in wire.frames]
        self.assertEqual([item["name"] for item in decoded],
                         ["EPS_PDU1", "AOCS_STATE", "GNSS_PVT"] * 3)
        self.assertEqual([item["utc_ms"] for item in decoded],
                         [int((START + timedelta(seconds=offset)).timestamp()) * 1000
                          for offset in (0, 5, 10) for _ in range(3)])
        self.assertTrue(all(item["operational"] is False for item in decoded))

    def test_restart_keeps_backlog_and_los_preserves_unsent_tail(self):
        spool = TmSpool(self.path, interval_s=5)
        self._queue(spool, 0, 5, 10)
        spool.close()
        resumed = TmSpool(self.path, interval_s=5)
        self.addCleanup(resumed.close)
        self.assertEqual(resumed.pending_count(), 9)
        self.assertEqual(resumed.last_generated_s, int((START + timedelta(seconds=10)).timestamp()))
        first = _Wire()
        self.assertEqual(flush_pending(resumed, first, _Gate(4), clock_ms=lambda: 3), 4)
        self.assertEqual(resumed.pending_count(), 5)
        second = _Wire()
        self.assertEqual(flush_pending(resumed, second, _Gate(20), clock_ms=lambda: 4), 5)
        self.assertEqual(resumed.pending_count(), 0)
        self.assertEqual([decode(packet)["name"] for packet in first.frames + second.frames],
                         ["EPS_PDU1", "AOCS_STATE", "GNSS_PVT"] * 3)

    def test_send_failure_retains_failed_packet_and_capacity_is_explicit(self):
        spool = TmSpool(self.path, interval_s=5, max_ticks=2)
        self.addCleanup(spool.close)
        self._queue(spool, 0, 5, 10)
        self.assertEqual(spool.pending_count(), 6)
        wire = _Wire(fail_after=2)
        with self.assertRaises(OSError):
            flush_pending(spool, wire, _Gate(20), clock_ms=lambda: 5)
        self.assertEqual(spool.pending_count(), 4)
        self.assertEqual(decode(spool.iter_pending()[0][1])["utc_ms"],
                         int((START + timedelta(seconds=5)).timestamp()) * 1000)

    def test_backlog_reaches_ground_as_old_measurements_during_pass(self):
        spool = TmSpool(self.path, interval_s=5)
        self.addCleanup(spool.close)
        self._queue(spool, 0, 5, 10)
        wire = _Wire()
        self.assertEqual(flush_pending(spool, wire, _Gate(20), clock_ms=lambda: 1), 9)
        root = self.path.parent
        truth = _view_document()
        utc = lambda seconds: (START + timedelta(seconds=seconds)).isoformat().replace(
            "+00:00", "Z")
        truth["stations"] = [{"id": "yonsei", "band": "UHF", "passes": [{
            "aos_utc": utc(240), "los_utc": utc(360)}]}]
        contact = root / "truth.json"
        contact.write_text(json.dumps(truth), encoding="utf-8")
        snapshot = root / "test_tm_snapshot.json"
        inbox = root / "n11"
        limits = Path(__file__).resolve().parents[1] / "services/sim_ground/test_limits.json"
        receiver = TmReceiver(snapshot, inbox, load_limits(limits), contact)
        received = START + timedelta(seconds=300)
        with patch("services.sim_ground.receiver.time.time", return_value=received.timestamp()):
            self.assertEqual(receiver.process_connection(_Stream(wire.frames)), 9)
        preview = load_snapshot(snapshot)
        self.assertEqual(preview["received_utc"], utc(300))
        self.assertEqual(len(preview["sensors"][0]["samples"]), 3)
        self.assertEqual(preview["sensors"][0]["samples"][0]["utc"], utc(0))
        self.assertEqual(preview["attitude"]["utc"], utc(10))
        self.assertEqual(list(inbox.glob("*.manifest.json")), [])


if __name__ == "__main__":
    unittest.main()
