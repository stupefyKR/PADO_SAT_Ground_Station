"""Fixed-upstream TCP gateway for the development operator interface.

The gateway forwards raw bytes to the portal on its private Docker network.
It does not interpret requests or accept a client-selected destination, so
HTTP headers, cookies, CSRF tokens and the optional TLS stream stay intact.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path
import re
import select
import socket
import socketserver
import threading


UPSTREAM_HOST = "portal"
MAX_CONNECTIONS = 64
IDLE_TIMEOUT_SECONDS = 90
LOOPBACK_OPERATOR = ipaddress.IPv4Network("127.0.0.1/32")
PRIVATE_BLOCKS = tuple(ipaddress.IPv4Network(cidr) for cidr in
                      ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def default_gateway(route_path: Path = Path("/proc/net/route")) -> ipaddress.IPv4Address | None:
    """Read the single exact private host gateway; malformed routes add no peer.

    Docker may preserve a localhost source or proxy it through this address.
    Never authorize the bridge subnet or a destination supplied by the client.
    """
    try:
        with route_path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            return None
        lines = raw.decode("ascii").splitlines()
        header = "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT".split()
        if not lines or lines[0].split() != header or len(lines) > 257:
            return None
        gateways = []
        for line in lines[1:]:
            if not line.strip():
                continue
            fields = line.split()
            if (len(fields) != 11 or not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", fields[0]) or
                    any(not re.fullmatch(r"[0-9A-Fa-f]{8}", fields[index]) for index in (1, 2, 7)) or
                    not re.fullmatch(r"[0-9A-Fa-f]{1,4}", fields[3]) or
                    any(not re.fullmatch(r"[0-9]+", fields[index]) for index in (4, 5, 6, 8, 9, 10))):
                return None
            if fields[1] != "00000000" or fields[7] != "00000000":
                continue
            flags = int(fields[3], 16)
            if (flags & 3) != 3:
                return None
            address = ipaddress.IPv4Address(bytes.fromhex(fields[2])[::-1])
            if not any(address in block for block in PRIVATE_BLOCKS):
                return None
            gateways.append(address)
        return gateways[0] if len(gateways) == 1 else None
    except (OSError, UnicodeError, ValueError):
        return None


class Gateway(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False
    request_queue_size = 32

    def __init__(self, address: tuple[str, int], upstream_port: int,
                 operator_cidr: ipaddress.IPv4Network | None):
        self.upstream_port = upstream_port
        self.operator_cidr = operator_cidr
        self.host_gateway = default_gateway() if operator_cidr == LOOPBACK_OPERATOR else None
        self.connection_slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(address, Relay)

    def process_request(self, request: socket.socket, client_address: tuple) -> None:
        if not self.connection_slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.connection_slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: tuple) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connection_slots.release()


class Relay(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: Gateway = self.server  # type: ignore[assignment]
        if server.operator_cidr is not None:
            try:
                peer_ip = ipaddress.IPv4Address(self.client_address[0])
            except ipaddress.AddressValueError:
                return
            if peer_ip not in server.operator_cidr and peer_ip != getattr(server, "host_gateway", None):
                return
        try:
            upstream = socket.create_connection(
                (UPSTREAM_HOST, server.upstream_port), timeout=5
            )
        except OSError:
            return
        with upstream:
            client = self.request
            client.settimeout(IDLE_TIMEOUT_SECONDS)
            upstream.settimeout(IDLE_TIMEOUT_SECONDS)
            opposite = {client: upstream, upstream: client}
            readers = set(opposite)
            while readers:
                ready, _, _ = select.select(tuple(readers), (), (), IDLE_TIMEOUT_SECONDS)
                if not ready:
                    return
                for source in ready:
                    try:
                        data = source.recv(65536)
                        if data:
                            opposite[source].sendall(data)
                        else:
                            readers.remove(source)
                            opposite[source].shutdown(socket.SHUT_WR)
                    except OSError:
                        return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--upstream-port", type=int, required=True)
    args = parser.parse_args()
    if (args.listen_port,args.upstream_port) not in ((8080,8080),(8082,8080),(8443,8443)):
        parser.error("only the fixed validation, operations or TLS portal endpoint is supported")
    cidr_text = os.environ.get("PADO_GATEWAY_OPERATOR_CIDR")
    if args.listen_port in (8080,8082):
        if not cidr_text:
            parser.error("local gateway requires PADO_GATEWAY_OPERATOR_CIDR")
        try:
            operator_cidr = ipaddress.IPv4Network(cidr_text, strict=True)
        except ValueError as exc:
            parser.error(f"invalid operator CIDR: {exc}")
        if operator_cidr != LOOPBACK_OPERATOR and not any(
                operator_cidr.subnet_of(block) for block in PRIVATE_BLOCKS):
            parser.error("operator CIDR must be 127.0.0.1/32 or within a private IPv4 range")
    else:
        if cidr_text:
            parser.error("development loopback gateway does not use a LAN operator CIDR")
        operator_cidr = None
    with Gateway(("0.0.0.0", args.listen_port), args.upstream_port,
                 operator_cidr) as server:
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
