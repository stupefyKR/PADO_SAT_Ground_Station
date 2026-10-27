"""Exact received PDUs remain durable and queryable beyond the plot window."""

import hashlib
from contextlib import closing
import json
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.sim_ground.archive import (ArchiveError, PacketArchive, archive_stats,
    delivery_receipt, list_packets, packet_detail, list_events)
from services.sim_ground.receiver import TmReceiver, load_limits
from tools.pado_packet_codec import decode, encode_tm, split_full_hk


ROOT = Path(__file__).resolve().parents[1]
STAMP = 1_800_000_000_000
EPS = {"channel_enabled_bits": 3, "latchup_bits": 0, "bus_voltage_v": 5.1,
       "temperature_c": 22.0, "uptime_s": 10}


class _Stream:
    def __init__(self, packet):
        self.raw = struct.pack(">I", len(packet)) + packet
        self.sent = []

    def recv(self, size):
        result, self.raw = self.raw[:size], self.raw[size:]
        return result

    def sendall(self, frame):
        self.sent.append(frame)


class ReceivedArchiveTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "out").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "out")
        self.root = Path(self.temp.name)
        self.path = self.root / "received_tm.sqlite3"
        self.archive = PacketArchive(self.path)

    def tearDown(self):
        self.archive.close()
        self.temp.cleanup()

    def _packet(self, seq=1, stamp=STAMP, **fields):
        return encode_tm("EPS_PDU1", seq, stamp, {**EPS, **fields})

    def test_exact_raw_parsed_fields_stable_labels_and_readonly_paging(self):
        for index in range(12):
            packet = self._packet(index, STAMP + index * 5000)
            self.archive.append(packet, decode(packet), STAMP + 60_000)
        first = list_packets(self.path, limit=5)
        self.assertEqual(first["total"], 12)
        self.assertEqual([row["id"] for row in first["items"]], [12, 11, 10, 9, 8])
        self.assertEqual(first["next_before_id"], 8)
        second = list_packets(self.path, limit=5, before_id=8, name="EPS_PDU1")
        self.assertEqual([row["id"] for row in second["items"]], [7, 6, 5, 4, 3])
        detail = packet_detail(self.path, 10)
        self.assertEqual(detail["label"], "TM0010")
        self.assertEqual(bytes.fromhex(detail["raw_hex"]), self._packet(9, STAMP + 45_000))
        self.assertAlmostEqual(detail["fields"]["bus_voltage_v"], 5.1, places=5)
        self.assertEqual(detail["header"]["kind"], "TM")
        self.assertEqual(archive_stats(self.path)["total"], 12)
        self.assertFalse(first["operational"])

    def test_restart_exact_duplicate_does_not_duplicate_archive_and_conflict_rejected(self):
        packet = self._packet()
        original = self.archive.append(packet, decode(packet), STAMP)
        self.archive.close()
        self.archive = PacketArchive(self.path)
        repeat = self.archive.append(packet, decode(packet), STAMP + 5000)
        self.assertEqual(repeat["id"], original["id"])
        self.assertTrue(repeat["duplicate"])
        conflict = self._packet(bus_voltage_v=4.9)
        with self.assertRaisesRegex(ArchiveError, "conflicting_packet"):
            self.archive.append(conflict, decode(conflict), STAMP + 5000)
        self.assertEqual(list_packets(self.path)["total"], 1)

    def test_full_hk_chunks_reassemble_parameters_and_keep_each_raw_chunk(self):
        document = {"schema": "sim.full-hk.v1", "channels": list(range(256))}
        record = json.dumps(document).encode()
        chunks = split_full_hk(record, 45, STAMP, 0)
        for packet in chunks[:-1]:
            self.archive.append(packet, decode(packet), STAMP)
        self.assertEqual(packet_detail(self.path, 1)["full_hk"]["state"], "receiving")
        self.archive.append(chunks[-1], decode(chunks[-1]), STAMP)
        detail = packet_detail(self.path, 1)
        self.assertEqual(detail["full_hk"]["state"], "complete")
        self.assertEqual(detail["full_hk"]["parameters"], document)
        self.assertEqual(detail["full_hk"]["record_sha256"], hashlib.sha256(record).hexdigest())
        self.assertEqual(detail["fields"]["data"]["encoding"], "hex")
        self.assertEqual(bytes.fromhex(detail["raw_hex"]), chunks[0])

    def test_full_archive_does_not_delete_older_packets(self):
        self.archive.close()
        self.path.unlink()
        self.archive = PacketArchive(self.path, max_bytes=32768)
        count = 0
        for index in range(500):
            packet = self._packet(index, STAMP + index * 5000)
            try:
                self.archive.append(packet, decode(packet), STAMP + 3_000_000)
                count += 1
            except ArchiveError as error:
                self.assertEqual(str(error), "archive_full")
                break
        else:
            self.fail("small archive did not fill")
        self.assertGreater(count, 0)
        self.assertEqual(list_packets(self.path)["total"], count)
        self.assertEqual(packet_detail(self.path, 1)["raw_hex"], self._packet(0).hex())
        self.assertLessEqual(self.path.stat().st_size, 32768)

    def test_query_bounds_missing_record_and_corrupted_raw_fail(self):
        for arguments in ({"limit": 0}, {"limit": 101}, {"before_id": -1},
                          {"before_id": 1 << 64}, {"name": "EPS_PDU1' OR 1=1"}):
            with self.assertRaisesRegex(ArchiveError, "invalid_query"):
                list_packets(self.path, **arguments)
        with self.assertRaisesRegex(ArchiveError, "packet_not_found"):
            packet_detail(self.path, 1)

    def test_corrupt_metadata_and_raw_do_not_become_display_values(self):
        packet = self._packet()
        self.archive.append(packet, decode(packet), STAMP)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE packets SET name='EPS_PDU2' WHERE id=1")
            db.commit()
        with self.assertRaisesRegex(ArchiveError, "archive_invalid"):
            packet_detail(self.path, 1)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE packets SET name='EPS_PDU1',raw=? WHERE id=1",
                       (packet[:-1] + bytes([packet[-1] ^ 1]),))
            db.commit()
        with self.assertRaisesRegex(ArchiveError, "archive_invalid"):
            packet_detail(self.path, 1)

    def test_event_filter_precedes_limit_so_normal_status_does_not_hide_event(self):
        for index in range(45):
            packet = encode_tm("FDIR_EVENT", index, STAMP + index * 5000, {
                "level": 2 if index == 0 else 0, "source_node": 1,
                "fault_code": 25 if index == 0 else 0,
                "action_code": 0, "recovery_state": 0})
            self.archive.append(packet, decode(packet), STAMP + 300_000)
        events = list_events(self.path, limit=1)
        self.assertEqual(len(events["items"]), 1)
        self.assertEqual(events["items"][0]["id"], 1)
        self.assertEqual(events["items"][0]["fields"]["fault_code"], 25)

    def test_chunk_reconnect_resends_receipts_without_duplicate_raw_records(self):
        receiver = TmReceiver(self.root / "test_tm_snapshot.json", self.root / "n11",
                              load_limits(ROOT / "services/sim_ground/test_limits.json"))
        receiver.contact_gate = type("Gate", (), {"active": lambda _, now: True})()
        chunks = split_full_hk(json.dumps({"channels": list(range(200))}).encode(), 73, STAMP)
        frame = lambda pdus: b"".join(struct.pack(">I", len(pdu)) + pdu for pdu in pdus)
        first = _Stream(chunks[0])
        first.raw = frame(chunks[:2])
        with patch("services.sim_ground.receiver.time.time", return_value=STAMP / 1000):
            self.assertEqual(receiver.process_connection(first), 2)
        second = _Stream(chunks[0])
        second.raw = frame(chunks)
        with patch("services.sim_ground.receiver.time.time", return_value=STAMP / 1000):
            self.assertEqual(receiver.process_connection(second), len(chunks) - 2)
        self.assertEqual(len(second.sent), len(chunks))
        self.assertEqual(list_packets(self.path)["total"], len(chunks))
        self.assertEqual(packet_detail(self.path, 1)["full_hk"]["state"], "complete")

    def test_receiver_receipt_follows_durable_commit_and_storage_error_has_no_receipt(self):
        receiver = TmReceiver(self.root / "test_tm_snapshot.json", self.root / "n11",
                              load_limits(ROOT / "services/sim_ground/test_limits.json"))
        packet = self._packet()
        stream = _Stream(packet)
        with patch("services.sim_ground.receiver.time.time", return_value=STAMP / 1000):
            self.assertEqual(receiver.process_connection(stream), 1)
        self.assertEqual(packet_detail(self.path, 1)["raw_hex"], packet.hex())
        receipt = delivery_receipt(packet)
        self.assertEqual(stream.sent, [struct.pack(">I", len(receipt)) + receipt])
        self.assertEqual(receipt, b"PSR1" + hashlib.sha256(packet).digest())

        failed_stream = _Stream(self._packet(2, STAMP + 5000))
        with patch("services.sim_ground.receiver.time.time", return_value=(STAMP + 5000) / 1000), \
                patch.object(receiver.archive, "append", side_effect=ArchiveError("archive_full")):
            with self.assertRaisesRegex(ArchiveError, "archive_full"):
                receiver.process_connection(failed_stream)
        self.assertEqual(failed_stream.sent, [])


if __name__ == "__main__":
    unittest.main()
