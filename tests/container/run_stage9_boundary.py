"""Live Linux proof that the operator port and outbound path cross only DGS.

This creates a disposable Stage 9 project through the same launcher used on the
VM. It never depends on a public TCP service being reachable: a controlled host
listener proves local egress denial, and firewall counters prove forwarded SYNs
are dropped before they leave the DGS bridge.
"""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ("docker", "compose", "-p", "pado-stage9-local", "-f",
           str(ROOT / "deploy" / "compose.local.yaml"))
TCP_PROBE = """import socket,sys
s=socket.socket()
s.settimeout(2)
if len(sys.argv)>3:
    s.bind((sys.argv[3],0))
result=s.connect_ex((sys.argv[1],int(sys.argv[2])))
s.close()
print('connect_ex='+str(result))
raise SystemExit(0 if result else 1)
"""
DNS_PROBE = """import socket
try:
    socket.getaddrinfo('example.com',443)
except socket.gaierror:
    raise SystemExit(0)
raise SystemExit('external DNS resolved through an internal network')
"""
GATEWAY_ACL_PROBE = """import socket
with socket.create_connection(('127.0.0.1',8080),timeout=5) as client:
    client.settimeout(5)
    try:
        received=client.recv(1)
    except socket.timeout:
        raise SystemExit('gateway did not reject unauthorized loopback peer')
    if received:
        raise SystemExit('gateway relayed unauthorized loopback peer')
"""
OPERATOR_HTTP_PROBE = """import sys,urllib.request
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://'+sys.argv[1]+':8080/login',timeout=5) as response:
    if response.status != 200 or b'<html' not in response.read(2048).lower():
        raise SystemExit('operator HTTP did not reach the portal through DGS')
"""


def run(*command: str, env: dict[str, str], timeout: int = 120) -> str:
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                            text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"{' '.join(command)} failed ({result.returncode}):\n"
                           f"{result.stdout[-4000:]}\n{result.stderr[-4000:]}")
    return result.stdout


def host_ip() -> str:
    route = subprocess.run(("ip", "-4", "-j", "route", "get", "1.1.1.1"),
                           capture_output=True, text=True, timeout=5, check=True)
    selected = json.loads(route.stdout)[0]
    address = selected.get("prefsrc") or selected.get("src")
    if not address:
        raise RuntimeError(f"no source IPv4 address in runner route: {selected}")
    parsed = ipaddress.IPv4Address(address)
    if not parsed.is_private or parsed.is_loopback:
        raise RuntimeError(f"runner has no private non-loopback interface: {address}")
    return address


def operator_link() -> tuple[str, str, str, str, str, str]:
    """Choose a free RFC1918 /29 for allowed and denied test PC addresses."""
    routes = json.loads(run("ip", "-4", "-j", "route", "show", "table", "all",
                            env=dict(os.environ), timeout=10))
    in_use = []
    for route in routes:
        destination = route.get("dst")
        if destination and destination != "default":
            try:
                in_use.append(ipaddress.IPv4Network(destination, strict=False))
            except ValueError:
                continue
    for cidr in ("10.254.254.0/29", "172.30.251.0/29",
                 "192.168.253.0/29"):
        subnet = ipaddress.IPv4Network(cidr)
        if all(not subnet.overlaps(route) for route in in_use):
            host, operator, denied = tuple(subnet.hosts())[:3]
            suffix = f"{os.getpid() % 1000000:06d}"
            return f"padoop{suffix}", f"padoh{suffix}", f"padon{suffix}", \
                str(host), str(operator), str(denied)
    raise RuntimeError("no free RFC1918 test operator /29 is available")


def drop_count(chain: str, marker: str, env: dict[str, str],
               *, interface_column: int | None = None) -> int:
    output = run("sudo", "iptables", "-nvxL", chain, env=env, timeout=10)
    selected = [line for line in output.splitlines()
                if marker in line and "DROP" in line and
                (interface_column is None or
                 len(line.split()) > interface_column and
                 line.split()[interface_column] == marker)]
    if len(selected) != 1:
        raise RuntimeError(f"expected one {marker} DROP rule in {chain}:\n{output}")
    return int(selected[0].split()[0])


def denied_tcp(service: str, address: str, port: int, env: dict[str, str]) -> None:
    run(*COMPOSE, "exec", "-T", service, "python3", "-c", TCP_PROBE,
        address, str(port), env=env, timeout=15)


