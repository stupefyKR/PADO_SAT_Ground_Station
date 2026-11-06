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


def approved_volume_fixture(document, service_mounts):
    """Add approved named volumes and the local shared settings directory."""
    document["volumes"] = {source: {} for mounts in service_mounts.values()
                           for source, _, _ in mounts}
    for name, mounts in service_mounts.items():
        document["services"][name]["volumes"] = [
            {"type": "volume", "source": source, "target": target,
             "read_only": read_only}
            for source, target, read_only in sorted(mounts)]
    if service_mounts is check_compose_policy.LOCAL_VOLUMES:
        for name, read_only in check_compose_policy.LOCAL_SETTINGS_MOUNTS.items():
            service = document["services"][name]
            service["volumes"].append({
                "type": "bind", "source": str(ROOT / "config"), "target": "/config",
                "read_only": read_only, "bind": {"create_host_path": False}})
            service.setdefault("environment", {})["PADO_SPACECRAFT_CONFIG"] = "/config/spacecraft.json"
        document["services"]["ros-api"]["group_add"] = ["1000"]


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
                                                 "isolated"}},
                         "sim_tm": {"driver": "bridge", "internal": True,
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
                "mps-core-read": {"network_mode": "none"},
                "init-token": {"network_mode": "none"},
                "fds-synthetic-demo": {"network_mode": "none"},
                "fds-iss-display": {"network_mode": "none"},
                "sim-volume-init": {
                    "network_mode": "none", "user": "0:0", "read_only": True,
                    "cap_add": ["CHOWN"],
                    "command": check_compose_policy.SIM_VOLUME_INIT_COMMAND.copy(),
                },
                "sim-ground": {"networks": {"sim_tm": None}},
                "sim-satellite": {"networks": {"sim_tm": None}},
            },
        }
        for service in self.good["services"].values():
            service.setdefault("cap_drop", ["ALL"])
            service.setdefault("security_opt", ["no-new-privileges:true"])
        approved_volume_fixture(self.good, check_compose_policy.LOCAL_VOLUMES)

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
            (lambda c: c["services"].update({"unreviewed-helper": {
                "network_mode": "none"}}),
             "service set differs"),
            (lambda c: c["networks"].update({"unreviewed_bridge": {
                "driver": "bridge", "internal": True}}),
             "network set differs"),
            (lambda c: c["services"]["ros-api"]["networks"].update(
                {"portal_mps": None}),
             "portal_mps: unexpected network membership"),
            (lambda c: c["services"]["sim-ground"]["networks"].update(
                {"portal_ros": None}),
             "portal_ros: unexpected network membership"),
        )
        for corrupt, policy_message in cases:
            with self.subTest(expected=policy_message):
                broken = copy.deepcopy(self.good)
                corrupt(broken)
                with self.assertRaisesRegex(ValueError, policy_message):
                    check_compose_policy.check(broken, "192.168.45.128", 8080,
                                               "br-pado-local")

    def test_volume_init_cannot_traverse_private_run_data(self):
        for change in (
            lambda service: service["command"].insert(1, "-R"),
            lambda service: service["command"].append("/unapproved"),
        ):
            with self.subTest(change=change):
                broken = copy.deepcopy(self.good)
                change(broken["services"]["sim-volume-init"])
                with self.assertRaisesRegex(ValueError, "without recursion"):
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

    def test_local_stack_rejects_host_and_indirect_mounts(self):
        mutations = (
            {"volumes": [{"type": "bind", "source": "/etc",
                          "target": "/host", "read_only": True}]},
            {"volumes": [{"type": "bind", "source": "/var/run/docker.sock",
                          "target": "/run/docker.sock", "read_only": True}]},
            {"volumes_from": ["dgs-ops-gateway"]},
            {"configs": [{"source": "host_config", "target": "/host"}]},
        )
        for extra in mutations:
            with self.subTest(extra=extra):
                broken = copy.deepcopy(self.good)
                broken["services"]["ros-api"].update(extra)
                with self.assertRaises(ValueError):
                    check_compose_policy.check(broken, "192.168.45.128", 8080,
                                               "br-pado-local")

    def test_shared_settings_bind_is_exact_and_only_ros_can_write(self):
        for name, change in (
                ("ros-api", {"source": str(ROOT)}),
                ("ros-api", {"source": str(ROOT / "config" / "spacecraft.json")}),
                ("ros-api", {"target": "/alternate"}),
                ("ros-api", {"read_only": True}),
                ("sim-ground", {"read_only": False}),
                ("sim-satellite", {"bind": {"create_host_path": True}})):
            with self.subTest(service=name, change=change):
                broken = copy.deepcopy(self.good)
                mount = next(m for m in broken["services"][name]["volumes"]
                             if m["type"] == "bind")
                mount.update(change)
                with self.assertRaisesRegex(ValueError, "unapproved settings host bind"):
                    check_compose_policy.check(broken, "192.168.45.128", 8080,
                                               "br-pado-local")

    def test_settings_bind_accepts_compose_normalized_false_defaults(self):
        for options in (None, {}, {'create_host_path':False}):
            with self.subTest(bind=options):
                document=copy.deepcopy(self.good)
                for name in check_compose_policy.LOCAL_SETTINGS_MOUNTS:
                    mount=next(m for m in document['services'][name]['volumes'] if m['type']=='bind')
                    if options is None:mount.pop('bind')
                    else:mount['bind']=options.copy()
                check_compose_policy.check(document,'192.168.45.128',8080,'br-pado-local')
        for value in (True,None,0,1,'false',[]):
            with self.subTest(create_host_path=value):
                document=copy.deepcopy(self.good)
                mount=next(m for m in document['services']['ros-api']['volumes'] if m['type']=='bind')
                mount['bind']={'create_host_path':value}
                with self.assertRaisesRegex(ValueError,'unapproved settings host bind'):
                    check_compose_policy.check(document,'192.168.45.128',8080,'br-pado-local')
        for groups in ([], ["root"], ["1000", "0"]):
            with self.subTest(groups=groups):
                broken = copy.deepcopy(self.good)
                broken["services"]["ros-api"]["group_add"] = groups
                with self.assertRaisesRegex(ValueError, "numeric operator group"):
                    check_compose_policy.check(broken, "192.168.45.128", 8080,
                                               "br-pado-local")
        for volume in ({"external": True},
                       {"driver": "local", "driver_opts": {
                           "type": "none", "device": "/etc", "o": "bind"}}):
            with self.subTest(volume=volume):
                broken = copy.deepcopy(self.good)
                broken["volumes"]["ros_drafts"] = volume
                with self.assertRaisesRegex(ValueError, "host-backed volume forbidden"):
                    check_compose_policy.check(broken, "192.168.45.128", 8080,
                                               "br-pado-local")

    def test_named_volumes_cannot_cross_subsystem_boundaries(self):
        for name, source, target in (
                ("sim-satellite", "ros_drafts", "/stolen-ros-drafts"),
                ("mps-api", "ros_tm_preview", "/unapproved-tm"),
                ("dgs-ops-gateway", "internal_token", "/internal")):
            with self.subTest(service=name, source=source):
                broken = copy.deepcopy(self.good)
                broken["services"][name]["volumes"].append({
                    "type": "volume", "source": source, "target": target,
                    "read_only": True})
                with self.assertRaisesRegex(ValueError, "named volume mount differs"):
                    check_compose_policy.check(broken, "192.168.45.128", 8080,
                                               "br-pado-local")
        for change in ({"source": "mps_candidates"}, {"target": "/alternate"},
                       {"read_only": False}):
            with self.subTest(change=change):
                broken = copy.deepcopy(self.good)
                mount = next(item for item in broken["services"]["ros-api"]["volumes"]
                             if item["source"] == "ros_core_socket")
                mount.update(change)
                with self.assertRaisesRegex(ValueError, "named volume mount differs"):
                    check_compose_policy.check(broken, "192.168.45.128", 8080,
                                               "br-pado-local")
        duplicate = copy.deepcopy(self.good)
        duplicate["services"]["ros-api"]["volumes"].append({
            "type": "volume", "source": "mps_candidates", "target": "/state"})
        with self.assertRaisesRegex(ValueError, "duplicate container mount target"):
            check_compose_policy.check(duplicate, "192.168.45.128", 8080,
                                       "br-pado-local")

    def test_named_volumes_resolve_within_the_compose_project(self):
        normalized = copy.deepcopy(self.good)
        normalized["name"] = "pado-stage9-local"
        for name, volume in normalized["volumes"].items():
            volume["name"] = f"pado-stage9-local_{name}"
        check_compose_policy.check(normalized, "192.168.45.128", 8080,
                                   "br-pado-local", project="pado-stage9-local")
        normalized["volumes"]["ros_drafts"]["name"] = "other_project_secret"
        with self.assertRaisesRegex(ValueError, "escapes the Compose project"):
            check_compose_policy.check(normalized, "192.168.45.128", 8080,
                                       "br-pado-local", project="pado-stage9-local")
        normalized["volumes"]["ros_drafts"]["name"] = "pado-stage9-local_ros_drafts"
        normalized["name"] = "different-project"
        with self.assertRaisesRegex(ValueError, "project name differs"):
            check_compose_policy.check(normalized, "192.168.45.128", 8080,
                                       "br-pado-local", project="pado-stage9-local")

    def test_shared_service_and_container_namespaces_are_forbidden(self):
        for field, value in (("pid", "service:ros-api"),
                             ("ipc", "service:ros-api"),
                             ("uts", "container:other"),
                             ("userns_mode", "host"),
                             ("cgroup", "host")):
            with self.subTest(field=field):
                broken = copy.deepcopy(self.good)
                broken["services"]["mps-api"][field] = value
                with self.assertRaisesRegex(ValueError, "explicit " + field):
                    check_compose_policy.check(broken, "192.168.45.128", 8080,
                                               "br-pado-local")

    def test_development_bind_mounts_are_read_only_and_target_limited(self):
        dev = copy.deepcopy(self.good)
        for name in check_compose_policy.LOCAL_SERVICES - check_compose_policy.COMMON_SERVICES:
            del dev["services"][name]
        del dev["networks"]["sim_tm"]
        approved_volume_fixture(dev, check_compose_policy.DEV_VOLUMES)
        gateway = dev["services"]["dgs-ops-gateway"]
        gateway["ports"] = [{"host_ip": "127.0.0.1", "published": "8443",
                             "target": 8443, "protocol": "tcp"}]
        gateway["command"] = ["python3", "/app/deploy/stage9/dgs_ops_gateway.py",
                              "--listen-port", "8443", "--upstream-port", "8443"]
        gateway["environment"] = {}
        dev["networks"]["entry"]["driver_opts"][
            "com.docker.network.bridge.name"] = "br-pado-dev"
        dev["services"]["portal"]["command"] = [
            "python3", "portal", "--ros-url", "https://ros-api:8443",
            "--mps-url", "https://mps-api:8443"]
        for name, targets in check_compose_policy.DEV_BIND_TARGETS.items():
            dev["services"][name]["volumes"].extend({
                "type": "bind", "source": "/tmp/pado-certs" if target == "/certs"
                else "/tmp/pado-data", "target": target, "read_only": True}
                for target in targets)
        dev["name"] = "pado-stage9-dev"
        for name, volume in dev["volumes"].items():
            volume["name"] = f"pado-stage9-dev_{name}"
        check_compose_policy.check(dev, "127.0.0.1", 8443, "br-pado-dev")
        duplicate = copy.deepcopy(dev)
        duplicate["services"]["portal"]["volumes"].append({
            "type": "bind", "source": "/tmp/other", "target": "/state",
            "read_only": True})
        with self.assertRaisesRegex(ValueError, "duplicate container mount target"):
            check_compose_policy.check(duplicate, "127.0.0.1", 8443,
                                       "br-pado-dev")
        for bad_mount in (
            {"type": "bind", "source": "/tmp/pado-certs", "target": "/certs",
             "read_only": False},
            {"type": "bind", "source": "/tmp/other", "target": "/host",
             "read_only": True},
        ):
            with self.subTest(bad_mount=bad_mount):
                broken = copy.deepcopy(dev)
                broken["services"]["portal"]["volumes"] = [
                    mount for mount in broken["services"]["portal"]["volumes"]
                    if mount["target"] != bad_mount["target"] and
                    mount["target"] != "/certs"] + [bad_mount]
                with self.assertRaisesRegex(ValueError, "unapproved or writable"):
                    check_compose_policy.check(broken, "127.0.0.1", 8443,
                                               "br-pado-dev")

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose is required")
    def test_real_compose_files_keep_dgs_boundary(self):
        for file, host, port, bridge in (
            ("compose.local.yaml", "192.168.45.128", 8080, "br-pado-local"),
            ("compose.dev.yaml", "127.0.0.1", 8443, "br-pado-dev")):
            with self.subTest(file=file):
                project = "pado-stage9-local" if file == "compose.local.yaml" else None
                env = dict(os.environ, PADO_LOCAL_BIND_IP="192.168.45.128",
                           PADO_CONFIG_GID=str(os.getgid()) if hasattr(os, "getgid") else "1000",
                           PADO_STAGE9_CERT_DIR="/tmp",
                           PADO_OPERATOR_CIDR="192.168.45.1/32")
                result = subprocess.run(
                    ["docker", "compose", *(["-p", project] if project else []),
                     "-f", str(COMPOSE / file),
                     "config", "--format", "json"], cwd=ROOT, env=env,
                    text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                config = json.loads(result.stdout)
                check_compose_policy.check(config, host, port, bridge,
                                           project=project)

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
