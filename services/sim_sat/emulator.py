"""Emit draft PADO TM packets from the virtual satellite's FDS orbit view.

This is a TEST_ONLY application-PDU source, not a spacecraft/RF protocol. The
view already contains the FDS TLE propagation; ECEF velocity is approximated
by differentiating four neighboring 60-second ECEF position samples. No second
orbit propagator runs here. The private TCP stream carries framed draft TM in
one direction and a strictly test-only TC/COMMAND_RESULT exchange in the other.
It is neither an RF/AX100/CSP transport nor an operational command link.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import re
import select
import sqlite3
import socket
import struct
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence

from tools.pado_packet_codec import PacketError, encode_tm
from .contact import ContactGate
from .dispatcher import (CommandContext, TcDispatcher, TcReplayState,
                         MAX_TC_AGE_MS, MAX_TC_REPLAY_ENTRIES,
                         SIM_READ_ONLY_COMMANDS, RESULT_OK, RESULT_REJECTED,
                         REASON_OK, REASON_UNSUPPORTED, REASON_SCHEDULED,
                         REASON_REPLAY, REASON_STALE)


MAX_VIEW_BYTES = 4 * 1024 * 1024
MAX_SAMPLES = 5000
VIRTUAL_NORAD_ID = 99999
DEFAULT_RECEIVER_PORT = 9200
MAX_SPOOL_SECONDS = 24 * 60 * 60
FLUSH_CHUNK_PDUS = 512
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
    scenario_id: str | None = None


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


def _valid_scenario_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value) is not None


def require_scenario(view: OrbitView, expected: str | None) -> None:
    """A configured scenario must not consume a prior deployment's truth view."""
    if expected is None:
        return
    if not _valid_scenario_id(expected):
        raise ValueError("invalid simulation scenario ID")
    if view.scenario_id != expected:
        raise ValueError(f"simulation truth scenario mismatch; waiting for {expected}")


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
    scenario = document.get("simulation_scenario_id")
    if "simulation_scenario_id" in document and not _valid_scenario_id(scenario):
        raise ValueError("invalid simulation truth scenario ID")
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
                     tuple(item.utc_s for item in samples), scenario)


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
    """Body basis in ITRF: -X nadir, +Z along-track, +Y right-handed."""
    x = _unit(position)
    tangential = tuple(velocity[i] - _dot(velocity, x) * x[i] for i in range(3))
    z = _unit(tangential)
    y = _unit(_cross(z, x))
    return x, y, z


def _nadir_quaternion(position: tuple[float, ...],
                      velocity: tuple[float, ...]) -> tuple[float, float, float, float]:
    """Body-to-ITRF: body -X points to Earth; +Z points along track."""
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