def direct_entry_ip(env: dict[str, str]) -> str:
    entry = json.loads(run("docker", "network", "inspect",
                           "pado-stage9-local_entry", env=env))[0]
    members = [member for member in entry.get("Containers", {}).values()
               if member.get("Name", "").startswith(
                   "pado-stage9-local-dgs-ops-gateway-")]
    if len(members) != 1:
        raise RuntimeError(f"DGS entry network has unexpected members: {members}")
    return str(ipaddress.ip_interface(members[0]["IPv4Address"]).ip)


def verify_loopback(namespace: str, lan_ip: str, operator_ip: str,
                    env: dict[str, str]) -> None:
    """Recreate the same disposable stack with the new host-only entry point."""
    run("bash", str(ROOT / "pado-gs.sh"), "start", env=env, timeout=600)
    gateway_id=run(*COMPOSE,"ps","--quiet","dgs-ops-gateway",env=env).strip()
    inspection=json.loads(run("docker","inspect",gateway_id,env=env))[0]
    bindings=inspection['NetworkSettings']['Ports']
    if bindings!={'8080/tcp':[{'HostIp':'127.0.0.1','HostPort':'8080'}]}:
        raise RuntimeError(f'loopback gateway published unexpected ports: {bindings}')
    for service in ("portal","ros-api","mps-api","sim-ground"):
        identity=run(*COMPOSE,"ps","--quiet",service,env=env).strip()
        ports=json.loads(run("docker","inspect",identity,env=env))[0]['HostConfig']['PortBindings']
        if ports:
            raise RuntimeError(f'{service} unexpectedly publishes a host port')
    # This host-origin request includes Docker's DNAT or userland-proxy path.
    # A success proves the relay accepts the exact host peer after translation.
    run(sys.executable,"-c",OPERATOR_HTTP_PROBE,'127.0.0.1',env=env,timeout=15)
    run("sudo","-n","ip","netns","exec",namespace,"python3","-c",
        TCP_PROBE,lan_ip,"8080",operator_ip,env=env,timeout=15)
    target=direct_entry_ip(env)
    run("sudo","-n","ip","netns","exec",namespace,"python3","-c",
        TCP_PROBE,target,"8080",operator_ip,env=env,timeout=15)
    # Changing the published address must retain both outbound deny paths.
    with socket.socket() as listener:
        listener.bind((lan_ip,0));listener.listen(1)
        before=drop_count('INPUT','pado-gs-local-host',env)
        denied_tcp('dgs-ops-gateway',lan_ip,listener.getsockname()[1],env)
        if drop_count('INPUT','pado-gs-local-host',env)<=before:
            raise RuntimeError('loopback DGS-to-host did not hit the INPUT deny rule')
    before=drop_count('PADO_GS_LOCAL','br-pado-local',env,interface_column=5)
    denied_tcp('dgs-ops-gateway','198.51.100.1',9,env)
    if drop_count('PADO_GS_LOCAL','br-pado-local',env,interface_column=5)<=before:
        raise RuntimeError('loopback DGS outbound did not hit the FORWARD deny rule')
    run('bash',str(ROOT/'pado-gs.sh'),'status',env=env,timeout=30)
    print('host loopback→DGS→portal: PASS; LAN and direct container bypass: DENIED')
    print('loopback mode DGS outbound and unpublished subsystem APIs: verified')


