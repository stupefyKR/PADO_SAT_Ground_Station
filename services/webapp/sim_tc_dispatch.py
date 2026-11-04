"""Dispatch due ROS simulation reservations to the private virtual satellite.

This path carries draft application PDUs over a local Unix socket. It has no
RF adapter, flight credentials, approval gate, or connection to the C TC gate.
Each reservation is claimed once before any send; an uncertain result is never
retried automatically.
"""

from __future__ import annotations

import socket
import sqlite3
import stat
import struct
import threading
import time
from pathlib import Path

from tools.pado_packet_codec import PacketError, decode, encode_tc

from .ros_workbench import SIM_ALLOWED_COMMANDS


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
                timeout_s: float = 15.0, validation: bool = False, on_packet=None) -> dict:
    """Send one draft TC and return a strictly correlated COMMAND_RESULT body."""
    if socket_path.is_symlink() or not socket_path.is_socket() or not stat.S_ISSOCK(
            socket_path.stat().st_mode):
        raise SimDispatchError("private simulation bridge unavailable")
    issued_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if type(issued_ms) is not int or issued_ms <= 0:
        raise SimDispatchError("invalid simulation issue time")
    packet_catalog = None
    allowed = SIM_ALLOWED_COMMANDS
    if validation:
        from services.validation.corpus import catalog
        packet_catalog = catalog()
        allowed = {entry['name'] for entry in packet_catalog['commands'] if entry['fields'] is not None}
    if reservation.get("command_name") not in allowed:
        raise SimDispatchError("only enabled simulator commands can be sent")
    sequence = reservation["command_sequence"]
    packet = encode_tc(reservation["command_name"], sequence, issued_ms,
                       reservation["parameters"], **({"catalog":packet_catalog} if packet_catalog else {}))
    command = decode(packet, **({"catalog":packet_catalog} if packet_catalog else {}))
    if on_packet:on_packet(packet,'ENCODED')
    if command["kind"] != "TC" or command["operational"] is not False or \
            command["execute_at_utc_ms"] != 0:
        raise SimDispatchError("not an immediate draft TC")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout_s)
        connection.connect(str(socket_path))
        connection.sendall(_LENGTH.pack(len(packet)) + packet)
        if on_packet:on_packet(packet,'SENT')
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
             (fields["reason"] not in (1, 2, 3, 4, 5, 6) or
              fields["executed_at_utc_ms"] != 0)) or
            fields["result"] not in (0, 1)):
        raise SimDispatchError("simulation ACK has inconsistent time or result")
    return {key: fields[key] for key in ("command_id", "command_sequence", "result",
                                          "reason", "executed_at_utc_ms")}


class SimTcDispatcher(threading.Thread):
    """Claim SIM_ONLY reservations and immediate dump requests once."""

    def __init__(self, store, socket_path: Path, *, validation_store=None) -> None:
        super().__init__(name="sim-tc-dispatch", daemon=True)
        self.store = store
        self.socket_path = socket_path
        self.validation_store = validation_store
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()
        self.join(timeout=3)

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                if self.validation_store is not None and self.validation_store.step(self.socket_path):
                    continue
                now_s = int(time.time())
                reservation = self.store.claim_due(now_s) or self.store.claim_sim_tm_request(now_s)
                if reservation is None:
                    self.stop_event.wait(0.5)
                    continue
                finish = (self.store.finish_sim_tm_request
                          if reservation.get("request_kind") == "TM_DUMP"
                          else self.store.finish_sim)
                try:
                    ack = send_sim_tc(self.socket_path, reservation)
                    status = "ACKED" if ack["result"] == 0 else "REJECTED"
                    finish(reservation["id"], status, ack)
                except (socket.timeout, TimeoutError):
                    finish(reservation["id"], "TIMEOUT")
                except (OSError, PacketError, ValueError, KeyError, TypeError):
                    finish(reservation["id"], "SEND_FAILED")
            except (OSError, ValueError, sqlite3.Error):
                # A broken draft store must not become an automatic retry loop.
                self.stop_event.wait(1.0)
