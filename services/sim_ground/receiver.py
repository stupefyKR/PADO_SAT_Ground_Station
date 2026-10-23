"""Receive test-only PADO draft TM from the synthetic satellite.

The TCP length prefix is a simulator transport boundary, not an RF, AX100,
or CSP frame. Only the draft UHF application PDUs are decoded here. This
service has no TC path and must run on an internal Docker network only.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import socket
import struct
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
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
ATTITUDE_FRAME = "SIM_BODY_TO_ITRF_NADIR"
_DRAFT_GNSS_MANIFEST = re.compile(
    r"draft-gnss-[0-9a-f]{32}\.properties\.manifest\.json\Z")


class ReceiverError(ValueError):
    """A simulator connection violated its bounded test-only contract."""


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
        """Process one stream until clean EOF; invalid streams are closed."""
        accepted = 0
        while True:
            packet = read_framed_pdu(connection)
            if packet is None:
                return accepted
            self.ingest(packet)
            accepted += 1

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-only", action="store_true", required=True)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9200)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--n11-inbox", type=Path, required=True)
    parser.add_argument("--limits", type=Path, required=True)
    args = parser.parse_args()
    try:
        receiver = TmReceiver(args.snapshot, args.n11_inbox,
                              load_limits(args.limits))
        receiver.serve_forever(args.bind, args.port)
    except (OSError, ValueError, PacketError) as error:
        parser.exit(2, f"synthetic TM receiver failed: {error}\n")


if __name__ == "__main__":
    main()