def main() -> int:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("live DGS boundary test requires Linux Docker")
    bind = host_ip()
    env = dict(os.environ, PADO_LOCAL_BIND_IP=bind,
               PADO_CONFIG_GID=str(os.getgid()))
    namespace, host_veth, peer_veth, link_host, operator_ip, denied_ip = operator_link()
    namespace_created = False
    veth_created = False
    failure: BaseException | None = None
    try:
        # A veth-backed namespace models the external operator PC. Its exact
        # source IP reaches Docker's host-published port without Docker bridge
        # SNAT; a successful HTTP response proves both firewall and relay ACL.
        run("sudo", "-n", "ip", "netns", "add", namespace, env=env, timeout=10)
        namespace_created = True
        run("sudo", "-n", "ip", "link", "add", host_veth, "type", "veth",
            "peer", "name", peer_veth, env=env, timeout=10)
        veth_created = True
        run("sudo", "-n", "ip", "link", "set", peer_veth, "netns", namespace,
            env=env, timeout=10)
        run("sudo", "-n", "ip", "addr", "add", f"{link_host}/29", "dev",
            host_veth, env=env, timeout=10)
        run("sudo", "-n", "ip", "link", "set", host_veth, "up", env=env,
            timeout=10)
        run("sudo", "-n", "ip", "netns", "exec", namespace, "ip", "addr", "add",
            f"{operator_ip}/29", "dev", peer_veth, env=env, timeout=10)
        run("sudo", "-n", "ip", "netns", "exec", namespace, "ip", "addr", "add",
            f"{denied_ip}/29", "dev", peer_veth, env=env, timeout=10)
        run("sudo", "-n", "ip", "netns", "exec", namespace, "ip", "link", "set",
            "lo", "up", env=env, timeout=10)
        run("sudo", "-n", "ip", "netns", "exec", namespace, "ip", "link", "set",
            peer_veth, "up", env=env, timeout=10)
        run("sudo", "-n", "ip", "netns", "exec", namespace, "ip", "route", "add",
            "default", "via", link_host, env=env, timeout=10)
        env["PADO_OPERATOR_CIDR"] = f"{operator_ip}/32"
        run(*COMPOSE, "build", "portal", "dgs-ops-gateway", env=env, timeout=600)
        # The launcher intentionally refuses account-free noninteractive start.
        # Provision an ephemeral, unknown password inside its disposable volume.
        run(*COMPOSE, "run", "--rm", "--no-deps", "portal", "python3", "-c",
            "from pathlib import Path; import secrets; "
            "from services.webapp.auth import AuthStore; "
            "s=AuthStore(Path('/state/accounts.db')); "
            "s.create_user('boundaryci','planner',secrets.token_urlsafe(24)); "
            "s.close()", env=env, timeout=120)
        run("bash", str(ROOT / "pado-gs.sh"), "start", env=env, timeout=600)

        run("sudo", "-n", "ip", "netns", "exec", namespace, "python3", "-c",
            OPERATOR_HTTP_PROBE, bind, env=env, timeout=15)

        # This connection stays within the gateway container's own network
        # namespace, so it bypasses host firewall filtering. Only the gateway
        # application CIDR check can reject its 127.0.0.1 source address.
        run(*COMPOSE, "exec", "-T", "dgs-ops-gateway", "python3", "-c",
            GATEWAY_ACL_PROBE, env=env, timeout=15)

        with socket.socket() as listener:
            listener.bind((bind, 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            with socket.create_connection((bind, port), timeout=2):
                connection, _ = listener.accept()
                connection.close()
            for service in ("portal", "ros-api", "dgs-ops-gateway"):
                before = (drop_count("INPUT", "pado-gs-local-host", env)
                          if service == "dgs-ops-gateway" else None)
                denied_tcp(service, bind, port, env)
                if before is not None and drop_count(
                        "INPUT", "pado-gs-local-host", env) <= before:
                    raise RuntimeError("DGS-to-host SYN did not hit the INPUT deny rule")

        # Every non-DGS application with a network attachment must lack a
        # route to an external IPv4 target. FDS/core are network_mode none.
        for service in ("portal", "ros-api", "mps-api", "sim-ground",
                        "sim-satellite"):
            denied_tcp(service, "198.51.100.1", 9, env)

        # A non-routable TEST-NET destination does not require Internet. The
        # DROP counter proves the SYN reached our FORWARD/DOCKER-USER boundary.
        before = drop_count("PADO_GS_LOCAL", "br-pado-local", env,
                            interface_column=5)
        denied_tcp("dgs-ops-gateway", "198.51.100.1", 9, env)
        if drop_count("PADO_GS_LOCAL", "br-pado-local", env,
                      interface_column=5) <= before:
            raise RuntimeError("DGS outbound SYN did not hit the FORWARD deny rule")

        # Use a second source IP from the same outside namespace. Docker
        # bridge hairpin/userland-proxy paths can accept the TCP handshake
        # before the gateway ACL closes it, so they are unsuitable here.
        target = direct_entry_ip(env)
        before_operator = drop_count("PADO_GS_LOCAL", "ctorigdstport 8080", env)
        run("sudo", "-n", "ip", "netns", "exec", namespace, "python3", "-c",
            TCP_PROBE, bind, "8080", denied_ip, env=env, timeout=15)
        if drop_count("PADO_GS_LOCAL", "ctorigdstport 8080", env) <= before_operator:
            raise RuntimeError("non-operator SYN did not hit the published-port deny rule")
        before = drop_count("PADO_GS_LOCAL", "br-pado-local", env,
                            interface_column=6)
        direct_result = run("sudo", "-n", "ip", "netns", "exec", namespace,
                            "python3", "-c", TCP_PROBE, target, "8080",
                            operator_ip, env=env, timeout=15).strip()
        after = drop_count("PADO_GS_LOCAL", "br-pado-local", env,
                           interface_column=6)
        if after <= before:
            # Docker Engine may refuse directly routed container traffic
            # before it reaches DOCKER-USER. The failed TCP connect above is
            # still a live denial; confirm our rule remains installed and in
            # order, then retain enough routing evidence to explain the path.
            run("sudo", "-n", "iptables", "-C", "PADO_GS_LOCAL", "-o",
                "br-pado-local", "-j", "DROP", env=env, timeout=10)
            run("bash", str(ROOT / "pado-gs.sh"), "status", env=env, timeout=30)
            route = subprocess.run(("sudo", "-n", "ip", "netns", "exec",
                                    namespace, "ip", "-4", "route", "get", target,
                                    "from", operator_ip), cwd=ROOT, env=env,
                                   capture_output=True, text=True, timeout=10,
                                   check=False)
            print("direct-to-DGS TCP denied before owned FORWARD DROP counter; "
                  f"{direct_result}; route={route.stdout.strip() or route.stderr.strip()}; "
                  f"counter={before}->{after}", file=sys.stderr)

        # Engine 28+ also fixes external DNS forwarding from internal bridges.
        # Verify the host can resolve the name so an offline runner cannot
        # accidentally make this denial test pass.
        try:
            socket.getaddrinfo("example.com", 443)
        except socket.gaierror:
            print("host DNS unavailable; external DNS probe skipped",
                  file=sys.stderr)
        else:
            for service in ("portal", "ros-api", "sim-ground"):
                run(*COMPOSE, "exec", "-T", service, "python3", "-c", DNS_PROBE,
                    env=env, timeout=15)
        print("operator→DGS→portal: PASS")
        print("unauthorized source→DGS relay: DENIED by gateway CIDR ACL")
        print("portal/ROS→host: DENIED")
        print("portal/ROS/MPS/simulator→external IPv4: DENIED")
        print("DGS→host INPUT and DGS→outside FORWARD: DENIED by owned rules")
        print("non-operator→host 8080: DENIED by owned rule")
        print("direct-to-DGS container bypass: DENIED (owned rule verified)")
        env.update(PADO_LOCAL_BIND_IP='127.0.0.1',PADO_OPERATOR_CIDR='127.0.0.1/32')
        verify_loopback(namespace,bind,operator_ip,env)
        return 0
    except BaseException as exc:
        failure = exc
        raise
    finally:
        cleanup_errors = []
        # cleanup removes only this project and its owned firewall rules.
        cleanup = subprocess.run(("bash", str(ROOT / "pado-gs.sh"), "cleanup",
                                  "--yes"), cwd=ROOT, env=env,
                                 capture_output=True, text=True, timeout=120,
                                 check=False)
        if cleanup.returncode:
            cleanup_errors.append(
                f"boundary cleanup failed: {cleanup.stdout[-1000:]} "
                f"{cleanup.stderr[-1000:]}")
        if veth_created:
            removed = subprocess.run(("sudo", "-n", "ip", "link", "delete", host_veth),
                                     cwd=ROOT, env=env, capture_output=True,
                                     text=True, timeout=10, check=False)
            if removed.returncode:
                cleanup_errors.append(f"test veth cleanup failed: {removed.stderr[-1000:]}")
        if namespace_created:
            removed = subprocess.run(("sudo", "-n", "ip", "netns", "delete", namespace),
                                     cwd=ROOT, env=env, capture_output=True,
                                     text=True, timeout=10, check=False)
            if removed.returncode:
                cleanup_errors.append(
                    f"test network namespace cleanup failed: {removed.stderr[-1000:]}")
        if cleanup_errors:
            if failure is None:
                raise RuntimeError("\n".join(cleanup_errors))
            for detail in cleanup_errors:
                print(detail, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
