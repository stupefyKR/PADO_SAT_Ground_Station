"""Reject a local Compose topology that crosses the DGS external boundary."""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
from urllib.parse import urlsplit


GATEWAY = "dgs-ops-gateway"
PORTAL = "portal"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def check(document: dict, bind_ip: str, port: int, bridge: str,
          operator_cidr: str = "192.168.45.1/32") -> None:
    services = document.get("services")
    networks = document.get("networks")
    require(isinstance(services, dict) and isinstance(networks, dict),
            "Compose services and networks are required")
    require(GATEWAY in services and PORTAL in services,
            "DGS gateway and portal services are required")
    require(set(networks) >= {"entry", "dgs_portal", "portal_ros", "portal_mps"},
            "required DGS and portal networks are missing")

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
            require(service.get(namespace) != "host",
                    f"{name}: host {namespace} namespace forbidden")
        require(not service.get("devices") and not service.get("device_cgroup_rules"),
                f"{name}: host devices forbidden")
        added_caps = service.get("cap_add") or []
        require(not added_caps or (name == "sim-volume-init" and
                                   service.get("network_mode") == "none" and
                                   added_caps == ["CHOWN"]),
                f"{name}: added capabilities forbidden")
        mounts = service.get("volumes", [])
        require(not any("/var/run/docker.sock" in str(mount) or
                        "/run/docker.sock" in str(mount) for mount in mounts),
                f"{name}: Docker socket mount forbidden")
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
                  args.operator_cidr)
    except (ValueError, TypeError, AttributeError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(1, f"DGS network policy violation: {exc}\n")


if __name__ == "__main__":
    main()
