"""Contract tests for the proposed UHF TC/TM application PDU."""

import copy
import struct
import unittest
import zlib
from datetime import datetime, timezone

from tools.pado_packet_codec import (PacketError, decode, encode_tc, encode_tm,
                                     gnss_to_fds_properties, join_full_hk,
                                     load_catalog, split_full_hk)


STAMP = 1_800_000_000_000


class PacketCodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()

    def test_catalog_inventories_and_draft_boundary(self):
        self.assertFalse(self.catalog["operational"])
        self.assertEqual(len(self.catalog["commands"]), 58)
        self.assertEqual(len(self.catalog["telemetry"]), 18)
        for entry in self.catalog["commands"]:
            if entry["fields"] is None:
                with self.assertRaisesRegex(PacketError, "awaits subsystem ICD"):
                    encode_tc(entry["name"], 1, STAMP, {}, catalog=self.catalog)

    def test_all_defined_packet_shapes_roundtrip(self):
        for kind, entries in (("TC", self.catalog["commands"]),
                              ("TM", self.catalog["telemetry"])):
            for entry in entries:
                if entry["fields"] is None:
                    continue
                values = {name: (" " * 69 if type_name == "ascii69" else
                                 b"\x00" * 160 if type_name == "bytes160" else
                                 False if type_name == "bool" else 0)
                          for name, type_name in entry["fields"].items()}
                if entry["name"] == "aocs_set_tle":
                    values = {
                        "line1": "1 25544U 98067A   26295.91886925  .00009886  00000-0  18726-3 0  9990",
                        "line2": "2 25544  51.6334  28.0071 0006866 297.7993  62.2299 15.49005378591551"}
                if entry["name"] == "hk_dump":
                    values["end_utc_ms"] = STAMP
                if entry["name"] == "FULL_HK_CHUNK":
                    values.update(record_id=1, chunk_index=0, chunk_count=1,
                                  total_len=1, data_len=1,
                                  record_crc32=zlib.crc32(b"\x00") & 0xffffffff)
                if kind == "TC":
                    packet = encode_tc(entry["name"], 7, STAMP, values,
                                       catalog=self.catalog)
                else:
                    packet = encode_tm(entry["name"], 7, STAMP, values,
                                       catalog=self.catalog)
                self.assertLessEqual(len(packet), 200)
                parsed = decode(packet, catalog=self.catalog)
                self.assertEqual((parsed["kind"], parsed["name"], parsed["fields"]),
                                 (kind, entry["name"], values))
                self.assertFalse(parsed["operational"])

    def test_immediate_and_scheduled_tc(self):
        immediate = encode_tc("hk_get", 0, STAMP, {})
        scheduled = encode_tc("hk_get", 65535, STAMP, {},
                              execute_at_utc_ms=STAMP + 1000)
        self.assertEqual(decode(immediate)["execute_at_utc_ms"], 0)
        self.assertEqual(decode(scheduled)["execute_at_utc_ms"], STAMP + 1000)
        self.assertEqual(scheduled[18], 0x81)
        with self.assertRaises(PacketError):
            encode_tc("hk_get", 1, STAMP, {}, execute_at_utc_ms=STAMP)

    def test_hk_dump_whole_buffer_sentinel_and_explicit_interval(self):
        for start, end in ((0, 0), (STAMP - 5000, STAMP)):
            fields = {"start_utc_ms": start, "end_utc_ms": end}
            packet = encode_tc("hk_dump", 8, STAMP, fields)
            self.assertEqual(decode(packet)["fields"], fields)
            self.assertFalse(decode(packet)["operational"])
        for start, end in ((STAMP, STAMP), (STAMP, 0), (STAMP, STAMP - 1),
                           (False, 0), ("0", 0)):
            with self.assertRaises(PacketError):
                encode_tc("hk_dump", 8, STAMP,
                          {"start_utc_ms": start, "end_utc_ms": end})

    def test_frozen_cross_implementation_vector(self):
        self.assertEqual(
            encode_tc("hk_get", 7, STAMP, {}).hex(),
            "5053000110100007000001a3185c50000008800000000000000000000614f874")

    def test_packet_tampering_and_unknowns_fail_closed(self):
        packet = encode_tc("hk_get", 1, STAMP, {})
        for offset in (0, 2, 3, 4, 6, 8, 16, 18, 19, 20, len(packet)-1):
            bad = bytearray(packet)
            bad[offset] ^= 0x10
            with self.assertRaises(PacketError, msg=f"offset={offset}"):
                decode(bytes(bad))
        no_draft = bytearray(packet)
        no_draft[18] = 0
        struct.pack_into(">I", no_draft, len(packet)-4,
                         zlib.crc32(no_draft[:-4]) & 0xffffffff)
        with self.assertRaisesRegex(PacketError, "draft marker"):
            decode(bytes(no_draft))
        with self.assertRaises(PacketError):
            encode_tc("unknown", 1, STAMP, {})
        with self.assertRaises(PacketError):
            encode_tm("GNSS_PVT", 1, STAMP, {"x_m": 1})
        with self.assertRaises(PacketError):
            encode_tm("SIREN_ALERT", 1, STAMP,
                      {"alert_type": 1, "risk_score": float("nan"),
                       "anomaly_score": 0.1, "model_version": 1, "fault_code": 0})

    def test_maximal_tle_update_fits_application_budget(self):
        packet = encode_tc("aocs_set_tle", 1, STAMP,
                           {"line1": "1 25544U 98067A   26295.91886925  .00009886  00000-0  18726-3 0  9990",
                            "line2": "2 25544  51.6334  28.0071 0006866 297.7993  62.2299 15.49005378591551"})
        self.assertEqual(len(packet), 170)
        self.assertEqual(len(decode(packet)["fields"]["line1"]), 69)
        with self.assertRaisesRegex(PacketError, "checksums invalid"):
            encode_tc("aocs_set_tle", 1, STAMP,
                      {"line1": "1 25544U 98067A   26295.91886925  .00009886  00000-0  18726-3 0  9991",
                       "line2": "2 25544  51.6334  28.0071 0006866 297.7993  62.2299 15.49005378591551"})

    def test_gnss_tm_normalizes_to_existing_fds_contract(self):
        fields = {"norad_id": 25544, "solution_status": 1, "receiver_age_ms": 125,
                  "x_m": 6_800_000.0, "y_m": 0.0, "z_m": 0.0,
                  "vx_mps": 0.0, "vy_mps": 7_500.0, "vz_mps": 0.0}
        packet = encode_tm("GNSS_PVT", 45, STAMP, fields)
        decoded = decode(packet)
        output = gnss_to_fds_properties(decoded)
        self.assertIn("schema=fds.gnss-state.v1\n", output)
        self.assertIn("frame=ITRF\n", output)
        self.assertIn("position_valid=true\nvelocity_valid=true\n", output)
        self.assertIn("norad_id=25544\n", output)
        self.assertIn(datetime.fromtimestamp(STAMP / 1000, timezone.utc)
                      .strftime("epoch_utc=%Y-%m-%dT%H:%M:%SZ"), output)
        for changed in ({**fields, "solution_status": 0},
                        {**fields, "receiver_age_ms": 1001},
                        {**fields, "x_m": 100.0}):
            with self.assertRaises(PacketError):
                gnss_to_fds_properties(decode(encode_tm("GNSS_PVT", 45, STAMP, changed)))
        with self.assertRaises(PacketError):
            gnss_to_fds_properties(decode(encode_tm("GNSS_PVT", 45, STAMP + 1, fields)))

    def test_full_hk_fragmentation_and_record_integrity(self):
        data = bytes(range(256)) * 7 + bytes(range(242))  # PDR full HK: 2034 B
        packets = split_full_hk(data, 9, STAMP, 100)
        self.assertEqual(len(packets), 13)
        self.assertEqual(max(map(len, packets)), 200)
        self.assertEqual(join_full_hk(list(reversed(packets))), data)
        with self.assertRaises(PacketError):
            join_full_hk(packets[:-1])
        with self.assertRaises(PacketError):
            join_full_hk(packets[:-1] + [packets[0]])
        changed = bytearray(packets[0])
        changed[36] ^= 1
        struct.pack_into(">I", changed, len(changed)-4,
                         zlib.crc32(changed[:-4]) & 0xffffffff)
        with self.assertRaises(PacketError):
            join_full_hk([bytes(changed)] + packets[1:])

    def test_duplicate_ids_and_operational_catalog_rejected(self):
        bad = copy.deepcopy(self.catalog)
        bad["commands"][1]["id"] = bad["commands"][0]["id"]
        # Runtime _entry accepts a preloaded catalog; verify catalog loader independently
        # by writing the changed metadata to a temporary input.
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "draft.json"
            target.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(PacketError):
                load_catalog(target)
            bad = copy.deepcopy(self.catalog)
            bad["operational"] = True
            target.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(PacketError):
                load_catalog(target)


if __name__ == "__main__":
    unittest.main()
