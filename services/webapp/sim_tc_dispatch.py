"""Dispatch due ROS simulation reservations to the private virtual satellite.

This path carries draft application PDUs over a local Unix socket. It has no
RF adapter, flight credentials, approval gate, or connection to the C TC gate.
Each reservation is claimed once before any send; an uncertain result is never
retried automatically.
"""

from __future__ import annotations

import socket
import stat
import struct
import threading
import time
from pathlib import Path

from tools.pado_packet_codec import PacketError, decode, encode_tc

from .ros_workbench import SIM_READ_ONLY_COMMANDS


_LENGTH = struct.Struct(">I")
_MAX_PDU = 200


class SimDispatchError(ValueError):
    pass


def _exact(connection: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = connection.recv(length - len(data))
        if not chunk:
            raise SimDispatchError("simulation bridge closed before ACK")
        data.extend(chunk)
    return bytes(data)


def send_sim_tc(socket_path: Path, reservation: dict, *, now_ms: int | None = None,
                timeout_s: float = 15.0) -> dict:
    """Send one draft TC and return a strictly correlated COMMAND_RESULT body."""
    if socket_path.is_symlink() or not socket_path.is_socket() or not stat.S_ISSOCK(
            socket_path.stat().st_mode):
        raise SimDispatchError("private simulation bridge unavailable")
    issued_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if type(issued_ms) is not int or issued_ms <= 0:
        raise SimDispatchError("invalid simulation issue time")
    if reservation.get("command_name") not in SIM_READ_ONLY_COMMANDS:
        raise SimDispatchError("only simulator read-only commands can be sent")
    sequence = reservation["command_sequence"]
    packet = encode_tc(reservation["command_name"], sequence, issued_ms,
                       reservation["parameters"])
    command = decode(packet)
    if command["kind"] != "TC" or command["operational"] is not False or \
            command["execute_at_utc_ms"] != 0:
        raise SimDispatchError("not an immediate draft TC")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout_s)
        connection.connect(str(socket_path))
        connection.sendall(_LENGTH.pack(len(packet)) + packet)
        (size,) = _LENGTH.unpack(_exact(connection, _LENGTH.size))
        if not 24 <= size <= _MAX_PDU:
            raise SimDispatchError("invalid simulation ACK length")
        ack = decode(_exact(connection, size))
    if ack["kind"] != "TM" or ack["name"] != "COMMAND_RESULT" or \
            ack["operational"] is not False or \
            ack["fields"]["command_id"] != command["id"] or \
            ack["fields"]["command_sequence"] != sequence:
        raise SimDispatchError("simulation ACK does not match the TC")
    fields = ack["fields"]
    if ((fields["result"] == 0 and
         (fields["reason"] != 0 or
          not issued_ms - 120_000 <= fields["executed_at_utc_ms"] <= issued_ms + 120_000)) or
            (fields["result"] == 1 and
             (fields["reason"] not in (1, 2, 3, 4) or
              fields["executed_at_utc_ms"] != 0)) or
            fields["result"] not in (0, 1)):
        raise SimDispatchError("simulation ACK has inconsistent time or result")
    return {key: fields[key] for key in ("command_id", "command_sequence", "result",
                                          "reason", "executed_at_utc_ms")}


class SimTcDispatcher(threading.Thread):
    """Poll only the SIM_ONLY reservation table; never touch legacy drafts."""

    def __init__(self, store, socket_path: Path) -> None:
        super().__init__(name="sim-tc-dispatch", daemon=True)
        self.store = store
        self.socket_path = socket_path
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()
        self.join(timeout=3)

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                reservation = self.store.claim_due(int(time.time()))
                if reservation is None:
                    self.stop_event.wait(0.5)
                    continue
                try:
                    ack = send_sim_tc(self.socket_path, reservation)
                    status = "ACKED" if ack["result"] == 0 else "REJECTED"
                    self.store.finish_sim(reservation["id"], status, ack)
                except (socket.timeout, TimeoutError):
                    self.store.finish_sim(reservation["id"], "TIMEOUT")
                except (OSError, PacketError, ValueError, KeyError, TypeError):
                    self.store.finish_sim(reservation["id"], "SEND_FAILED")
            except (OSError, ValueError):
                # A broken draft store must not become an automatic retry loop.
                self.stop_event.wait(1.0)
