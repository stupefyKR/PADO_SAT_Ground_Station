"""Keep the local web stack behind the DGS boundary as it evolves."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from deploy.stage9 import check_compose_policy


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "stage9"


def check_private_probe(config):
    """The Stage 7 N2 probe has no operator or external attachment."""
    networks = config["networks"]
    for name, network in networks.items():
        assert network.get("internal") is True, f"{name}: probe network is external"
        assert network.get("enable_ipv6") is False, f"{name}: probe IPv6 path is unfiltered"
        assert network.get("driver_opts", {}).get(
            "com.docker.network.bridge.gateway_mode_ipv4") == "isolated", (
                f"{name}: probe bridge is not isolated")
    for name, service in config["services"].items():
        assert not service.get("ports"), f"{name}: probe published a port"
        assert service.get("network_mode") in (None, "none"), (
            f"{name}: probe escaped bridge isolation")
        assert service.get("network_mode") == "none" or service.get("networks"), (
            f"{name}: probe joined implicit default network")
        for network in service.get("networks", {}):
            assert network in networks, f"{name}: probe attached to unknown network"


class DgsBoundaryInvariantTests(unittest.TestCase):
    def setUp(self):
        self.good = {
            "networks": {"entry": {"driver": "bridge", "internal": False,
                                    "enable_ipv6": False,
                                    "driver_opts": {"com.docker.network.bridge.name":
                                                    "br-pado-local",
                                                    "com.docker.network.bridge.gateway_mode_ipv4":
                                                    "nat"}},
                         "dgs_portal": {"driver": "bridge", "internal": True,
                                        "enable_ipv6": False,
                                        "driver_opts": {
                                            "com.docker.network.bridge.gateway_mode_ipv4":
                                                "isolated"}},
                         "portal_ros": {"driver": "bridge", "internal": True,
                                        "enable_ipv6": False,
                                        "driver_opts": {
                                            "com.docker.network.bridge.gateway_mode_ipv4":
                                                "isolated"}},
                         "portal_mps": {"driver": "bridge", "internal": True,
                                        "enable_ipv6": False,
                                        "driver_opts": {
                                            "com.docker.network.bridge.gateway_mode_ipv4":
                                                "isolated"}}},
            "services": {
                "dgs-ops-gateway": {"networks": {"entry": None, "dgs_portal": None},
                                "ports": [{"host_ip": "192.168.45.128",
                                           "published": "8080", "target": 8080,
                                           "protocol": "tcp"}],
                                "read_only": True, "cap_drop": ["ALL"],
                                "security_opt": ["no-new-privileges:true"],
                                "environment": {"PADO_GATEWAY_OPERATOR_CIDR":
                                                "192.168.45.1/32"},
                                "command": ["python3", "/app/deploy/stage9/dgs_ops_gateway.py",
                                            "--listen-port", "8080", "--upstream-port",
                                            "8080"]},
                "portal": {"networks": {"dgs_portal": None, "portal_ros": None,
                                        "portal_mps": None},
                           "command": ["python3", "portal", "--ros-url",
                                       "http://ros-api:8081", "--mps-url",
                                       "http://mps-api:8081"]},
                "ros-api": {"networks": {"portal_ros": None}},
                "mps-api": {"networks": {"portal_mps": None}},
                "ros-core-read": {"network_mode": "none"},
            },
        }
        for service in self.good["services"].values():
            service.setdefault("cap_drop", ["ALL"])
            service.setdefault("security_opt", ["no-new-privileges:true"])

    def test_dgs_gateway_is_the_only_external_boundary(self):
        check_compose_policy.check(self.good, "192.168.45.128", 8080,
                                   "br-pado-local")
        cases = (
            (lambda c: c["services"]["portal"]["networks"].update({"entry": None}),
             "only DGS may join entry"),
            (lambda c: c["services"]["ros-api"].update(
                {"ports": [{"host_ip": "127.0.0.1", "published": "8081", "target": 8081}]}),
             "only DGS may publish"),
            (lambda c: c["networks"]["portal_ros"].update({"internal": False}),
             "subsystem network must be isolated"),
            (lambda c: c["services"]["ros-api"].update({"network_mode": "host"}),
             "only explicit Compose networks"),
            (lambda c: c["services"]["dgs-ops-gateway"]["ports"][0].update(
                {"host_ip": "0.0.0.0"}),
             "published port differs"),
            (lambda c: c["services"]["portal"]["networks"].pop("dgs_portal"),
             "DGS-to-portal network must have exactly two peers"),
            (lambda c: c["networks"]["entry"]["driver_opts"].update(
                {"com.docker.network.bridge.name": "br-unfiltered"}),
             "firewall-pinned interface name"),
            (lambda c: c["services"]["dgs-ops-gateway"].update(
                {"extra_hosts": ["portal:192.0.2.1"]}),
             "Docker service discovery"),
            (lambda c: c["services"]["dgs-ops-gateway"]["command"].extend(
                ["--upstream-host", "192.0.2.1"]),
             "fixed portal endpoint"),
            (lambda c: c["services"]["dgs-ops-gateway"]["environment"].update(
                {"PADO_GATEWAY_OPERATOR_CIDR": "192.168.45.0/24"}),
             "operator CIDR differs"),
            (lambda c: c["services"]["dgs-ops-gateway"]["environment"].pop(
                "PADO_GATEWAY_OPERATOR_CIDR"),
             "operator CIDR differs"),
        )
        for corrupt, policy_message in cases:
            with self.subTest(expected=policy_message):
                broken = copy.deepcopy(self.good)
                corrupt(broken)
                with self.assertRaisesRegex(ValueError, policy_message):
                    check_compose_policy.check(broken, "192.168.45.128", 8080,
                                               "br-pado-local")

    def test_actual_bridge_membership_is_checked_before_start(self):
        project = "pado-stage9-local"
        networks = []
        for name in ("entry", "dgs_portal", "portal_ros", "portal_mps", "sim_tm"):
            entry = name == "entry"
            networks.append({
                "Name": f"{project}_{name}", "Driver": "bridge",
                "Internal": not entry, "EnableIPv6": False,
                "Options": {"com.docker.network.bridge.name": "br-pado-local",
                            "com.docker.network.bridge.gateway_mode_ipv4": "nat"}
                if entry else {
                    "com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
                "Containers": {},
            })
        check_compose_policy.check_actual_networks(networks, project, "br-pado-local")
        with self.assertRaisesRegex(ValueError, "expected running service is absent"):
            check_compose_policy.check_actual_networks(networks, project,
                                                        "br-pado-local", True)
        expected_members = {
            "entry": ("dgs-ops-gateway",),
            "dgs_portal": ("dgs-ops-gateway", "portal"),
            "portal_ros": ("portal", "ros-api"),
            "portal_mps": ("portal", "mps-api"),
            "sim_tm": ("sim-ground", "sim-satellite"),
        }
        running = copy.deepcopy(networks)
        for network in running:
            name = network["Name"].removeprefix(f"{project}_")
            network["Containers"] = {
                str(index): {"Name": f"{project}-{service}-1"}
                for index, service in enumerate(expected_members[name])}
        check_compose_policy.check_actual_networks(running, project,
                                                    "br-pado-local", True)
        missing = copy.deepcopy(running)
        missing[0]["Containers"] = {}
        with self.assertRaisesRegex(ValueError, "expected running service is absent"):
            check_compose_policy.check_actual_networks(missing, project,
                                                        "br-pado-local", True)
        attached = copy.deepcopy(networks)
        attached[0]["Containers"] = {
            "unexpected": {"Name": "untrusted-bypass-1"}}
        with self.assertRaisesRegex(ValueError, "unexpected container"):
            check_compose_policy.check_actual_networks(attached, project,
                                                        "br-pado-local")
        unisolated = copy.deepcopy(networks)
        unisolated[2]["Options"]["com.docker.network.bridge.gateway_mode_ipv4"] = "nat"
        with self.assertRaisesRegex(ValueError, "not isolated"):
            check_compose_policy.check_actual_networks(unisolated, project,
                                                        "br-pado-local")

    def test_container_privilege_and_namespace_bypasses_are_rejected(self):
        mutations = (
            ("host PID namespace", {"pid": "host"}),
            ("added network capability", {"cap_add": ["NET_ADMIN"]}),
            ("host device", {"devices": ["/dev/net/tun:/dev/net/tun"]}),
            ("Docker socket", {"volumes": [
                "/var/run/docker.sock:/var/run/docker.sock"]}),
            ("shared DGS network namespace", {
                "network_mode": "service:dgs-ops-gateway"}),
        )
        for label, extra in mutations:
            with self.subTest(bypass=label):
                broken = copy.deepcopy(self.good)
                broken["services"]["ros-api"].update(extra)
                with self.assertRaises(ValueError):
                    check_compose_policy.check(broken, "192.168.45.128", 8080,
                                               "br-pado-local")

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose is required")
    def test_real_compose_files_keep_dgs_boundary(self):
        for file, host, port, bridge in (
            ("compose.local.yaml", "192.168.45.128", 8080, "br-pado-local"),
            ("compose.dev.yaml", "127.0.0.1", 8443, "br-pado-dev")):
            with self.subTest(file=file):
                env = dict(os.environ, PADO_LOCAL_BIND_IP="192.168.45.128",
                           PADO_STAGE9_CERT_DIR="/tmp",
                           PADO_OPERATOR_CIDR="192.168.45.1/32")
                result = subprocess.run(
                    ["docker", "compose", "-f", str(COMPOSE / file),
                     "config", "--format", "json"], cwd=ROOT, env=env,
                    text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                config = json.loads(result.stdout)
                check_compose_policy.check(config, host, port, bridge)

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose is required")
    def test_stage7_probe_remains_private(self):
        result = subprocess.run(
            ["docker", "compose", "-f", str(ROOT / "deploy" / "stage7" /
                                         "compose.probe.yaml"), "config", "--format", "json"],
            cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        check_private_probe(json.loads(result.stdout))


if __name__ == "__main__":
    unittest.main()
