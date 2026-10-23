"""Receive test-only PADO draft TM from the synthetic satellite.

The TCP length prefix is a simulator transport boundary, not an RF, AX100,
or CSP frame. Only draft UHF application PDUs are decoded here. The private
TCP stream also carries draft TC in the reverse direction and COMMAND_RESULT
TM back. A local Unix socket accepts one test-only TC request at a time from
ROS; neither socket is an operational command link.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import select
import socket
import stat
import struct
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from tools.bridge_draft_gnss import bridge
from tools.pado_packet_codec import PacketError, decode, gnss_to_fds_properties
from tools.preview_draft_tm import build_snapshot, publish_snapshot
from tools.run_fds_pipeline import verified_pair


FRAME_LENGTH = struct.Struct(">I")
MIN_PDU_BYTES = 24
MAX_PDU_BYTES = 200
MAX_AGE_MS = 120_000
MAX_BUFFERED_PDUS = 180
GNSS_BRIDGE_PERIOD_MS = 300_000
MAX_N11_PAIRS = 288  # 24 hours at the five-minute bridge cadence.
SATELLITE_NORAD_ID = 99999
ALLOWED_TELEMETRY = frozenset({"EPS_PDU1", "AOCS_STATE", "GNSS_PVT"})
RESULT_TELEMETRY = "COMMAND_RESULT"
TC_RESPONSE_TIMEOUT_S = 15.0
ATTITUDE_FRAME = "SIM_BODY_TO_ITRF_NADIR"
_DRAFT_GNSS_MANIFEST = re.compile(
    r"draft-gnss-[0-9a-f]{32}\.properties\.manifest\.json\Z")


class ReceiverError(ValueError):
    """A simulator connection violated its bounded test-only contract."""


class _PendingTc:
    def __init__(self, packet: bytes, command_id: int, sequence: int):
        self.packet = packet
        self.command_id = command_id
        self.sequence = sequence
        self.sent = False
        self.done = Event()
        self.response: bytes | None = None
        self.error: str | None = None


def load_limits(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= 16_384:
        raise ReceiverError("regular, bounded test limits file required")
    try:
        limits = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ReceiverError("invalid test limits JSON") from error
    if not isinstance(limits, dict) or set(limits) != {"sensors"} or \
            not isinstance(limits["sensors"], dict) or not limits["sensors"]:
        raise ReceiverError("explicit sensor limits required")
    return limits


def read_framed_pdu(connection: socket.socket) -> bytes | None:
    """Read one complete simulator frame; clean EOF is distinct from truncation."""
    prefix = _read_exact(connection, FRAME_LENGTH.size, allow_clean_eof=True)
    if prefix is None:
        return None
    (length,) = FRAME_LENGTH.unpack(prefix)
    if not MIN_PDU_BYTES <= length <= MAX_PDU_BYTES:
        raise ReceiverError("TM PDU frame length outside 24..200 bytes")
    packet = _read_exact(connection, length)
    if packet is None:  # Defensive: non-empty reads cannot return clean EOF.
        raise ReceiverError("truncated TM PDU")
    return packet


def send_framed_pdu(connection: socket.socket, packet: bytes) -> None:
    if not isinstance(packet, bytes) or not MIN_PDU_BYTES <= len(packet) <= MAX_PDU_BYTES:
        raise ReceiverError("bounded draft PDU required")
    connection.sendall(FRAME_LENGTH.pack(len(packet)) + packet)


def _read_exact(connection: socket.socket, length: int, *,
                allow_clean_eof: bool = False) -> bytes | None:
    result = bytearray()
    while len(result) < length:
        chunk = connection.recv(length - len(result))
        if not chunk:
            if not result and allow_clean_eof:
                return None
            raise ReceiverError("truncated simulator frame")
        result.extend(chunk)
    return bytes(result)


def prune_simulator_n11(inbox: Path, newest_manifest: Path, *,
                        keep: int = MAX_N11_PAIRS) -> int:
    """Bound only complete, verified simulator pairs; preserve the new pair.

    The consumer enumerates manifests, so remove each old manifest before its
    body. Unknown files, symlinks and incomplete or invalid pairs are untouched.
    """
    if type(keep) is not int or keep < 1 or inbox.is_symlink() or not inbox.is_dir() or \
            newest_manifest.parent != inbox or \
            _DRAFT_GNSS_MANIFEST.fullmatch(newest_manifest.name) is None:
        raise ReceiverError("invalid simulator N11 retention target")
    pairs: list[tuple[int, str, Path, Path]] = []
    for manifest in inbox.iterdir():
        if _DRAFT_GNSS_MANIFEST.fullmatch(manifest.name) is None or \
                manifest.is_symlink() or not manifest.is_file():
            continue
        body = inbox / manifest.name.removesuffix(".manifest.json")
        if body.is_symlink() or not body.is_file():
            continue
        try:
            if verified_pair(body, manifest, "gnss") is None:
                continue
            modified_ns = manifest.stat().st_mtime_ns
        except (OSError, ValueError, UnicodeError):
            continue
        pairs.append((modified_ns, manifest.name, manifest, body))
    if not any(manifest == newest_manifest for _, _, manifest, _ in pairs):
        raise ReceiverError("newly bridged N11 pair is not verified")
    pairs.sort(key=lambda item: (item[0], item[1]), reverse=True)
    retained = {newest_manifest}
    for _, _, manifest, _ in pairs:
        if len(retained) >= keep:
            break
        retained.add(manifest)
    deleted = 0
    for _, _, manifest, body in pairs:
        if manifest in retained:
            continue
        manifest.unlink()
        body.unlink()
        deleted += 1
    return deleted


class TmReceiver:
    """Single-writer, bounded decoder and ROS/FDS test publication point."""

    def __init__(self, snapshot: Path, n11_inbox: Path, limits: dict[str, Any]):
        if snapshot.name != "test_tm_snapshot.json":
            raise ReceiverError("ROS snapshot must be test_tm_snapshot.json")
        if snapshot.parent.is_symlink() or not snapshot.parent.is_dir():
            raise ReceiverError("ROS snapshot directory must exist and be regular")
        if n11_inbox.is_symlink() or (n11_inbox.exists() and not n11_inbox.is_dir()):
            raise ReceiverError("N11 test inbox must be a regular directory")
        self.snapshot = snapshot
        self.n11_inbox = n11_inbox
        self.limits = limits
        self.packets: deque[bytes] = deque(maxlen=MAX_BUFFERED_PDUS)
        self.last_sequence: int | None = None
        self.last_stamp_ms: int | None = None
        self.last_stamp_by_name: dict[str, int] = {}
        self.last_gnss_bridge_ms: int | None = None
        self._tc_lock = Lock()
        self._tc_pending: _PendingTc | None = None
        self._satellite_connected = False

    def submit_tc(self, packet: bytes, *, timeout_s: float = TC_RESPONSE_TIMEOUT_S,
                  now_ms: int | None = None) -> bytes:
        """Queue one fresh draft TC and await its correlated COMMAND_RESULT TM.

        This method is the in-process boundary behind the private Unix socket.
        It never accepts a flight packet or initiates a network uplink itself.
        """
        if not 0 < timeout_s <= 30 or (now_ms is not None and type(now_ms) is not int):
            raise ReceiverError("invalid test TC timeout or clock")
        decoded = decode(packet)
        if decoded["kind"] != "TC" or decoded["operational"] is not False or \
                decoded["execute_at_utc_ms"] != 0:
            raise ReceiverError("immediate draft TC required")
        instant = int(time.time() * 1000) if now_ms is None else now_ms
        if not 0 <= instant - decoded["utc_ms"] <= MAX_AGE_MS:
            raise ReceiverError("test TC issue timestamp is not fresh")
        request = _PendingTc(packet, decoded["id"], decoded["sequence"])
        with self._tc_lock:
            if not self._satellite_connected:
                raise ReceiverError("simulated satellite TM stream unavailable")
            if self._tc_pending is not None:
                raise ReceiverError("simulated TC path is busy")
            self._tc_pending = request
        if not request.done.wait(timeout_s):
            with self._tc_lock:
                completed_at_deadline = request.done.is_set()
                if not completed_at_deadline and self._tc_pending is request:
                    self._tc_pending = None
            if not completed_at_deadline:
                raise ReceiverError("simulated COMMAND_RESULT timed out")
        if request.error is not None or request.response is None:
            raise ReceiverError(request.error or "simulated COMMAND_RESULT missing")
        return request.response

    def _fail_pending(self, message: str) -> None:
        with self._tc_lock:
            request = self._tc_pending
            self._tc_pending = None
            if request is not None:
                request.error = message
                request.done.set()

    def _ingest_command_result(self, packet: bytes, decoded: dict[str, Any],
                               *, received_ms: int | None = None) -> None:
        """Correlate ACK by draft command ID/sequence before releasing ROS."""
        now_ms = int(time.time() * 1000) if received_ms is None else received_ms
        stamp = decoded["utc_ms"]
        if decoded["kind"] != "TM" or decoded["name"] != RESULT_TELEMETRY or \
                decoded["operational"] is not False or stamp % 1000 or \
                not 0 <= now_ms // 1000 * 1000 - stamp <= MAX_AGE_MS or \
                (self.last_stamp_ms is not None and stamp < self.last_stamp_ms) or \
                self.last_stamp_by_name.get(RESULT_TELEMETRY, -1) >= stamp:
            raise ReceiverError("invalid simulated COMMAND_RESULT TM timestamp")
        if self.last_sequence is not None:
            step = (decoded["sequence"] - self.last_sequence) & 0xffff
            if not 1 <= step <= 32767:
                raise ReceiverError("replayed simulated COMMAND_RESULT TM")
        with self._tc_lock:
            request = self._tc_pending
            fields = decoded["fields"]
            if request is None or not request.sent or \
                    fields["command_id"] != request.command_id or \
                    fields["command_sequence"] != request.sequence:
                raise ReceiverError("unmatched simulated COMMAND_RESULT TM")
            if not ((fields["result"] == 0 and fields["reason"] == 0 and
                     fields["executed_at_utc_ms"] >= stamp) or
                    (fields["result"] == 1 and fields["reason"] in (1, 2, 3, 4) and
                     fields["executed_at_utc_ms"] == 0)):
                raise ReceiverError("invalid simulated COMMAND_RESULT status")
            self.last_sequence = decoded["sequence"]
            self.last_stamp_ms = stamp
            self.last_stamp_by_name[RESULT_TELEMETRY] = stamp
            self._tc_pending = None
            request.response = packet
            request.done.set()

    def ingest(self, packet: bytes, *, received_ms: int | None = None) -> dict[str, Any]:
        """Validate one draft PDU, update the preview, and bridge GNSS sparingly."""
        if type(received_ms) is not int and received_ms is not None:
            raise ReceiverError("integer receive UTC milliseconds required")
        now_ms = int(time.time() * 1000) if received_ms is None else received_ms
        received_second_ms = now_ms // 1000 * 1000
        decoded = decode(packet)
        if decoded["kind"] != "TM" or decoded["operational"] is not False or \
                decoded["name"] not in ALLOWED_TELEMETRY:
            raise ReceiverError("only synthetic draft TM is accepted")
        stamp = decoded["utc_ms"]
        if stamp % 1000 or not 0 <= received_second_ms - stamp <= MAX_AGE_MS:
            raise ReceiverError("TM timestamp is not a fresh whole UTC second")
        if self.last_stamp_ms is not None and stamp < self.last_stamp_ms:
            raise ReceiverError("TM timestamp moved backwards")
        if self.last_stamp_by_name.get(decoded["name"], -1) >= stamp:
            raise ReceiverError("duplicate or out-of-order TM sample")
        if self.last_sequence is not None:
            step = (decoded["sequence"] - self.last_sequence) & 0xffff
            if not 1 <= step <= 32767:
                raise ReceiverError("duplicate or backwards TM sequence")
        if decoded["name"] == "GNSS_PVT":
            if decoded["fields"]["norad_id"] != SATELLITE_NORAD_ID:
                raise ReceiverError("GNSS satellite identity mismatch")
            gnss_to_fds_properties(decoded)  # Fix, age, state envelope and frame contract.
        elif decoded["name"] == "AOCS_STATE":
            q = [decoded["fields"][f"q_{axis}"] for axis in "wxyz"]
            if abs(math.hypot(*q) - 1.0) > 0.02:
                raise ReceiverError("non-unit synthetic attitude quaternion")

        candidate = list(self.packets) + [packet]
        candidate = candidate[-MAX_BUFFERED_PDUS:]
        if self._has_all_sensor_sources(candidate):
            preview = build_snapshot(candidate, self.limits,
                                     received_ms=received_second_ms)
            if preview["attitude"] is not None:
                preview["attitude"]["frame"] = ATTITUDE_FRAME
            publish_snapshot(preview, self.snapshot)

        self.packets.append(packet)
        self.last_sequence = decoded["sequence"]
        self.last_stamp_ms = stamp
        self.last_stamp_by_name[decoded["name"]] = stamp

        if decoded["name"] == "GNSS_PVT" and (
                self.last_gnss_bridge_ms is None or
                stamp - self.last_gnss_bridge_ms >= GNSS_BRIDGE_PERIOD_MS):
            _, manifest = bridge(packet, self.n11_inbox,
                                 created=datetime.fromtimestamp(now_ms / 1000,
                                                                timezone.utc))
            prune_simulator_n11(self.n11_inbox, manifest, keep=MAX_N11_PAIRS)
            self.last_gnss_bridge_ms = stamp
        return decoded

    def _has_all_sensor_sources(self, packets: list[bytes]) -> bool:
        required = {name.split(".", 1)[0] for name in self.limits["sensors"]}
        present = {decode(packet)["name"] for packet in packets}
        return required <= present

    def process_connection(self, connection: socket.socket) -> int:
        """Process one duplex stream until clean EOF; invalid streams close."""
        accepted = 0
        with self._tc_lock:
            self._satellite_connected = True
        try:
            while True:
                to_send = None
                with self._tc_lock:
                    request = self._tc_pending
                    if request is not None and not request.sent:
                        request.sent = True
                        to_send = request.packet
                if to_send is not None:
                    # Exactly one writer owns this socket on the ground side.
                    # A failed send closes the stream and wakes the submitter.
                    send_framed_pdu(connection, to_send)
                # Socket doubles used by unit tests have no descriptor. Live
                # sockets are polled so queued TC is sent within 250 ms.
                if isinstance(connection, socket.socket) and not \
                        select.select([connection], [], [], 0.25)[0]:
                    continue
                packet = read_framed_pdu(connection)
                if packet is None:
                    return accepted
                decoded = decode(packet)
                if decoded["name"] == RESULT_TELEMETRY:
                    self._ingest_command_result(packet, decoded)
                else:
                    self.ingest(packet)
                accepted += 1
        finally:
            with self._tc_lock:
                self._satellite_connected = False
            self._fail_pending("simulated satellite TM stream closed")

    def serve_forever(self, bind: str, port: int, *, stop: Event | None = None) -> None:
        if not 1 <= port <= 65535:
            raise ReceiverError("TCP port out of range")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((bind, port))
            listener.listen(1)
            listener.settimeout(1.0)
            print(f"test-only TM receiver listening on {bind}:{port}", flush=True)
            while stop is None or not stop.is_set():
                try:
                    connection, address = listener.accept()
                except socket.timeout:
                    continue
                with connection:
                    connection.settimeout(15.0)
                    try:
                        accepted = self.process_connection(connection)
                        print(f"TM stream {address[0]} closed after {accepted} PDUs",
                              flush=True)
                    except (OSError, ValueError, PacketError, OverflowError) as error:
                        print(f"TM stream {address[0]} rejected: {error}",
                              file=sys.stderr, flush=True)

    def serve_tc_socket(self, path: Path, *, stop: Event | None = None,
                        ready: Event | None = None) -> None:
        """Accept one draft TC per Unix connection, replying with one TM PDU."""
        if (path.name != "bridge.sock" or path.parent.is_symlink() or
                not path.parent.is_dir()):
            raise ReceiverError("regular private simulator TC socket directory required")
        if path.exists() or path.is_symlink():
            mode = path.lstat().st_mode
            if not stat.S_ISSOCK(mode):
                raise ReceiverError("simulator TC path is not a stale socket")
            path.unlink()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(path))
                os.chmod(path, 0o600)
                listener.listen(1)
                listener.settimeout(1.0)
                if ready is not None:
                    ready.set()
                print(f"TEST_ONLY TC bridge listening on {path}", flush=True)
                while stop is None or not stop.is_set():
                    try:
                        connection, _ = listener.accept()
                    except socket.timeout:
                        continue
                    with connection:
                        connection.settimeout(TC_RESPONSE_TIMEOUT_S + 2)
                        try:
                            packet = read_framed_pdu(connection)
                            if packet is None:
                                raise ReceiverError("empty simulator TC request")
                            result = self.submit_tc(packet)
                            send_framed_pdu(connection, result)
                        except (OSError, ValueError, PacketError) as error:
                            print(f"TEST_ONLY TC bridge rejected request: {error}",
                                  file=sys.stderr, flush=True)
        finally:
            if path.is_socket():
                path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-only", action="store_true", required=True)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9200)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--n11-inbox", type=Path, required=True)
    parser.add_argument("--limits", type=Path, required=True)
    parser.add_argument("--tc-socket", type=Path,
                        help="private Unix socket for one test-only TC/ACK exchange")
    args = parser.parse_args()
    try:
        receiver = TmReceiver(args.snapshot, args.n11_inbox,
                              load_limits(args.limits))
        stop = Event()
        bridge_thread = None
        if args.tc_socket is not None:
            ready = Event()
            bridge_thread = Thread(target=receiver.serve_tc_socket,
                                   args=(args.tc_socket,),
                                   kwargs={"stop": stop, "ready": ready},
                                   name="sim-tc-bridge", daemon=True)
            bridge_thread.start()
            if not ready.wait(timeout=3):
                raise ReceiverError("simulated TC Unix socket did not start")
        try:
            receiver.serve_forever(args.bind, args.port, stop=stop)
        finally:
            stop.set()
            if bridge_thread is not None:
                bridge_thread.join(timeout=2)
    except (OSError, ValueError, PacketError) as error:
        parser.exit(2, f"synthetic TM receiver failed: {error}\n")


if __name__ == "__main__":
    main()
