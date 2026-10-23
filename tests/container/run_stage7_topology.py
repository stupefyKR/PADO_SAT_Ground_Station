"""Probe pairwise Docker bridge isolation for the proposed single-VM layout.

Creates only disposable, uniquely named networks and containers; it changes no
host firewall rule. A successful probe does not establish connection direction.
"""

import argparse
import json
import pathlib
import re
import subprocess
import time
import uuid


ROOT = pathlib.Path(__file__).resolve().parents[2]
PLAN = ROOT / "deploy" / "stage7" / "flow-plan.json"
IMAGE = "pado-stage2-test:local"
SERVER = """import socket
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('0.0.0.0', 25252))
s.listen()
while True:
    peer, _ = s.accept()
    peer.close()
"""
CLIENT = (
    "import socket,sys; s=socket.socket(); s.settimeout(.8); "
    "r=s.connect_ex((sys.argv[1],25252)); s.close(); "
    "sys.exit(0 if (r==0)==(sys.argv[2]=='allow') else 1)"
)


def run(*args):
    return subprocess.run(args, check=True, text=True,
                          capture_output=True, timeout=30)


def require_patched_engine():
    version = run("docker", "info", "--format", "{{.ServerVersion}}").stdout.strip()
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:\+[A-Za-z0-9.-]+)?", version)
    if not match or tuple(map(int, match.groups()[:3])) < (28, 0, 0):
        raise RuntimeError("Docker Engine stable 28.0.0+ is required for isolated probe bridges")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    if plan["publications"] or plan["scope"] != "single-linux-vm-disposable-docker-probes":
        raise RuntimeError("stage 7 probe plan must have no published ports")
    require_patched_engine()
    if args.build:
        run("docker", "build", "-t", IMAGE, "-f",
            str(ROOT / "tests/container/Dockerfile"), str(ROOT))
    suffix = uuid.uuid4().hex[:10]
    members = sorted({role for network in plan["probe_networks"]
                      for role in network["members"]})
    pairs = {frozenset(net["members"]) for net in plan["probe_networks"]}
    nets = {}
    containers = {}
    try:
        for item in plan["probe_networks"]:
            name = f"pado-s7-{suffix}-{item['name']}"
            run("docker", "network", "create", "--driver", "bridge", "--internal",
                "--opt", "com.docker.network.bridge.gateway_mode_ipv4=isolated", name)
            nets[item["name"]] = name
            actual = json.loads(run("docker", "network", "inspect", name).stdout)[0]
            if (actual.get("Driver") != "bridge" or actual.get("Internal") is not True or
                    actual.get("EnableIPv6") is not False or
                    actual.get("Options", {}).get(
                        "com.docker.network.bridge.gateway_mode_ipv4") != "isolated"):
                raise RuntimeError(f"probe bridge is not isolated: {name}")
        for role in members:
            attached = [nets[net["name"]] for net in plan["probe_networks"]
                        if role in net["members"]]
            name = f"pado-s7-{suffix}-{role}"
            run("docker", "run", "-d", "--name", name, "--network", attached[0],
                "--read-only", "--cap-drop", "ALL", "--security-opt",
                "no-new-privileges:true", "--tmpfs", "/tmp", "--log-driver", "journald",
                IMAGE, "sh", "-c", "echo pado-stage7-probe-start; exec sleep infinity")
            containers[role] = name
            for network in attached[1:]:
                run("docker", "network", "connect", network, name)
            run("docker", "exec", "-d", name, "python3", "-c", SERVER)
        time.sleep(1)
        inspected = {}
        for role, name in containers.items():
            inspected[role] = json.loads(run("docker", "inspect", name).stdout)[0]
            if inspected[role]["NetworkSettings"]["Ports"]:
                raise RuntimeError(f"published ports on {role}")
            if inspected[role]["HostConfig"]["LogConfig"]["Type"] != "journald":
                raise RuntimeError(f"journald logging disabled on {role}")
            if "pado-stage7-probe-start" not in run("docker", "logs", name).stdout:
                raise RuntimeError(f"startup log missing from {role}")
        permitted = 0
        blocked = 0
        for source in members:
            for destination in members:
                if source == destination:
                    continue
                shared = frozenset((source, destination)) in pairs
                target_networks = inspected[destination]["NetworkSettings"]["Networks"]
                if shared:
                    pair = next(net for net in plan["probe_networks"]
                                if set(net["members"]) == {source, destination})
                    ip = target_networks[nets[pair["name"]]]["IPAddress"]
                else:
                    ip = next(iter(target_networks.values()))["IPAddress"]
                expectation = "allow" if shared else "deny"
                try:
                    run("docker", "exec", containers[source], "python3", "-c",
                        CLIENT, ip, expectation)
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError(f"{source} -> {destination} expected {expectation}") from exc
                permitted += shared
                blocked += not shared
        print(f"Docker pair reachability: {permitted} allowed, {blocked} blocked; "
              "zero published ports; seven journald log probes")
        print("Direction and host firewall remain separate checks")
    finally:
        for name in reversed(list(containers.values())):
            subprocess.run(("docker", "rm", "-f", name), stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)
        for name in reversed(list(nets.values())):
            subprocess.run(("docker", "network", "rm", name), stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)


if __name__ == "__main__":
    main()
