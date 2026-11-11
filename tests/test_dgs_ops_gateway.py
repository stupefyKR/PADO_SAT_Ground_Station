"""The operator gateway must reject peers before opening its portal upstream."""

from __future__ import annotations

import io
import ipaddress
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from deploy import dgs_ops_gateway


class GatewayOperatorAclTests(unittest.TestCase):
    def test_allowed_peer_reaches_only_the_fixed_portal(self) -> None:
        server = SimpleNamespace(operator_cidr=ipaddress.IPv4Network(
            "192.168.45.0/24"), upstream_port=8080)
        upstream = mock.MagicMock()
        upstream.__enter__.return_value = upstream
        with mock.patch.object(dgs_ops_gateway.socket, "create_connection",
                               return_value=upstream) as connect, \
             mock.patch.object(dgs_ops_gateway.select, "select",
                               return_value=([], [], [])):
            dgs_ops_gateway.Relay(mock.MagicMock(),
                                  ("192.168.45.17", 40000), server)
        connect.assert_called_once_with(("portal", 8080), timeout=5)

    def test_disallowed_or_non_ipv4_peer_never_reaches_portal(self) -> None:
        server = SimpleNamespace(operator_cidr=ipaddress.IPv4Network(
            "192.168.45.1/32"), upstream_port=8080)
        for peer in ("192.168.45.2", "127.0.0.1", "::1"):
            with self.subTest(peer=peer), \
                 mock.patch.object(dgs_ops_gateway.socket,
                                   "create_connection") as connect:
                dgs_ops_gateway.Relay(mock.MagicMock(), (peer, 40000), server)
                connect.assert_not_called()

    def test_missing_invalid_or_non_lan_cidr_fails_before_listening(self) -> None:
        for cidr in (None, "192.168.45.1/24", "127.0.0.0/8", "127.0.0.2/32",
                     "192.0.2.1/32", "2001:db8::1/128"):
            with self.subTest(cidr=cidr):
                environment = ({"PADO_GATEWAY_OPERATOR_CIDR": cidr}
                               if cidr is not None else {})
                with mock.patch.dict(os.environ, environment, clear=True), \
                     mock.patch.object(sys, "argv", ["dgs_ops_gateway.py",
                                                    "--listen-port", "8080",
                                                    "--upstream-port", "8080"]), \
                     mock.patch.object(dgs_ops_gateway, "Gateway") as gateway_type, \
                     mock.patch.object(sys, "stderr", io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        dgs_ops_gateway.main()
                    self.assertEqual(raised.exception.code, 2)
                    gateway_type.assert_not_called()

    def test_valid_lan_cidr_is_passed_to_gateway(self) -> None:
        with mock.patch.dict(os.environ, {"PADO_GATEWAY_OPERATOR_CIDR":
                                       "192.168.45.1/32"}, clear=True), \
             mock.patch.object(sys, "argv", ["dgs_ops_gateway.py",
                                            "--listen-port", "8080",
                                            "--upstream-port", "8080"]), \
             mock.patch.object(dgs_ops_gateway, "Gateway") as gateway_type:
            dgs_ops_gateway.main()
        gateway_type.assert_called_once_with(
            ("0.0.0.0", 8080), 8080, ipaddress.IPv4Network("192.168.45.1/32"))
        gateway_type.return_value.__enter__.return_value.serve_forever.assert_called_once()

    def test_operations_listener_keeps_fixed_internal_port_and_cidr(self):
        with mock.patch.dict(os.environ,{'PADO_GATEWAY_OPERATOR_CIDR':'192.168.45.1/32'},clear=True), \
             mock.patch.object(sys,'argv',['gateway','--listen-port','8082','--upstream-port','8080']), \
             mock.patch.object(dgs_ops_gateway,'Gateway') as gateway:
            dgs_ops_gateway.main()
        gateway.assert_called_once_with(('0.0.0.0',8082),8080,ipaddress.IPv4Network('192.168.45.1/32'))

    def test_loopback_listener_preserves_fixed_portal_and_exact_operator(self):
        for port in (8080, 8082):
            with self.subTest(port=port), \
                 mock.patch.dict(os.environ, {'PADO_GATEWAY_OPERATOR_CIDR':'127.0.0.1/32'}, clear=True), \
                 mock.patch.object(sys, 'argv', ['gateway', '--listen-port', str(port), '--upstream-port', '8080']), \
                 mock.patch.object(dgs_ops_gateway, 'Gateway') as gateway:
                dgs_ops_gateway.main()
                gateway.assert_called_once_with(('0.0.0.0', port), 8080, dgs_ops_gateway.LOOPBACK_OPERATOR)

    def test_loopback_acl_allows_only_localhost_and_the_exact_host_gateway(self):
        server = SimpleNamespace(operator_cidr=dgs_ops_gateway.LOOPBACK_OPERATOR,
            host_gateway=ipaddress.IPv4Address('172.29.0.1'), upstream_port=8080)
        for peer, allowed in (('127.0.0.1', True), ('172.29.0.1', True),
                              ('172.29.0.2', False), ('172.29.1.1', False),
                              ('192.168.45.1', False), ('127.0.0.2', False),
                              ('8.8.8.8', False), ('0.0.0.0', False),
                              ('224.0.0.1', False), ('::1', False)):
            with self.subTest(peer=peer), \
                 mock.patch.object(dgs_ops_gateway.socket, 'create_connection') as connect, \
                 mock.patch.object(dgs_ops_gateway.select, 'select', return_value=([], [], [])):
                dgs_ops_gateway.Relay(mock.MagicMock(), (peer, 40000), server)
                if allowed:
                    connect.assert_called_once_with(('portal', 8080), timeout=5)
                else:
                    connect.assert_not_called()

    def test_missing_gateway_keeps_only_exact_localhost_peer(self):
        server = SimpleNamespace(operator_cidr=dgs_ops_gateway.LOOPBACK_OPERATOR,
                                 host_gateway=None, upstream_port=8080)
        for peer, allowed in (('127.0.0.1', True), ('172.29.0.1', False)):
            with self.subTest(peer=peer), \
                 mock.patch.object(dgs_ops_gateway.socket, 'create_connection') as connect, \
                 mock.patch.object(dgs_ops_gateway.select, 'select', return_value=([], [], [])):
                dgs_ops_gateway.Relay(mock.MagicMock(), (peer, 40000), server)
                self.assertEqual(connect.called, allowed)

    def test_route_table_adds_only_a_single_exact_private_default_gateway(self):
        header = 'Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n'
        default = 'eth0 00000000 01001DAC 0003 0 0 0 00000000 0 0 0\n'
        connected = 'eth0 00001DAC 00000000 0001 0 0 0 0000FFFF 0 0 0\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'route'
            path.write_text(header + default + connected, encoding='ascii')
            self.assertEqual(dgs_ops_gateway.default_gateway(path), ipaddress.IPv4Address('172.29.0.1'))
            cases = [header, header + connected, '', default, header + default + default,
                     header + default.replace('01001DAC', '01080808'),
                     header + default.replace('01001DAC', '00000000'),
                     header + default.replace('01001DAC', '010000E0'),
                     header + default.replace('01001DAC', '0100007F'),
                     header + default.replace('01001DAC', '01001DA'),
                     header + default.replace('0003', '0001'),
                     header + default.replace('0003', 'XX'),
                     header + default + 'malformed\n', 'x' * 65537]
            for value in cases:
                with self.subTest(value=value[:100]):
                    path.write_text(value, encoding='ascii')
                    self.assertIsNone(dgs_ops_gateway.default_gateway(path))
            path.unlink()
            self.assertIsNone(dgs_ops_gateway.default_gateway(path))

    def test_only_loopback_mode_discovers_a_host_gateway(self):
        for operator, should_read in ((dgs_ops_gateway.LOOPBACK_OPERATOR, True),
                                      (ipaddress.IPv4Network('192.168.45.1/32'), False), (None, False)):
            with self.subTest(operator=operator), \
                 mock.patch.object(dgs_ops_gateway, 'default_gateway', return_value=ipaddress.IPv4Address('172.29.0.1')) as route, \
                 mock.patch.object(dgs_ops_gateway.socketserver.TCPServer, '__init__'):
                server = dgs_ops_gateway.Gateway(('0.0.0.0',8080), 8080, operator)
                self.assertEqual(route.called, should_read)
                self.assertEqual(server.host_gateway, ipaddress.IPv4Address('172.29.0.1') if should_read else None)


if __name__ == "__main__":
    unittest.main()
