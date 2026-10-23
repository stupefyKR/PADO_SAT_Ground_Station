"""Emit draft PADO TM packets from the virtual satellite's FDS orbit view.

This is a TEST_ONLY application-PDU source, not a spacecraft/RF protocol. The
view already contains the FDS TLE propagation; ECEF velocity is approximated
by differentiating four neighboring 60-second ECEF position samples. No second
orbit propagator runs here. A private TCP receiver gets repeated
``uint32_be packet_length + draft_PDU`` records, with no TC listener or route.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import socket
import struct
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from tools.pado_packet_codec import PacketError, encode_tm


MAX_VIEW_BYTES = 4 * 1024 * 1024
MAX_SAMPLES = 5000
VIRTUAL_NORAD_ID = 99999
DEFAULT_RECEIVER_PORT = 9200
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SIMULATED_BOOT_S = int((datetime(2026, 10, 23, tzinfo=timezone.utc) - _EPOCH).total_seconds())


@dataclass(frozen=True)
class OrbitSample:
    utc_s: int
    ecef_m: tuple[float, float, float]


@dataclass(frozen=True)
class OrbitView:
    norad_id: int
    input_epoch_s: float
    start_s: int
    end_s: int
    samples: tuple[OrbitSample, ...]
    times_s: tuple[int, ...]


def _unique_pairs(items: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate FDS view key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite FDS view value: {value}")


def _utc_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("FDS view timestamp must be UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("invalid FDS view timestamp") from error
    return parsed


def _utc_s(value: object) -> int:
    parsed = _utc_datetime(value)
    if parsed.microsecond != 0:
        raise ValueError("FDS view samples must use whole UTC seconds")
    return int((parsed - _EPOCH).total_seconds())


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def parse_orbit_view(raw: bytes) -> OrbitView:
    """Validate the published virtual TLE view before deriving fake GNSS TM."""
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_VIEW_BYTES:
        raise ValueError("FDS view size is invalid")
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                              parse_constant=_reject_constant)
    except (UnicodeError, ValueError) as error:
        raise ValueError("FDS view JSON is invalid") from error
    if (not isinstance(document, dict) or
            document.get("schema") != "fds.orbit-view.v1" or
            document.get("quality") != "PRELIMINARY_NOT_FOR_OPERATIONS" or
            document.get("source") != "TLE" or
            type(document.get("norad_id")) is not int or
            document["norad_id"] != VIRTUAL_NORAD_ID or
            not isinstance(document.get("samples"), list) or
            not 4 <= len(document["samples"]) <= MAX_SAMPLES):
        raise ValueError("only the virtual TEST_ONLY TLE view is accepted")
    # A TLE epoch normally has fractional seconds; FDS sample ticks do not.
    epoch = (_utc_datetime(document.get("input_epoch_utc")) - _EPOCH).total_seconds()
    start = _utc_s(document.get("start_utc"))
    end = _utc_s(document.get("end_utc"))
    if not epoch <= start < end or end - start > 72 * 3600:
        raise ValueError("virtual view epoch or span is invalid")
    step = document.get("sample_step_s")
    if type(step) is not int or not 1 <= step <= 120:
        raise ValueError("virtual view sample step is invalid")
    samples = []
    previous = None
    for row in document["samples"]:
        if (not isinstance(row, list) or len(row) != 10 or
                any(not _number(number) for number in row[1:])):
            raise ValueError("virtual view sample is invalid")
        stamp = _utc_s(row[0])
        xyz = (float(row[1]), float(row[2]), float(row[3]))
        radius = math.hypot(*xyz)
        if (not 6.3e6 <= radius <= 8.0e6 or
                (previous is not None and not 0 < stamp - previous <= step)):
            raise ValueError("virtual view ECEF position or cadence is invalid")
        samples.append(OrbitSample(stamp, xyz))
        previous = stamp
    if samples[0].utc_s != start or samples[-1].utc_s != end:
        raise ValueError("virtual view sample coverage is invalid")
    return OrbitView(VIRTUAL_NORAD_ID, epoch, start, end, tuple(samples),
                     tuple(item.utc_s for item in samples))


def load_orbit_view(path: Path) -> OrbitView:
    if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= MAX_VIEW_BYTES:
        raise ValueError("bounded regular virtual FDS view required")
    return parse_orbit_view(path.read_bytes())


def _interpolated_ecef(view: OrbitView, utc_s: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Cubic Lagrange position and its derivative; input is FDS ECEF samples."""
    if not view.start_s <= utc_s <= view.end_s:
        raise ValueError("current time is outside virtual FDS view")
    first = max(0, min(len(view.samples) - 4,
                       bisect.bisect_left(view.times_s, utc_s) - 2))
    knots = view.samples[first:first + 4]
    base = knots[0].utc_s
    t = utc_s - base
    offsets = [sample.utc_s - base for sample in knots]
    position = [0.0, 0.0, 0.0]
    velocity = [0.0, 0.0, 0.0]
    for j, sample in enumerate(knots):
        others = [k for k in range(4) if k != j]
        denominator = math.prod(offsets[j] - offsets[k] for k in others)
        weight = math.prod(t - offsets[k] for k in others) / denominator
        derivative = sum(math.prod(t - offsets[k] for k in others if k != m)
                         for m in others) / denominator
        for axis in range(3):
            position[axis] += weight * sample.ecef_m[axis]
            velocity[axis] += derivative * sample.ecef_m[axis]
    if not (6.3e6 <= math.hypot(*position) <= 8.0e6 and
            100 <= math.hypot(*velocity) <= 15000):
        raise ValueError("interpolated virtual GNSS state is implausible")
    return tuple(position), tuple(velocity)


