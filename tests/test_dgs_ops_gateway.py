"""The operator gateway must reject peers before opening its portal upstream."""

from __future__ import annotations

import io
import ipaddress
import os
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from deploy.stage9 import dgs_ops_gateway


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
        for cidr in (None, "192.168.45.1/24", "127.0.0.1/32",
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


if __name__ == "__main__":
    unittest.main()
