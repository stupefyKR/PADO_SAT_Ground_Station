"""The test satellite emits bounded, decodable PDU bytes from one FDS truth view."""

import json
import math
import struct
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from services.sim_sat.emulator import (VIRTUAL_NORAD_ID, generate_packet_batch,
                                       parse_orbit_view, send_packet_batch)
from tools.pado_packet_codec import decode


START = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
RADIUS = 6_878_000.0
RATE = 0.0011
INCLINATION = math.radians(97.4)


def _position(elapsed_s: float) -> tuple[float, float, float]:
    angle = RATE * elapsed_s
    return (RADIUS * math.cos(angle),
            RADIUS * math.sin(angle) * math.cos(INCLINATION),
            RADIUS * math.sin(angle) * math.sin(INCLINATION))


def _sample(stamp: datetime, elapsed_s: int) -> list:
    return [stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            *_position(elapsed_s), 0.0, 0.0, 500000.0, 1.0, 0.0, 0.0]


def _view_document() -> dict:
    return {"schema": "fds.orbit-view.v1",
            "quality": "PRELIMINARY_NOT_FOR_OPERATIONS",
            "source": "TLE", "norad_id": VIRTUAL_NORAD_ID,
            "input_epoch_utc": START.isoformat().replace("+00:00", "Z"),
            "start_utc": START.isoformat().replace("+00:00", "Z"),
            "end_utc": (START + timedelta(minutes=9)).isoformat().replace("+00:00", "Z"),
            "sample_step_s": 60,
            "samples": [_sample(START + timedelta(seconds=offset), offset)
                        for offset in range(0, 541, 60)]}