def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _cross(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, ...]:
    return (left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0])


def _unit(vector: tuple[float, ...]) -> tuple[float, ...]:
    length = math.hypot(*vector)
    if length <= 0 or not math.isfinite(length):
        raise ValueError("virtual attitude axis is invalid")
    return tuple(component / length for component in vector)


def _nadir_axes(position: tuple[float, ...],
                velocity: tuple[float, ...]) -> tuple[tuple[float, ...], ...]:
    """Body basis in ITRF: +Z nadir, +X along-track, +Y right-handed."""
    z = tuple(-value for value in _unit(position))
    tangential = tuple(velocity[i] - _dot(velocity, z) * z[i] for i in range(3))
    x = _unit(tangential)
    y = _unit(_cross(z, x))
    return x, y, z


def _nadir_quaternion(position: tuple[float, ...],
                      velocity: tuple[float, ...]) -> tuple[float, float, float, float]:
    """Body-to-ITRF: body +Z points to Earth; +X points along track."""
    x, y, z = _nadir_axes(position, velocity)
    # Rotation matrix columns are the body axes expressed in ITRF.
    m = ((x[0], y[0], z[0]), (x[1], y[1], z[1]), (x[2], y[2], z[2]))
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        s = 2 * math.sqrt(1 + trace)
        q = (0.25 * s, (m[2][1] - m[1][2]) / s,
             (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s)
    else:
        axis = max(range(3), key=lambda i: m[i][i])
        if axis == 0:
            s = 2 * math.sqrt(1 + m[0][0] - m[1][1] - m[2][2])
            q = ((m[2][1] - m[1][2]) / s, 0.25 * s,
                 (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s)
        elif axis == 1:
            s = 2 * math.sqrt(1 + m[1][1] - m[0][0] - m[2][2])
            q = ((m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s,
                 0.25 * s, (m[1][2] + m[2][1]) / s)
        else:
            s = 2 * math.sqrt(1 + m[2][2] - m[0][0] - m[1][1])
            q = ((m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s,
                 (m[1][2] + m[2][1]) / s, 0.25 * s)
    normalized = _unit(q)
    return tuple(-part for part in normalized) if normalized[0] < 0 else normalized


def _nadir_body_rates(view: OrbitView, utc_s: int,
                      axes: tuple[tuple[float, ...], ...]) -> tuple[float, float, float]:
    """Body angular velocity relative to ITRF, consistent with the TM quaternion.

    For an orthonormal moving basis e_i, sum(e_i x de_i/dt) = 2 omega.
    Differentiate over one second on either side of the sample when possible.
    """
    before_s = max(view.start_s, utc_s - 1)
    after_s = min(view.end_s, utc_s + 1)
    if before_s == after_s:
        raise ValueError("virtual view has no interval for attitude rate")
    before = _nadir_axes(*_interpolated_ecef(view, before_s))
    after = _nadir_axes(*_interpolated_ecef(view, after_s))
    span_s = after_s - before_s
    omega_itrf = tuple(
        0.5 * sum(_cross(axis, tuple((new[i] - old[i]) / span_s for i in range(3)))[k]
                  for axis, old, new in zip(axes, before, after))
        for k in range(3))
    return tuple(_dot(omega_itrf, axis) for axis in axes)


def generate_packet_batch(view: OrbitView, now: datetime, seq: int, *,
                          seed: int = 1) -> tuple[tuple[bytes, bytes, bytes], int]:
    """Pure TM sample source; caller supplies time and starting sequence."""
    if (not isinstance(view, OrbitView) or not isinstance(now, datetime) or
            now.tzinfo is None or now.utcoffset() != timedelta(0) or
            now.microsecond != 0 or type(seq) is not int or not 0 <= seq <= 65535 or
            type(seed) is not int or not 0 <= seed <= 0xffffffff):
        raise ValueError("UTC whole-second clock, u16 sequence and u32 seed required")
    stamp = int((now - _EPOCH).total_seconds())
    if not view.start_s <= stamp <= view.end_s or not 0 <= stamp - view.input_epoch_s < 72 * 3600:
        raise ValueError("virtual TLE result is not current")
    position, velocity = _interpolated_ecef(view, stamp)
    quaternion = _nadir_quaternion(position, velocity)
    body_rates = _nadir_body_rates(view, stamp, _nadir_axes(position, velocity))
    phase = (seed % 10000) * 0.017
    # A roughly 220-second cycle guarantees a visible, short test overvoltage
    # episode in every five-minute ROS history window (test limit: 5.5 V).
    voltage = 5.15 + 0.42 * math.sin(stamp / 35 + phase) + 0.03 * math.sin(stamp / 31)
    temperature = 22.0 + 4.0 * math.sin(stamp / 570 + phase / 2)
    # TLE refreshes every 15 minutes; spacecraft boot time must not reset
    # with each FDS view. This fixed epoch is synthetic, not flight TM.
    elapsed_s = stamp - _SIMULATED_BOOT_S
    if not 0 <= elapsed_s <= 0xffffffff:
        raise ValueError("simulated uptime exceeds draft TM field range")
    sampled_ms = stamp * 1000
    eps = encode_tm("EPS_PDU1", seq, sampled_ms, {
        "channel_enabled_bits": 3, "latchup_bits": 0,
        "bus_voltage_v": voltage, "temperature_c": temperature,
        "uptime_s": elapsed_s})
    attitude = encode_tm("AOCS_STATE", (seq + 1) & 0xffff, sampled_ms, {
        "adcs_state": 1, "control_mode": 1, "boot_index": 1,
        "sram_latchup_count": 0, "edac_error_count": 0,
        "q_w": quaternion[0], "q_x": quaternion[1],
        "q_y": quaternion[2], "q_z": quaternion[3],
        "body_rate_x_rad_s": body_rates[0], "body_rate_y_rad_s": body_rates[1],
        "body_rate_z_rad_s": body_rates[2]})
    gnss = encode_tm("GNSS_PVT", (seq + 2) & 0xffff, sampled_ms, {
        "norad_id": view.norad_id, "solution_status": 1, "receiver_age_ms": 0,
        "x_m": position[0], "y_m": position[1], "z_m": position[2],
        "vx_mps": velocity[0], "vy_mps": velocity[1], "vz_mps": velocity[2]})
    return (eps, attitude, gnss), (seq + 3) & 0xffff


def send_packet_batch(connection: socket.socket, packets: tuple[bytes, ...]) -> None:
    """Send framed draft TM PDUs; a receiver must reject TC and non-draft PDUs."""
    for packet in packets:
        if not isinstance(packet, bytes) or not 24 <= len(packet) <= 200:
            raise ValueError("bounded draft TM PDU required")
        connection.sendall(struct.pack(">I", len(packet)) + packet)


def run_forever(orbit_view: Path, host: str, port: int, interval_s: int,
                seed: int, *, clock: Callable[[], datetime] | None = None,
                sleeper: Callable[[float], None] = time.sleep) -> None:
    """Keep the simulated downlink alive while valid FDS output covers now."""
    clock = clock or (lambda: datetime.now(timezone.utc))
    connection: socket.socket | None = None
    try:
        while True:
            try:
                view = load_orbit_view(orbit_view)
                moment = clock()
                if moment.tzinfo is None:
                    raise ValueError("simulator clock must be timezone aware")
                now = moment.astimezone(timezone.utc).replace(microsecond=0)
                utc_s = int((now - _EPOCH).total_seconds())
                # Recover monotone modular sequence after an emulator restart.
                sequence = (3 * (utc_s // interval_s)) & 0xffff
                packets, _ = generate_packet_batch(view, now, sequence, seed=seed)
                if connection is None:
                    connection = socket.create_connection((host, port), timeout=3)
                    connection.settimeout(3)
                send_packet_batch(connection, packets)
            except (OSError, ValueError, PacketError) as error:
                if connection is not None:
                    connection.close()
                    connection = None
                print(f"TEST_ONLY simulated TM waiting: {error}", file=sys.stderr, flush=True)
            sleeper(interval_s)
    finally:
        if connection is not None:
            connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-only", action="store_true", required=True)
    parser.add_argument("--orbit-view", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_RECEIVER_PORT)
    parser.add_argument("--interval-seconds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 5 <= args.interval_seconds <= 60 or \
            not 0 <= args.seed <= 0xffffffff:
        parser.error("port, 5..60 second cadence or u32 seed invalid")
    try:
        run_forever(args.orbit_view, args.host, args.port, args.interval_seconds, args.seed)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
