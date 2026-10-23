"""Read-only audit of a Linux VM running the single-VM Docker architecture.

Run as root, or give the operator passwordless read-only access to UFW status.
No service or firewall setting is changed by this script.
"""

import argparse
import ipaddress
import json
import os
import subprocess
import sys


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, check=False,
                            timeout=20, env={**os.environ, "LC_ALL": "C"})
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def inbound_rules(status: str) -> list[tuple[str, str]]:
    rules = []
    for line in status.splitlines():
        if "ALLOW IN" in line:
            target, source = line.split("ALLOW IN", 1)
            rules.append((target.strip(), source.strip()))
    return rules


def audit(admin_ip: str):
    report = {"docker_access": False, "published_ports": [],
              "ufw_active": False, "ufw_default_deny_incoming": False,
              "ufw_default_deny_routed": False, "ufw_logging_on": False,
              "ufw_inbound_rules": [], "ufw_unexpected_inbound": [],
              "ntp_synchronized": False, "docker_logging_driver": None,
              "errors": []}
    rc, out, err = command("docker", "info", "--format", "{{.LoggingDriver}}")
    if rc == 0:
        report["docker_access"] = True
        report["docker_logging_driver"] = out
        rc, out, err = command("docker", "ps", "-q")
        if rc == 0 and out:
            rc, data, err = command("docker", "inspect", *out.splitlines())
            if rc == 0:
                for item in json.loads(data):
                    for port, bindings in item["NetworkSettings"]["Ports"].items():
                        if bindings:
                            report["published_ports"].append({
                                "container": item["Name"].lstrip("/"),
                                "container_port": port, "bindings": bindings})
            else:
                report["errors"].append(f"docker inspect: {err}")
    else:
        report["errors"].append(f"docker info: {err}")

    rc, out, err = command("sudo", "-n", "ufw", "status", "verbose")
    if rc != 0:
        report["errors"].append(f"ufw status: {err}")
    else:
        report["ufw_active"] = "Status: active" in out
        report["ufw_default_deny_incoming"] = "Default: deny (incoming)" in out
        report["ufw_default_deny_routed"] = "deny (routed)" in out
        report["ufw_logging_on"] = "Logging: on" in out
        report["ufw_inbound_rules"] = inbound_rules(out)
        report["ufw_unexpected_inbound"] = [rule for rule in report["ufw_inbound_rules"]
                                            if rule != ("22/tcp", admin_ip)]

    rc, out, err = command("timedatectl", "show", "-p", "NTPSynchronized", "--value")
    if rc == 0:
        report["ntp_synchronized"] = out == "yes"
    else:
        report["errors"].append(f"timedatectl: {err}")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-ip", required=True,
                        help="exact management PC IPv4 address allowed to SSH")
    args = parser.parse_args()
    ipaddress.IPv4Address(args.admin_ip)
    report = audit(args.admin_ip)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    passed = (report["docker_access"] and not report["published_ports"] and
              report["ufw_active"] and report["ufw_default_deny_incoming"] and
              report["ufw_default_deny_routed"] and report["ufw_logging_on"] and
              report["ufw_inbound_rules"] == [("22/tcp", args.admin_ip)] and
              report["ntp_synchronized"] and not report["errors"])
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