class SimulatorEmulatorTests(unittest.TestCase):
    def setUp(self):
        self.view = parse_orbit_view(json.dumps(_view_document()).encode())

    def test_three_real_draft_pdus_share_sample_time_and_roll_sequence(self):
        now = START + timedelta(seconds=155)
        packets, next_sequence = generate_packet_batch(self.view, now, 65534, seed=7)
        self.assertEqual([decode(packet)["name"] for packet in packets],
                         ["EPS_PDU1", "AOCS_STATE", "GNSS_PVT"])
        self.assertEqual([decode(packet)["sequence"] for packet in packets],
                         [65534, 65535, 0])
        self.assertEqual(next_sequence, 1)
        self.assertTrue(all(decode(packet)["utc_ms"] == int(now.timestamp()) * 1000
                            for packet in packets))
        self.assertTrue(all(not decode(packet)["operational"] and len(packet) <= 200
                            for packet in packets))
        self.assertEqual(generate_packet_batch(self.view, now, 65534, seed=7)[0], packets)

    def test_gnss_uses_interpolated_fds_ecef_and_attitude_points_nadir(self):
        now = START + timedelta(seconds=155)
        packets, _ = generate_packet_batch(self.view, now, 1)
        gnss = decode(packets[2])["fields"]
        self.assertEqual(gnss["norad_id"], VIRTUAL_NORAD_ID)
        self.assertEqual(gnss["solution_status"], 1)
        self.assertEqual(gnss["receiver_age_ms"], 0)
        expected = _position(155)
        position = tuple(gnss[f"{axis}_m"] for axis in "xyz")
        velocity = tuple(gnss[f"v{axis}_mps"] for axis in "xyz")
        self.assertLess(math.dist(position, expected), 1000)
        self.assertTrue(100 < math.hypot(*velocity) < 15000)
        attitude = decode(packets[1])["fields"]
        quaternion = tuple(attitude[f"q_{axis}"] for axis in "wxyz")
        self.assertAlmostEqual(math.hypot(*quaternion), 1, places=6)
        # Rotate body +Z through q. The result must point toward Earth center.
        w, x, y, z = quaternion
        rotated_z = (2 * (x * z + w * y), 2 * (y * z - w * x),
                     1 - 2 * (x * x + y * y))
        radial = tuple(component / math.hypot(*position) for component in position)
        self.assertLess(math.dist(rotated_z, tuple(-component for component in radial)),
                        1e-5)

    def test_body_rates_follow_the_nadir_quaternion_rotation(self):
        # In this fixed-plane prograde orbit, +Z faces nadir and +X follows
        # velocity, so the body rotates about -Y relative to ITRF.
        for seconds in (0, 155, 540):
            with self.subTest(seconds=seconds):
                packets, _ = generate_packet_batch(
                    self.view, START + timedelta(seconds=seconds), 1)
                fields = decode(packets[1])["fields"]
                self.assertAlmostEqual(fields["body_rate_x_rad_s"], 0.0,
                                       delta=2e-5)
                self.assertAlmostEqual(fields["body_rate_y_rad_s"], -RATE,
                                       delta=2e-5)
                self.assertAlmostEqual(fields["body_rate_z_rad_s"], 0.0,
                                       delta=2e-5)

    def test_smooth_sensor_values_and_bounded_wire_frames(self):
        before, _ = generate_packet_batch(self.view, START + timedelta(seconds=150), 0)
        after, _ = generate_packet_batch(self.view, START + timedelta(seconds=155), 3)
        bus_before = decode(before[0])["fields"]["bus_voltage_v"]
        bus_after = decode(after[0])["fields"]["bus_voltage_v"]
        self.assertLess(abs(bus_after - bus_before), 0.1)
        self.assertTrue(4.6 < bus_before < 5.7)
        class WireCapture:
            def __init__(self):
                self.data = bytearray()

            def sendall(self, data: bytes) -> None:
                self.data.extend(data)

        wire = WireCapture()
        send_packet_batch(wire, after)
        received = []
        offset = 0
        for _ in after:
            size = struct.unpack_from(">I", wire.data, offset)[0]
            self.assertTrue(24 <= size <= 200)
            offset += 4
            received.append(bytes(wire.data[offset:offset + size]))
            offset += size
        self.assertEqual(offset, len(wire.data))
        self.assertEqual(tuple(received), after)

    def test_a_five_minute_test_window_includes_an_overvoltage(self):
        voltage = []
        for offset in range(0, 301, 5):
            packets, _ = generate_packet_batch(self.view, START + timedelta(seconds=offset),
                                               (3 * offset // 5) & 0xffff)
            voltage.append(decode(packets[0])["fields"]["bus_voltage_v"])
        self.assertGreater(max(voltage), 5.5)
        self.assertGreater(min(voltage), 4.5)

    def test_wrong_or_stale_truth_is_rejected_before_tm(self):
        for field, value in (("norad_id", 25544), ("source", "GNSS"),
                             ("quality", "OPERATIONAL")):
            document = _view_document()
            document[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_orbit_view(json.dumps(document).encode())
        with self.assertRaises(ValueError):
            generate_packet_batch(self.view, START - timedelta(seconds=1), 0)
        with self.assertRaises(ValueError):
            generate_packet_batch(self.view, START + timedelta(minutes=10), 0)
        old = replace(self.view, input_epoch_s=self.view.start_s - 72 * 3600)
        with self.assertRaises(ValueError):
            generate_packet_batch(old, START, 0)
        with self.assertRaises(ValueError):
            parse_orbit_view(b'{"schema":"fds.orbit-view.v1","schema":"duplicate"}')

    def test_fractional_tle_epoch_matches_real_fds_view_format(self):
        document = _view_document()
        document["input_epoch_utc"] = "2026-10-23T11:59:59.303Z"
        view = parse_orbit_view(json.dumps(document).encode())
        packets, _ = generate_packet_batch(view, START + timedelta(seconds=10), 12)
        self.assertEqual(decode(packets[0])["fields"]["uptime_s"], 12 * 3600 + 10)

    def test_uptime_remains_monotone_across_tle_refresh(self):
        before, _ = generate_packet_batch(self.view, START + timedelta(seconds=155), 12)
        refreshed = _view_document()
        refreshed["input_epoch_utc"] = (START + timedelta(seconds=120)).isoformat().replace(
            "+00:00", "Z")
        refreshed["start_utc"] = refreshed["input_epoch_utc"]
        refreshed["samples"] = refreshed["samples"][2:]
        new_view = parse_orbit_view(json.dumps(refreshed).encode())
        after, _ = generate_packet_batch(new_view, START + timedelta(seconds=160), 15)
        earlier_uptime = decode(before[0])["fields"]["uptime_s"]
        later_uptime = decode(after[0])["fields"]["uptime_s"]
        self.assertEqual(later_uptime - earlier_uptime, 5)


if __name__ == "__main__":
    unittest.main()
