"""Reject a local Compose topology that crosses the DGS external boundary."""

from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit


GATEWAY = "dgs-ops-gateway"
PORTAL = "portal"
COMMON_SERVICES = {GATEWAY, PORTAL, "ros-api", "mps-api",
                   "ros-core-read", "mps-core-read"}
LOCAL_SERVICES = COMMON_SERVICES | {"init-token", "fds-synthetic-demo", "fds-iss-display",
                                    "sim-volume-init", "sim-ground", "sim-satellite"}
NETWORK_MEMBERS = {
    "entry": {GATEWAY},
    "dgs_portal": {GATEWAY, PORTAL},
    "portal_ros": {PORTAL, "ros-api"},
    "portal_mps": {PORTAL, "mps-api"},
    "sim_tm": {"sim-ground", "sim-satellite"},
}
# This one operator-managed directory is shared for atomic settings updates.
# ROS alone may write it; simulation consumers may only read it.
LOCAL_SETTINGS_MOUNTS = {"ros-api": False, "sim-ground": True, "sim-satellite": True}
LOCAL_SETTINGS_SOURCE = Path(__file__).resolve().parents[2] / "config"
# A service's named volumes are part of its isolation boundary. Checking only
# that a volume is declared would let an unrelated service mount ROS's draft DB
# (or the shared internal token) while all network checks still pass.
# Tuple order is source, container target, read-only.
LOCAL_VOLUMES = {
    "init-token": {("internal_token", "/internal", False)},
    "dgs-ops-gateway": set(),
    "portal": {("internal_token", "/internal", True),
               ("portal_state", "/state", False),
               ("fds_view", "/fds-view", True),
               ("fds_iss_view", "/fds-iss-view", True)},
    "ros-api": {("internal_token", "/internal", True),
                ("ros_core_socket", "/run/core", True),
                ("ros_drafts", "/state", False),
                ("ros_tm_preview", "/tm-preview", True),
                ("sim_tc_socket", "/run/sim-tc", True)},
    "mps-api": {("internal_token", "/internal", True),
                ("mps_core_socket", "/run/core", True),
                ("mps_candidates", "/state", False)},
    "ros-core-read": {("ros_core_socket", "/run/core", False),
                      ("ros_state", "/state", True)},
    "mps-core-read": {("mps_core_socket", "/run/core", False),
                      ("mps_schedules", "/mps-schedules", True)},
    "fds-synthetic-demo": {("fds_runs", "/app/out", False),
                           ("fds_view", "/app/out/fds-view", False),
                           ("fds_truth", "/app/out/fds-truth", False),
                           ("mps_schedules", "/app/out/mps-schedules", False),
                           ("sim_n11_inbox", "/n11-inbox", True)},
    "fds-iss-display": {("fds_iss_runs", "/app/out/iss-display", False),
                        ("fds_iss_view", "/app/out/fds-iss-view", False)},
    "sim-volume-init": {("ros_tm_preview", "/tm-preview", False),
                        ("sim_n11_inbox", "/n11-inbox", False),
                        ("fds_runs", "/fds-runs", False),
                        ("fds_view", "/fds-view", False),
                        ("fds_truth", "/fds-truth", False),
                        ("mps_schedules", "/mps-schedules", False),
                        ("sim_tc_socket", "/sim-tc", False),
                        ("sim_tm_spool", "/sim-spool", False),
                        ("fds_iss_runs", "/iss-runs", False),
                        ("fds_iss_view", "/iss-view", False)},
    "sim-ground": {("ros_tm_preview", "/tm-preview", False),
                   ("sim_n11_inbox", "/n11-inbox", False),
                   ("sim_tc_socket", "/run/sim-tc", False),
                   ("fds_truth", "/fds-view", True)},
    "sim-satellite": {("fds_truth", "/fds-view", True),
                      ("sim_tm_spool", "/state", False)},
}
SIM_VOLUME_INIT_COMMAND = [
    "chown", "10001:10001", "/tm-preview", "/n11-inbox", "/fds-runs",
    "/fds-view", "/fds-truth", "/mps-schedules", "/sim-tc", "/sim-spool",
    "/iss-runs", "/iss-view",
]
DEV_VOLUMES = {
    "dgs-ops-gateway": set(),
    "portal": {("portal_state", "/state", False)},
    "ros-api": {("ros_core_socket", "/run/core", True),
                ("ros_drafts", "/state", False)},
    "mps-api": {("mps_core_socket", "/run/core", True),
                ("mps_candidates", "/state", False)},
    "ros-core-read": {("ros_core_socket", "/run/core", False),
                      ("ros_state", "/state", True)},
    "mps-core-read": {("mps_core_socket", "/run/core", False)},
}
DEV_BIND_TARGETS = {
    "portal": {"/certs", "/fds-view"},
    "ros-api": {"/certs", "/tm-preview"},
    "mps-api": {"/certs"},
    "mps-core-read": {"/mps-schedules"},
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def check(document: dict, bind_ip: str, port: int, bridge: str,
          operator_cidr: str = "192.168.45.1/32",
          project: str | None = None) -> None:
    services = document.get("services")
    networks = document.get("networks")
    require(isinstance(services, dict) and isinstance(networks, dict),
            "Compose services and networks are required")
    local = port == 8080
    expected_services = LOCAL_SERVICES if local else COMMON_SERVICES
    expected_networks = set(NETWORK_MEMBERS) if local else set(NETWORK_MEMBERS) - {"sim_tm"}
    expected_volumes = LOCAL_VOLUMES if local else DEV_VOLUMES
    require(set(services) == expected_services,
            "Compose service set differs from approved services")
    require(set(networks) == expected_networks,
            "Compose network set differs from approved networks")

    # A named volume backed by a host bind is still a host mount. Do not let
    # volume driver options or external volumes bypass the service mount check.
    volumes = document.get("volumes") or {}
    require(isinstance(volumes, dict), "Compose volumes must be a mapping")
    document_project = document.get("name")
    if local:
        require(project in (None, "pado-stage9-local") and
                document_project in (None, "pado-stage9-local") and
                (project is None or document_project == project),
                "local Compose project name differs from launcher")
        project = "pado-stage9-local"
    else:
        require(project is None or document_project == project,
                "Compose project name differs from expected project")
        project = document_project
    require(set(volumes) == {source for mounts in expected_volumes.values()
                             for source, _, _ in mounts},
            "Compose named volume set differs from approved volumes")
    for name, volume in volumes.items():
        require(isinstance(volume, dict) and not volume.get("external") and
                not volume.get("driver_opts") and volume.get("driver") in (None, "local"),
                f"{name}: external or host-backed volume forbidden")
        configured_name = volume.get("name")
        require(configured_name is None or
                (isinstance(project, str) and bool(project) and
                 configured_name == f"{project}_{name}"),
                f"{name}: named volume escapes the Compose project")

    entry = networks["entry"]
    require(entry.get("driver") == "bridge" and entry.get("internal") is not True,
            "only the DGS entry network may cross the Docker boundary")
    require(entry.get("external") is not True,
            "the DGS entry network must be managed by this Compose project")
    require(entry.get("enable_ipv6") is False,
            "the DGS entry network must disable IPv6 until IPv6 policy exists")
    require(entry.get("driver_opts", {}).get("com.docker.network.bridge.name") == bridge,
            "the DGS entry bridge must use the firewall-pinned interface name")
    require(entry.get("driver_opts", {}).get(
        "com.docker.network.bridge.gateway_mode_ipv4") == "nat",
        "the DGS entry bridge must use the filtered nat gateway mode")
    for name, network in networks.items():
        if name != "entry":
            require(network.get("driver") == "bridge" and
                    network.get("internal") is True and
                    network.get("external") is not True and
                    network.get("enable_ipv6") is False and
                    network.get("driver_opts", {}).get(
                        "com.docker.network.bridge.gateway_mode_ipv4") == "isolated",
                    f"{name}: subsystem network must be isolated, internal and IPv4-only")

    members = {name: set() for name in networks}
    for name, service in services.items():
        require(service.get("network_mode") in (None, "none"),
                f"{name}: only explicit Compose networks or network_mode none are allowed")
        require(service.get("privileged") is not True, f"{name}: privileged forbidden")
        require("ALL" in (service.get("cap_drop") or []) and
                "no-new-privileges:true" in (service.get("security_opt") or []),
                f"{name}: all capabilities must be dropped and new privileges denied")
        for namespace in ("pid", "ipc", "uts", "userns_mode", "cgroup"):
            require(service.get(namespace) is None,
                    f"{name}: explicit {namespace} namespace forbidden")
        require(not service.get("devices") and not service.get("device_cgroup_rules"),
                f"{name}: host devices forbidden")
        added_caps = service.get("cap_add") or []
        require(not added_caps or (name == "sim-volume-init" and
                                   service.get("network_mode") == "none" and
                                   added_caps == ["CHOWN"]),
                f"{name}: added capabilities forbidden")
        require(not service.get("volumes_from") and not service.get("configs") and
                not service.get("secrets"),
                f"{name}: indirect host mounts forbidden")
        mounts = service.get("volumes") or []
        require(isinstance(mounts, list), f"{name}: volumes must be a list")
        named_mounts = set()
        bind_targets = set()
        all_targets = set()
        for mount in mounts:
            require(isinstance(mount, dict),
                    f"{name}: only normalized Compose mounts are allowed")
            kind = mount.get("type")
            target = mount.get("target")
            source = mount.get("source")
            require(isinstance(target, str) and target.startswith("/") and
                    isinstance(source, str) and source,
                    f"{name}: mount source and target are required")
            require(target not in all_targets,
                    f"{name}: duplicate container mount target")
            all_targets.add(target)
            require(target not in ("/var/run/docker.sock", "/run/docker.sock"),
                    f"{name}: Docker socket mount forbidden")
            if kind == "bind":
                if local:
                    require(name in LOCAL_SETTINGS_MOUNTS and target == "/config" and
                            Path(source) == LOCAL_SETTINGS_SOURCE and
                            (mount.get("read_only") is True) == LOCAL_SETTINGS_MOUNTS[name] and
                            mount.get("bind", {}).get("create_host_path") is False,
                            f"{name}: unapproved settings host bind source, target or mode")
                else:
                    require(target in DEV_BIND_TARGETS.get(name, set()) and
                            mount.get("read_only") is True and source.startswith("/") and
                            source != "/",
                            f"{name}: unapproved or writable host bind mount")
                bind_targets.add(target)
            else:
                require(kind == "volume" and source in volumes,
                        f"{name}: undeclared named volume or mount type")
                named_mounts.add((source, target, mount.get("read_only") is True))
        require(len(named_mounts) + len(bind_targets) == len(mounts) and
                named_mounts == expected_volumes[name],
                f"{name}: named volume mount differs from approved source, target or mode")
        expected_bind_targets = ({"/config"} if name in LOCAL_SETTINGS_MOUNTS else set()) if local else DEV_BIND_TARGETS.get(name, set())
        require(bind_targets == expected_bind_targets,
                f"{name}: host bind targets differ from approved targets")
        if local and name in LOCAL_SETTINGS_MOUNTS:
            require(service.get("environment", {}).get("PADO_SPACECRAFT_CONFIG") ==
                    "/config/spacecraft.json",
                    f"{name}: shared settings path differs from approved file")
        if local and name == "ros-api":
            groups = service.get("group_add")
            require(isinstance(groups, list) and len(groups) == 1 and
                    isinstance(groups[0], str) and groups[0].isascii() and groups[0].isdigit(),
                    "ros-api: one numeric operator group is required for settings writes")
        if local and name == "sim-volume-init":
            require(service.get("command") == SIM_VOLUME_INIT_COMMAND,
                    "sim-volume-init must chown only approved volume roots without recursion")
            require(service.get("user") == "0:0" and added_caps == ["CHOWN"] and
                    service.get("read_only") is True,
                    "sim-volume-init must run read-only with only CHOWN capability")
        connected = set(service.get("networks") or ())
        if service.get("network_mode") == "none":
            require(not connected, f"{name}: network_mode none has networks")
        else:
            require(connected, f"{name}: implicit default network forbidden")
            require(connected <= set(networks), f"{name}: unknown network")
            for network in connected:
                members[network].add(name)
        published = service.get("ports") or []
        if name == GATEWAY:
            require(connected == {"entry", "dgs_portal"},
                    "DGS gateway must have only the entry and portal networks")
            require(not service.get("extra_hosts") and not service.get("dns") and
                    not service.get("dns_search"),
                    "DGS gateway must resolve the portal through Docker service discovery")
            require(service.get("read_only") is True and
                    "ALL" in (service.get("cap_drop") or []) and
                    "no-new-privileges:true" in (service.get("security_opt") or []),
                    "DGS gateway must run read-only without capabilities or new privileges")
            require(service.get("command") == [
                "python3", "/app/deploy/stage9/dgs_ops_gateway.py",
                "--listen-port", str(port), "--upstream-port", str(port)],
                "DGS gateway must relay only to the fixed portal endpoint")
            environment = service.get("environment") or {}
            require(isinstance(environment, dict),
                    "DGS gateway environment must be a keyed mapping")
            if port == 8080:
                require(environment == {"PADO_GATEWAY_OPERATOR_CIDR": operator_cidr},
                        "DGS gateway operator CIDR differs from the host firewall policy")
            else:
                require(not environment,
                        "loopback development gateway must not inherit environment overrides")
            require(len(published) == 1, "DGS gateway must publish exactly one port")
            rule = published[0]
            require(isinstance(rule, dict) and
                    str(rule.get("host_ip")) == bind_ip and
                    int(rule.get("published", -1)) == port and
                    int(rule.get("target", -1)) == port and
                    rule.get("protocol", "tcp") == "tcp",
                    "DGS gateway published port differs from firewall policy")
        else:
            require(not published, f"{name}: only DGS may publish a host port")
            require("entry" not in connected, f"{name}: only DGS may join entry")
    require(members["entry"] == {GATEWAY}, "only DGS may join entry")
    require(members["dgs_portal"] == {GATEWAY, PORTAL},
            "DGS-to-portal network must have exactly two peers")
    for name, expected in NETWORK_MEMBERS.items():
        if name in networks:
            require(members[name] == expected,
                    f"{name}: unexpected network membership")
    require(set(services[PORTAL].get("networks") or ()) ==
            {"dgs_portal", "portal_ros", "portal_mps"},
            "portal must have only approved internal networks")
    command = services[PORTAL].get("command")
    require(isinstance(command, list), "portal command must be a fixed argument list")
    for flag, target in (("--ros-url", "ros-api"), ("--mps-url", "mps-api")):
        require(flag in command and command.index(flag) + 1 < len(command),
                f"portal {flag} is missing")
        url = urlsplit(command[command.index(flag) + 1])
        require(url.hostname == target and url.scheme in ("http", "https") and
                url.port in (8081, 8443),
                f"portal {flag} must target the internal {target} service")


def check_actual_networks(document: list, project: str, bridge: str,
                          require_gateway: bool = False) -> None:
    require(isinstance(document, list), "Docker network inspect must return a list")
    require(all(isinstance(network, dict) for network in document),
            "Docker network inspect returned an invalid entry")
    expected = {f"{project}_{name}" for name in
                ("entry", "dgs_portal", "portal_ros", "portal_mps", "sim_tm")}
    expected_members = {
        "entry": {GATEWAY},
        "dgs_portal": {GATEWAY, PORTAL},
        "portal_ros": {PORTAL, "ros-api"},
        "portal_mps": {PORTAL, "mps-api"},
        "sim_tm": {"sim-ground", "sim-satellite"},
    }
    require({network.get("Name") for network in document} == expected,
            "actual Docker network set differs from approved Compose networks")
    for network in document:
        name = network["Name"]
        options = network.get("Options") or {}
        require(network.get("Driver") == "bridge" and
                network.get("EnableIPv6") is False,
                f"{name}: unexpected driver or IPv6 state")
        if name == f"{project}_entry":
            require(network.get("Internal") is False and
                    options.get("com.docker.network.bridge.name") == bridge and
                    options.get("com.docker.network.bridge.gateway_mode_ipv4") == "nat",
                    "actual entry bridge differs from the egress firewall interface")
        else:
            require(network.get("Internal") is True and
                    options.get("com.docker.network.bridge.gateway_mode_ipv4") == "isolated",
                    f"{name}: actual private bridge is not isolated")
        attached = network.get("Containers") or {}
        require(isinstance(attached, dict) and
                all(isinstance(member, dict) and isinstance(member.get("Name"), str)
                    for member in attached.values()),
                f"{name}: malformed Docker network membership")
        logical_name = name[len(project) + 1:]
        allowed = {f"{project}-{service}-1" for service in expected_members[logical_name]}
        present = {member["Name"] for member in attached.values()}
        require(present <= allowed,
                f"{name}: an unexpected container is attached to the bridge")
        if require_gateway:
            require(present == allowed,
                    f"{name}: expected running service is absent from the bridge")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind-ip", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--bridge", required=True)
    parser.add_argument("--operator-cidr", default="192.168.45.1/32")
    parser.add_argument("--actual-networks", action="store_true")
    parser.add_argument("--require-gateway", action="store_true")
    parser.add_argument("--project")
    args = parser.parse_args()
    ipaddress.IPv4Address(args.bind_ip)
    try:
        document = json.load(sys.stdin)
        if args.actual_networks:
            require(bool(args.project), "project is required for actual network checks")
            check_actual_networks(document, args.project, args.bridge,
                                  args.require_gateway)
        else:
            check(document, args.bind_ip, args.port, args.bridge,
                  args.operator_cidr, args.project)
    except (ValueError, TypeError, AttributeError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(1, f"DGS network policy violation: {exc}\n")


if __name__ == "__main__":
    main()