class TmSpool:
    """Durable, bounded spacecraft TM awaiting a dump TC in a UHF PASS.

    One row holds one unchanged draft PDU. The generation cursor is committed
    atomically with all three PDUs, so a normal container restart neither loses
    queued samples nor regenerates an already transmitted sample.
    """

    def __init__(self, path: Path, *, interval_s: int = 5,
                 max_ticks: int | None = None):
        if type(interval_s) is not int or not 5 <= interval_s <= 60:
            raise ValueError("5..60 second simulator sample cadence required")
        if max_ticks is None:
            max_ticks = MAX_SPOOL_SECONDS // interval_s
        if (not isinstance(path, Path) or path.is_symlink() or
                not path.parent.is_dir() or path.parent.is_symlink() or
                (path.exists() and not path.is_file()) or
                type(max_ticks) is not int or
                not 1 <= max_ticks <= MAX_SPOOL_SECONDS // interval_s):
            raise ValueError("regular bounded simulator spool path required")
        self.path = path
        self.interval_s = interval_s
        self.max_ticks = max_ticks
        self.connection = sqlite3.connect(path, timeout=5)
        try:
            os.chmod(path, 0o600)
            self.connection.execute("PRAGMA journal_mode=DELETE")
            self.connection.execute("PRAGMA synchronous=FULL")
            with self.connection:
                self.connection.execute("CREATE TABLE IF NOT EXISTS tm_spool ("
                                        "id INTEGER PRIMARY KEY, sampled_s INTEGER NOT NULL, "
                                        "packet_index INTEGER NOT NULL, pdu BLOB NOT NULL, "
                                        "UNIQUE(sampled_s, packet_index))")
                self.connection.execute("CREATE TABLE IF NOT EXISTS spool_meta ("
                                        "key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
                self.connection.execute("CREATE TABLE IF NOT EXISTS tc_replay ("
                                        "issued_ms INTEGER NOT NULL, sequence INTEGER NOT NULL, "
                                        "received_ms INTEGER NOT NULL, "
                                        "PRIMARY KEY(issued_ms,sequence))")
                recorded_interval = self.connection.execute(
                    "SELECT value FROM spool_meta WHERE key = ?", ("interval_s",)).fetchone()
                if recorded_interval is None:
                    self.connection.execute("INSERT INTO spool_meta(key,value) VALUES (?,?)",
                                            ("interval_s", interval_s))
                elif recorded_interval[0] != interval_s:
                    raise ValueError("simulator spool cadence changed across restart")
            result = self.connection.execute("SELECT value FROM spool_meta WHERE key = ?",
                                             ("last_generated_s",)).fetchone()
            self.last_generated_s: int | None = result[0] if result is not None else None
        except BaseException:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def pending_count(self) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM tm_spool").fetchone()[0]

    def pending_window(self) -> tuple[int | None, int | None]:
        return self.connection.execute(
            "SELECT MIN(sampled_s),MAX(sampled_s) FROM tm_spool").fetchone()

    def next_control_sequence(self) -> int:
        """Persistent independent live beacon/ACK cursor, never a history cursor."""
        with self.connection:
            row = self.connection.execute(
                "SELECT value FROM spool_meta WHERE key='control_counter'").fetchone()
            counter = ((row[0] if row else 0) + 1) & 0xffffffff
            self.connection.execute("INSERT INTO spool_meta(key,value) VALUES (?,?) "
                                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                    ("control_counter", counter))
        return counter

    def next_beacon_counter(self) -> int:
        with self.connection:
            row = self.connection.execute(
                "SELECT value FROM spool_meta WHERE key='beacon_counter'").fetchone()
            counter = ((row[0] if row else 0) + 1) & 0xffffffff
            self.connection.execute("INSERT INTO spool_meta(key,value) VALUES (?,?) "
                                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                    ("beacon_counter", counter))
        return counter

    def load_replay(self) -> TcReplayState:
        rows = list(self.connection.execute(
            "SELECT issued_ms,sequence,received_ms FROM tc_replay LIMIT ?",
            (MAX_TC_REPLAY_ENTRIES + 1,)))
        if len(rows) > MAX_TC_REPLAY_ENTRIES:
            raise ValueError("simulator TC replay table exceeds limit")
        return TcReplayState({(issued, sequence): received for issued, sequence, received in rows})

    def save_replay(self, replay: TcReplayState) -> None:
        if len(replay.seen) > MAX_TC_REPLAY_ENTRIES:
            raise ValueError("bounded simulator TC replay state required")
        with self.connection:
            self.connection.execute("DELETE FROM tc_replay")
            self.connection.executemany("INSERT INTO tc_replay VALUES (?,?,?)",
                                        ((issued, seq, received)
                                         for (issued, seq), received in replay.seen.items()))

    def iter_pending(self, limit: int = FLUSH_CHUNK_PDUS, *,
                     window: tuple[int, int] | None = None) -> list[tuple[int, bytes]]:
        if type(limit) is not int or not 1 <= limit <= FLUSH_CHUNK_PDUS:
            raise ValueError("bounded TM flush size required")
        if window is not None:
            if (not isinstance(window, tuple) or len(window) != 2 or
                    any(type(value) is not int for value in window) or
                    not 0 <= window[0] <= window[1]):
                raise ValueError("ordered whole-second dump window required")
            rows = self.connection.execute(
                "SELECT id,pdu FROM tm_spool WHERE sampled_s BETWEEN ? AND ? "
                "ORDER BY sampled_s,packet_index LIMIT ?", (*window, limit))
        else:
            rows = self.connection.execute(
                "SELECT id, pdu FROM tm_spool ORDER BY sampled_s,packet_index LIMIT ?", (limit,))
        return [(row_id, bytes(pdu)) for row_id, pdu in rows]

    def mark_sent(self, ids: Sequence[int]) -> None:
        if (not isinstance(ids, (tuple, list)) or len(ids) > FLUSH_CHUNK_PDUS or
                any(type(value) is not int or value <= 0 for value in ids)):
            raise ValueError("bounded simulator spool IDs required")
        with self.connection:
            self.connection.executemany("DELETE FROM tm_spool WHERE id = ?",
                                        ((value,) for value in ids))

    def queue_tick(self, view: OrbitView, now: datetime, *, seed: int = 1) -> bool:
        if not isinstance(now, datetime) or now.tzinfo is None or \
                now.utcoffset() != timedelta(0) or now.microsecond:
            raise ValueError("whole-second UTC simulator sample required")
        stamp = int((now - _EPOCH).total_seconds())
        if stamp % self.interval_s:
            raise ValueError("simulator sample must align to cadence")
        if self.last_generated_s is not None and stamp <= self.last_generated_s:
            return False
        sequence = (4 * (stamp // self.interval_s)) & 0xffff
        packets, _ = generate_packet_batch(view, now, sequence, seed=seed)
        dropped = 0
        with self.connection:
            self.connection.executemany(
                "INSERT INTO tm_spool(sampled_s,packet_index,pdu) VALUES (?,?,?)",
                ((stamp, index, packet) for index, packet in enumerate(packets)))
            self.connection.execute("INSERT INTO spool_meta(key,value) VALUES (?,?) "
                                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                    ("last_generated_s", stamp))
            cutoff = stamp - self.interval_s * self.max_ticks
            cursor = self.connection.execute("DELETE FROM tm_spool WHERE sampled_s <= ?",
                                             (cutoff,))
            dropped += cursor.rowcount
            while self.connection.execute("SELECT COUNT(*) FROM tm_spool").fetchone()[0] > \
                    3 * self.max_ticks:
                oldest = self.connection.execute(
                    "SELECT sampled_s FROM tm_spool ORDER BY id LIMIT 1").fetchone()[0]
                cursor = self.connection.execute(
                    "DELETE FROM tm_spool WHERE sampled_s = ?", (oldest,))
                dropped += cursor.rowcount
        self.last_generated_s = stamp
        if dropped:
            print(f"TEST_ONLY TM spool overflow: dropped {dropped} oldest PDUs",
                  file=sys.stderr, flush=True)
        return True


def flush_pending(spool: TmSpool, connection: socket.socket, gate: ContactGate,
                  *, clock_ms: Callable[[], int] | None = None,
                  max_pdus: int = FLUSH_CHUNK_PDUS,
                  window: tuple[int, int] | None = None) -> int:
    """Send the oldest queued PDUs only in contact, preserving their timestamps.

    Successful ``sendall`` is the local dequeue boundary. There is no RF or
    application delivery acknowledgement in this TEST_ONLY transport.
    """
    if not isinstance(spool, TmSpool) or not 1 <= max_pdus <= FLUSH_CHUNK_PDUS:
        raise ValueError("bounded simulator flush context required")
    now_ms = clock_ms or (lambda: int(time.time() * 1000))
    sent_ids: list[int] = []
    try:
        for row_id, packet in spool.iter_pending(max_pdus, window=window):
            if not gate.active(now_ms()):
                break
            send_packet_batch(connection, (packet,))
            sent_ids.append(row_id)
    finally:
        if sent_ids:
            spool.mark_sent(sent_ids)
    return len(sent_ids)


def send_packet_batch(connection: socket.socket, packets: tuple[bytes, ...]) -> None:
    """Send framed draft TM PDUs; a receiver must reject TC and non-draft PDUs."""
    for packet in packets:
        if not isinstance(packet, bytes) or not 24 <= len(packet) <= 200:
            raise ValueError("bounded draft TM PDU required")
        connection.sendall(struct.pack(">I", len(packet)) + packet)


def _read_exact(connection: socket.socket, length: int, *, clean_eof: bool = False) -> bytes | None:
    result = bytearray()
    while len(result) < length:
        chunk = connection.recv(length - len(result))
        if not chunk:
            if not result and clean_eof:
                return None
            raise OSError("truncated test-only TC frame")
        result.extend(chunk)
    return bytes(result)


def read_test_tc(connection: socket.socket) -> bytes | None:
    """Read one bounded PADO application PDU, never a flight link frame."""
    prefix = _read_exact(connection, 4, clean_eof=True)
    if prefix is None:
        return None
    (length,) = struct.unpack(">I", prefix)
    if not 24 <= length <= 200:
        raise ValueError("test-only TC frame length outside 24..200 bytes")
    packet = _read_exact(connection, length)
    if packet is None:
        raise OSError("truncated test-only TC frame")
    return packet


def build_command_result(packet: bytes, tm_sequence: int, replay: TcReplayState,
                         *, received_ms: int) -> bytes:
    """Compatibility helper for the registry's validation/acceptance result."""
    return TcDispatcher(replay).dispatch(packet, tm_sequence,
                                         CommandContext(received_ms)).ack



def wait_and_answer_one_tc(connection: socket.socket, tm_sequence: int,
                           replay: TcReplayState, timeout_s: float,
                           *, clock_ms: Callable[[], int] | None = None) -> bool:
    """Poll the same private TM socket for at most one TC per TM interval."""
    if timeout_s < 0:
        raise ValueError("negative simulator wait")
    if not select.select([connection], [], [], timeout_s)[0]:
        return False
    packet = read_test_tc(connection)
    if packet is None:
        raise OSError("simulated ground TCP peer closed")
    sampled_ms = (clock_ms or (lambda: int(time.time() * 1000)))()
    result = build_command_result(packet, tm_sequence, replay,
                                  received_ms=sampled_ms)
    send_packet_batch(connection, (result,))
    return True


class SatelliteSession:
    """One contact connection: beacon by default, stored TM only after a TC.

    The dump authorization is deliberately transient. Closing the link or
    restarting keeps the durable TM but requires a new command to resume.
    """

    def __init__(self, spool: TmSpool):
        self.spool = spool
        self.dispatcher = TcDispatcher(spool.load_replay())
        self.dump_window: tuple[int, int] | None = None

    def disconnect(self) -> None:
        self.dump_window = None

    def send_beacon(self, connection: socket.socket, gate: ContactGate,
                    *, now_ms: int) -> bool:
        if not gate.active(now_ms):
            self.disconnect()
            return False
        counter = self.spool.next_beacon_counter()
        sequence = self.spool.next_control_sequence() & 0xffff
        usage = min(100, self.spool.pending_count() * 100 // (3 * self.spool.max_ticks))
        beacon = encode_tm("BEACON_CORE", sequence, now_ms // 1000 * 1000,
                           {"mode": 1, "beacon_counter": counter,
                            "beacon_period_s": self.spool.interval_s, "boot_count": 1,
                            "reset_cause": 0, "fsw_image": 1, "crash_count": 0,
                            "watchdog_state": 1, "nor_usage_pct": usage,
                            "fdir_level": 0, "fault_bitmap": 0})
        send_packet_batch(connection, (beacon,))
        return True

    def handle_tc(self, packet: bytes, connection: socket.socket,
                  gate: ContactGate, *, now_ms: int) -> bool:
        if not gate.active(now_ms):
            self.disconnect()
            return False
        first, last = self.spool.pending_window()
        sequence = self.spool.next_control_sequence() & 0xffff
        result = self.dispatcher.dispatch(packet, sequence,
                                          CommandContext(now_ms, first, last))
        # Save before acknowledging, so an ACK loss/restart cannot turn a replay
        # into another execution. No handler itself writes to the wire.
        self.spool.save_replay(self.dispatcher.replay)
        send_packet_batch(connection, (result.ack,))
        if result.action is not None and result.action.dump_requested:
            # A second valid dump replaces the remaining selection explicitly.
            self.dump_window = result.action.dump_window
            print(f"TEST_ONLY stored TM request accepted: "
                  f"window={self.dump_window} queued={self.spool.pending_count()} PDUs",
                  flush=True)
        return True

    def flush_requested(self, connection: socket.socket, gate: ContactGate, *,
                        clock_ms: Callable[[], int],
                        max_pdus: int = FLUSH_CHUNK_PDUS) -> int:
        if not gate.active(clock_ms()):
            self.disconnect()
            return 0
        if self.dump_window is None:
            return 0
        sent = flush_pending(self.spool, connection, gate, clock_ms=clock_ms,
                             max_pdus=max_pdus, window=self.dump_window)
        if not self.spool.iter_pending(1, window=self.dump_window):
            self.dump_window = None
            print(f"TEST_ONLY stored TM request completed: "
                  f"remaining={self.spool.pending_count()} PDUs", flush=True)
        return sent


def run_forever(orbit_view: Path, host: str, port: int, interval_s: int,
                seed: int, spool_path: Path, *,
                scenario_id: str | None = None,
                clock: Callable[[], datetime] | None = None,
                sleeper: Callable[[float], None] = time.sleep) -> None:
    """Store every TM tick; beacon in contact, dump only when commanded."""
    if scenario_id is not None and not _valid_scenario_id(scenario_id):
        raise ValueError("invalid simulation scenario ID")
    clock = clock or (lambda: datetime.now(timezone.utc))
    connection: socket.socket | None = None
    gate = ContactGate(orbit_view)
    spool = TmSpool(spool_path, interval_s=interval_s)
    session = SatelliteSession(spool)
    previous_contact: bool | None = None
    try:
        while True:
            tick_started = time.monotonic()
            try:
                view = load_orbit_view(orbit_view)
                require_scenario(view, scenario_id)
                moment = clock()
                if moment.tzinfo is None:
                    raise ValueError("simulator clock must be timezone aware")
                utc_s = int((moment.astimezone(timezone.utc) - _EPOCH).total_seconds())
                tick_s = utc_s // interval_s * interval_s
                # FDS views refresh while the emulator runs. Reconstruct only
                # missed ticks still covered by the current truth view and the
                # bounded 24-hour spool; never invent samples beyond coverage.
                first = tick_s if spool.last_generated_s is None else \
                    spool.last_generated_s + interval_s
                first = max(first, tick_s - (spool.max_ticks - 1) * interval_s,
                            math.ceil(view.start_s / interval_s) * interval_s,
                            math.ceil(view.input_epoch_s / interval_s) * interval_s)
                if spool.last_generated_s is not None and \
                        first > spool.last_generated_s + interval_s:
                    print("TEST_ONLY TM generation gap outside retained FDS coverage",
                          file=sys.stderr, flush=True)
                for sample_s in range(first, min(tick_s, view.end_s) + 1, interval_s):
                    sample = _EPOCH + timedelta(seconds=sample_s)
                    spool.queue_tick(view, sample, seed=seed)

                now_ms = int((moment.astimezone(timezone.utc) - _EPOCH).total_seconds() * 1000)
                contact_open = gate.active(now_ms)
                if contact_open != previous_contact:
                    print(f"TEST_ONLY UHF contact {'open' if contact_open else 'closed'}: "
                          f"queued={spool.pending_count()} PDUs", flush=True)
                    previous_contact = contact_open
                if not contact_open:
                    session.disconnect()
                    if connection is not None:
                        connection.close()
                        connection = None
                else:
                    if connection is None:
                        connection = socket.create_connection((host, port), timeout=3)
                        connection.settimeout(3)
                    session.send_beacon(connection, gate, now_ms=now_ms)
                    # Poll TC independently of backlog size. ACK first, then
                    # move bounded chunks of the command-time snapshot. Fresh
                    # generation continues next tick and is not silently added.
                    sent_this_tick = 0
                    while (time.monotonic() - tick_started < interval_s - 0.2 and
                           gate.active(int(clock().timestamp() * 1000))):
                        wait_s = 0.0 if session.dump_window is not None else min(
                            0.25, max(0.0, interval_s - 0.2 -
                                      (time.monotonic() - tick_started)))
                        if select.select([connection], [], [], wait_s)[0]:
                            packet = read_test_tc(connection)
                            if packet is None:
                                raise OSError("simulated ground TCP peer closed")
                            session.handle_tc(packet, connection, gate,
                                              now_ms=int(clock().timestamp() * 1000))
                        sent_this_tick += session.flush_requested(
                            connection, gate, clock_ms=lambda: int(clock().timestamp() * 1000),
                            max_pdus=128)
                    if sent_this_tick:
                        print(f"TEST_ONLY stored TM downlink: sent={sent_this_tick} "
                              f"remaining={spool.pending_count()} PDUs", flush=True)
            except (OSError, ValueError, PacketError, sqlite3.Error) as error:
                session.disconnect()
                if connection is not None:
                    connection.close()
                    connection = None
                print(f"TEST_ONLY simulated TM waiting: {error}", file=sys.stderr, flush=True)
            sleeper(max(0.0, interval_s - (time.monotonic() - tick_started)))
    finally:
        if connection is not None:
            connection.close()
        spool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-only", action="store_true", required=True)
    parser.add_argument("--orbit-view", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_RECEIVER_PORT)
    parser.add_argument("--interval-seconds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--spool", type=Path, required=True)
    parser.add_argument("--scenario-id",
                        help="wait for the matching synthetic truth scenario before producing TM")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 5 <= args.interval_seconds <= 60 or \
            not 0 <= args.seed <= 0xffffffff or \
            (args.scenario_id is not None and not _valid_scenario_id(args.scenario_id)):
        parser.error("port, 5..60 second cadence, u32 seed or scenario ID invalid")
    try:
        run_forever(args.orbit_view, args.host, args.port, args.interval_seconds,
                    args.seed, args.spool, scenario_id=args.scenario_id)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
