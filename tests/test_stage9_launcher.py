"""Exercise the local Stage 9 launcher without Docker or host firewall access."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from deploy.stage9.check_compose_policy import LOCAL_VOLUMES


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "pado-gs.sh"


FAKE_COMMAND = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
log = Path(os.environ["PADO_LAUNCHER_TEST_LOG"])
with log.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps([name, *args]) + "\n")

if name == "sudo":
    while args and args[0] in ("-n", "--non-interactive"):
        args.pop(0)
    if not args:
        sys.exit(1)
    sys.exit(subprocess.run(args, check=False).returncode)

if name == "ip":
    print("2: eth0    inet 192.168.45.128/24 brd 192.168.45.255 scope global eth0")
    sys.exit(0)

if name == "docker":
    if os.environ.get("PADO_LAUNCHER_TEST_DOCKER_FAIL") == "up" and "up" in args:
        sys.exit(42)
    if (os.environ.get("PADO_LAUNCHER_TEST_PORTAL_FAIL") == "1" and
            "exec" in args and "portal" in args and "urlopen" in " ".join(args)):
        sys.exit(7)
    if args[:2] == ["network", "inspect"]:
        print(Path(os.environ["PADO_LAUNCHER_TEST_NETWORK_JSON"]).read_text(encoding="utf-8"))
    elif "account-status" in args:
        print(os.environ.get("PADO_LAUNCHER_TEST_ACCOUNT_STATUS", "configured"))
    elif args == ["info", "--format", "{{.ServerVersion}}"]:
        print(os.environ.get("PADO_LAUNCHER_TEST_DOCKER_VERSION", "28.0.0"))
    elif "--services" in args:
        services = ["dgs-ops-gateway", "portal", "ros-api", "mps-api", "ros-core-read",
                    "mps-core-read", "fds-synthetic-demo", "sim-ground", "sim-satellite"]
        if os.environ.get("PADO_LAUNCHER_TEST_SERVICES_INCOMPLETE") == "1":
            services.remove("mps-core-read")
        print("\n".join(services))
    elif "config" in args and "--format" in args:
        print(Path(os.environ["PADO_LAUNCHER_TEST_COMPOSE_JSON"]).read_text(encoding="utf-8"))
    elif "ps" in args:
        print("dgs-ops-gateway running 192.168.45.128:8080->8080/tcp")
        print("portal running")
    sys.exit(0)

if name == "iptables":
    state_path = Path(os.environ["PADO_LAUNCHER_TEST_IPTABLES"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    while args and args[0] == "-w":
        args.pop(0)
    if args[:2] == ["-t", "filter"]:
        args = args[2:]
    if not args:
        sys.exit(1)
    op = args.pop(0)
    chain = args.pop(0) if args else None
    status = 0
    if op == "-S":
        if chain and chain not in state:
            status = 1
        else:
            for selected in ([chain] if chain else state):
                print("-N " + selected)
                for rule in state[selected]:
                    print("-A " + selected + " " + " ".join(rule))
    elif op == "-N":
        if chain in state:
            status = 1
        else:
            state[chain] = []
    elif op == "-C":
        status = 0 if chain in state and args in state[chain] else 1
    elif op in ("-A", "-I"):
        if chain not in state:
            status = 1
        else:
            if op == "-I" and args and args[0].isdigit():
                state[chain].insert(int(args.pop(0)) - 1, args)
            elif op == "-I":
                state[chain].insert(0, args)
            else:
                state[chain].append(args)
    elif op == "-D":
        if chain not in state:
            status = 1
        elif args and args[0].isdigit():
            index = int(args[0]) - 1
            if index >= len(state[chain]):
                status = 1
            else:
                state[chain].pop(index)
        elif args not in state[chain]:
            status = 1
        else:
            state[chain].remove(args)
    elif op == "-F":
        if chain not in state:
            status = 1
        else:
            state[chain] = []
    elif op == "-X":
        if chain not in state or state[chain]:
            status = 1
        else:
            del state[chain]
    else:
        # Keep the fake closed: a new firewall verb must be modeled explicitly.
        status = 2
    state_path.write_text(json.dumps(state), encoding="utf-8")
    sys.exit(status)

# The launcher must not require a real UFW, network listener, or browser.
if name == "curl" and os.environ.get("PADO_LAUNCHER_TEST_CURL_FAIL") == "1":
    sys.exit(7)
if name in ("ufw", "ss", "curl", "sleep"):
    sys.exit(0)
sys.exit(2)
'''


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("bash"),
                     "launcher lifecycle test requires Linux bash")
class LauncherLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        bin_dir = base / "bin"
        bin_dir.mkdir()
        for name in ("docker", "sudo", "iptables", "ip", "ufw", "ss", "curl", "sleep"):
            command = bin_dir / name
            command.write_text(FAKE_COMMAND, encoding="utf-8")
            command.chmod(0o755)
        self.log_path = base / "commands.jsonl"
        self.log_path.touch()
        self.rules_path = base / "iptables.json"
        self.rules_path.write_text('{"DOCKER-USER": [], "INPUT": []}', encoding="utf-8")
        self.compose_json_path = base / "compose.json"
        self.compose_policy = {
            "name": "pado-stage9-local",
            "networks": {
                "entry": {"driver": "bridge", "enable_ipv6": False,
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
                                   "isolated"}},
            },
            "services": {
                "dgs-ops-gateway": {
                    "networks": {"entry": None, "dgs_portal": None},
                    "ports": [{"host_ip": "192.168.45.128", "published": "8080",
                               "target": 8080, "protocol": "tcp"}],
                    "read_only": True, "cap_drop": ["ALL"],
                    "security_opt": ["no-new-privileges:true"],
                    "environment": {"PADO_GATEWAY_OPERATOR_CIDR":
                                    "192.168.45.1/32"},
                    "command": ["python3", "/app/deploy/stage9/dgs_ops_gateway.py",
                                "--listen-port", "8080", "--upstream-port", "8080"],
                },
                "portal": {"networks": {"dgs_portal": None,
                                        "portal_ros": None, "portal_mps": None},
                           "command": ["python3", "portal", "--ros-url",
                                       "http://ros-api:8081", "--mps-url",
                                       "http://mps-api:8081"]},
                "ros-api": {"networks": {"portal_ros": None}},
                "mps-api": {"networks": {"portal_mps": None}},
                "ros-core-read": {"network_mode": "none"},
                "mps-core-read": {"network_mode": "none"},
                "init-token": {"network_mode": "none"},
                "fds-synthetic-demo": {"network_mode": "none"},
                "sim-volume-init": {"network_mode": "none"},
                "sim-ground": {"networks": {"sim_tm": None}},
                "sim-satellite": {"networks": {"sim_tm": None}},
            },
        }
        for service in self.compose_policy["services"].values():
            service.setdefault("cap_drop", ["ALL"])
            service.setdefault("security_opt", ["no-new-privileges:true"])
        self.compose_policy["volumes"] = {
            source: {} for mounts in LOCAL_VOLUMES.values()
            for source, _, _ in mounts}
        for name, mounts in LOCAL_VOLUMES.items():
            self.compose_policy["services"][name]["volumes"] = [
                {"type": "volume", "source": source, "target": target,
                 "read_only": read_only}
                for source, target, read_only in sorted(mounts)]
        self.compose_json_path.write_text(json.dumps(self.compose_policy), encoding="utf-8")
        self.network_json_path = base / "networks.json"
        members = {
            "entry": ("dgs-ops-gateway",),
            "dgs_portal": ("dgs-ops-gateway", "portal"),
            "portal_ros": ("portal", "ros-api"),
            "portal_mps": ("portal", "mps-api"),
            "sim_tm": ("sim-ground", "sim-satellite"),
        }
        self.networks = []
        for name in ("entry", "dgs_portal", "portal_ros", "portal_mps", "sim_tm"):
            external = name == "entry"
            self.networks.append({
                "Name": f"pado-stage9-local_{name}", "Driver": "bridge",
                "Internal": not external, "EnableIPv6": False,
                "Options": {"com.docker.network.bridge.name": "br-pado-local",
                            "com.docker.network.bridge.gateway_mode_ipv4": "nat"}
                if external else {
                    "com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
                "Containers": {str(index): {
                    "Name": f"pado-stage9-local-{service}-1"}
                    for index, service in enumerate(members[name])},
            })
        self.network_json_path.write_text(json.dumps(self.networks), encoding="utf-8")
        self.fixture_root = base / "checkout"
        (self.fixture_root / "deploy" / "stage9").mkdir(parents=True)
        (self.fixture_root / "out").mkdir()
        shutil.copyfile(LAUNCHER, self.fixture_root / "pado-gs.sh")
        shutil.copyfile(ROOT / "deploy" / "stage9" / "compose.local.yaml",
                        self.fixture_root / "deploy" / "stage9" / "compose.local.yaml")
        shutil.copyfile(ROOT / "deploy" / "stage9" / "check_compose_policy.py",
                        self.fixture_root / "deploy" / "stage9" / "check_compose_policy.py")
        self.sentinel = self.fixture_root / "out" / "existing-output.txt"
        self.sentinel.write_text("keep repo output", encoding="utf-8")
        self.env = dict(os.environ,
                        PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
                        PADO_LAUNCHER_TEST_LOG=str(self.log_path),
                        PADO_LAUNCHER_TEST_IPTABLES=str(self.rules_path),
                        PADO_LAUNCHER_TEST_COMPOSE_JSON=str(self.compose_json_path),
                        PADO_LAUNCHER_TEST_NETWORK_JSON=str(self.network_json_path),
                        PADO_LOCAL_BIND_IP="192.168.45.128",
                        PADO_OPERATOR_CIDR="192.168.45.1/32")

    def invoke(self, *args, input_text=""):
        return subprocess.run(["bash", str(self.fixture_root / "pado-gs.sh"), *args],
                              cwd=self.fixture_root,
                              env=self.env, input=input_text, text=True,
                              capture_output=True, timeout=30, check=False)

    def run_launcher(self, *args, input_text=""):
        result = self.invoke(*args, input_text=input_text)
        self.assertEqual(result.returncode, 0,
                         f"{args}: stdout={result.stdout}\nstderr={result.stderr}")
        return result

    def calls(self):
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]

    def rules(self):
        return json.loads(self.rules_path.read_text(encoding="utf-8"))

    def test_start_is_idempotent_and_stop_removes_owned_firewall_rules(self):
        self.assertTrue(LAUNCHER.is_file())
        self.run_launcher("start")
        first_calls = self.calls()
        docker_calls = [call[1:] for call in first_calls if call[0] == "docker"]
        self.assertTrue(any("build" in call for call in docker_calls), docker_calls)
        self.assertTrue(any("up" in call and "-d" in call
                            for call in docker_calls), docker_calls)
        self.assertTrue(any("--status" in call and "--services" in call
                            for call in docker_calls), docker_calls)
        self.assertTrue(any(call[0] == "docker" and "exec" in call and
                            "portal" in call and "urlopen" in " ".join(call)
                            for call in first_calls), first_calls)
        self.assertTrue(all("compose.local.yaml" in " ".join(call)
                            for call in docker_calls if "compose" in call and
                            "version" not in call), docker_calls)
        self.assertIn("PADO_GS_LOCAL", self.rules())
        first_rules = self.rules()
        all_rules = " ".join(" ".join(rule) for rules in first_rules.values() for rule in rules)
        self.assertIn("192.168.45.1/32", all_rules)
        self.assertIn("8080", all_rules)
        for rule in first_rules["PADO_GS_LOCAL"][:2]:
            self.assertEqual(rule[rule.index("--ctdir") + 1], "ORIGINAL",
                             "the 8080 filter must not drop reply packets")
        direct_route = first_rules["PADO_GS_LOCAL"][2]
        self.assertEqual(direct_route, ["-o", "br-pado-local", "-j", "DROP"])
        egress = first_rules["PADO_GS_LOCAL"][3]
        self.assertEqual(egress[egress.index("-i") + 1], "br-pado-local")
        self.assertEqual(egress[egress.index("--ctstate") + 1],
                         "ESTABLISHED,RELATED")
        self.assertIn("!", egress)
        self.assertEqual(egress[-2:], ["-j", "DROP"])
        host_egress = first_rules["INPUT"]
        self.assertEqual(len(host_egress), 1)
        self.assertIn("br-pado-local", host_egress[0])
        self.assertIn("pado-gs-local-host", host_egress[0])
        self.assertEqual(host_egress[0][-2:], ["-j", "DROP"])

        self.run_launcher("start")
        self.assertEqual(self.rules(), first_rules, "start added duplicate firewall rules")

        self.run_launcher("stop")
        stop_calls = [call[1:] for call in self.calls()[len(first_calls):] if call[0] == "docker"]
        self.assertTrue(any("stop" in call for call in stop_calls), stop_calls)
        self.assertNotIn("PADO_GS_LOCAL", self.rules())
        self.assertFalse(any("PADO_GS_LOCAL" in " ".join(rule)
                             for rule in self.rules()["DOCKER-USER"]))
        self.assertEqual(self.rules()["INPUT"], [])
        forbidden = [call for call in self.calls() if call[0] == "ufw" and
                     any(arg in ("disable", "reset", "default") for arg in call[1:])]
        self.assertEqual(forbidden, [])

    def test_cleanup_removes_only_project_containers_images_and_volumes(self):
        self.run_launcher("start")
        before_cleanup = len(self.calls())
        self.run_launcher("cleanup", "--yes")
        docker_calls = [call[1:] for call in self.calls()[before_cleanup:]
                        if call[0] == "docker"]
        down_calls = [call for call in docker_calls if "down" in call]
        self.assertEqual(len(down_calls), 1, docker_calls)
        self.assertIn("--volumes", down_calls[0])
        self.assertIn("--rmi", down_calls[0])
        self.assertIn("local", down_calls[0])
        self.assertNotIn("--remove-orphans", down_calls[0])
        self.assertNotIn("PADO_GS_LOCAL", self.rules())
        self.assertEqual(self.sentinel.read_text(encoding="utf-8"), "keep repo output")

    def test_missing_docker_user_chain_fails_before_publishing_port(self):
        self.rules_path.write_text("{}", encoding="utf-8")
        result = self.invoke("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DOCKER-USER", result.stderr)
        self.assertTrue(any("stop" in call for call in self.calls() if call[0] == "docker"),
                        self.calls())
        self.assertFalse(any("up" in call for call in self.calls() if call[0] == "docker"))
        self.assertNotIn("PADO_GS_LOCAL", self.rules())

    def test_compose_up_failure_stops_and_removes_firewall(self):
        self.env["PADO_LAUNCHER_TEST_DOCKER_FAIL"] = "up"
        result = self.invoke("start")
        self.assertNotEqual(result.returncode, 0)
        docker_calls = [call for call in self.calls() if call[0] == "docker"]
        self.assertTrue(any("up" in call for call in docker_calls), docker_calls)
        self.assertGreaterEqual(sum("stop" in call for call in docker_calls), 2, docker_calls)
        self.assertNotIn("PADO_GS_LOCAL", self.rules())
        self.assertEqual(self.rules()["DOCKER-USER"], [])
        self.assertEqual(self.rules()["INPUT"], [])

    def test_portal_probe_failure_stops_and_removes_firewall(self):
        self.env["PADO_LAUNCHER_TEST_PORTAL_FAIL"] = "1"
        result = self.invoke("start")
        self.assertNotEqual(result.returncode, 0)
        calls = self.calls()
        docker_calls = [call for call in calls if call[0] == "docker"]
        self.assertTrue(any(call[0] == "docker" and "exec" in call and
                            "portal" in call for call in calls), calls)
        self.assertGreaterEqual(sum("stop" in call for call in docker_calls), 2, docker_calls)
        self.assertNotIn("PADO_GS_LOCAL", self.rules())
        self.assertEqual(self.rules()["DOCKER-USER"], [])
        self.assertEqual(self.rules()["INPUT"], [])

    def test_incomplete_service_set_stops_and_removes_firewall(self):
        self.env["PADO_LAUNCHER_TEST_SERVICES_INCOMPLETE"] = "1"
        result = self.invoke("start")
        self.assertNotEqual(result.returncode, 0)
        calls = self.calls()
        docker_calls = [call for call in calls if call[0] == "docker"]
        self.assertTrue(any("--services" in call for call in docker_calls), docker_calls)
        self.assertGreaterEqual(sum("stop" in call for call in docker_calls), 2, docker_calls)
        self.assertFalse(any(call[0] == "docker" and "exec" in call and
                             "portal" in call for call in calls), calls)
        self.assertNotIn("PADO_GS_LOCAL", self.rules())

    def test_empty_account_refuses_noninteractive_start(self):
        self.env["PADO_LAUNCHER_TEST_ACCOUNT_STATUS"] = "empty"
        result = self.invoke("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("interactive", result.stderr)
        docker_calls = [call for call in self.calls() if call[0] == "docker"]
        self.assertTrue(any("stop" in call for call in docker_calls), docker_calls)
        self.assertFalse(any("up" in call for call in docker_calls), docker_calls)
        self.assertNotIn("PADO_GS_LOCAL", self.rules())

    def test_empty_account_uses_interactive_first_user_flow(self):
        import pty

        self.env["PADO_LAUNCHER_TEST_ACCOUNT_STATUS"] = "empty"
        master, slave = pty.openpty()
        try:
            process = subprocess.Popen(["bash", str(self.fixture_root / "pado-gs.sh"),
                                        "start"], cwd=self.fixture_root, env=self.env,
                                       stdin=slave, stdout=slave, stderr=slave)
            os.close(slave)
            slave = -1
            code = process.wait(timeout=30)
            self.assertEqual(code, 0, self.calls())
        finally:
            if slave >= 0:
                os.close(slave)
            os.close(master)
        docker_calls = [call for call in self.calls() if call[0] == "docker"]
        self.assertTrue(any("add-user" in call for call in docker_calls), docker_calls)
        self.assertTrue(any("up" in call for call in docker_calls), docker_calls)

    def test_foreign_chain_is_never_overwritten(self):
        original = {"DOCKER-USER": [], "INPUT": [],
                    "PADO_GS_LOCAL": [["-j", "ACCEPT"]]}
        self.rules_path.write_text(json.dumps(original), encoding="utf-8")
        result = self.invoke("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not owned", result.stderr)
        self.assertEqual(self.rules(), original)
        self.assertFalse(any("up" in call for call in self.calls() if call[0] == "docker"))

    def test_marked_interrupted_setup_can_be_recovered(self):
        interrupted = {"DOCKER-USER": [], "INPUT": [],
                       "PADO_GS_LOCAL": [["-m", "comment", "--comment",
                                          "pado-gs-local-http", "-j", "RETURN"]]}
        self.rules_path.write_text(json.dumps(interrupted), encoding="utf-8")
        self.run_launcher("start")
        self.assertEqual(len(self.rules()["DOCKER-USER"]), 1)
        self.assertEqual(len(self.rules()["PADO_GS_LOCAL"]), 5)
        self.run_launcher("stop")
        self.assertNotIn("PADO_GS_LOCAL", self.rules())

    def test_cleanup_requires_confirmation(self):
        self.run_launcher("start")
        before_cleanup = len(self.calls())
        result = self.invoke("cleanup", input_text="DELETE\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("down" in call for call in self.calls()[before_cleanup:]
                             if call[0] == "docker"))
        self.assertIn("PADO_GS_LOCAL", self.rules())
        self.assertEqual(self.sentinel.read_text(encoding="utf-8"), "keep repo output")

    def test_unapproved_non_dgs_port_refuses_to_start_before_publish(self):
        self.compose_policy["services"]["ros-api"]["ports"] = [
            {"host_ip": "192.168.45.128", "published": "8081", "target": 8081}]
        self.compose_json_path.write_text(json.dumps(self.compose_policy), encoding="utf-8")
        result = self.invoke("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DGS-only", result.stderr)
        self.assertFalse(any("up" in call for call in self.calls() if call[0] == "docker"))
        self.assertNotIn("PADO_GS_LOCAL", self.rules())

    def test_gateway_acl_mismatch_refuses_to_start_before_publish(self):
        self.compose_policy["services"]["dgs-ops-gateway"]["environment"][
            "PADO_GATEWAY_OPERATOR_CIDR"] = "192.168.45.0/24"
        self.compose_json_path.write_text(json.dumps(self.compose_policy), encoding="utf-8")
        result = self.invoke("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DGS-only", result.stderr)
        self.assertFalse(any("up" in call for call in self.calls()
                             if call[0] == "docker"))
        self.assertNotIn("PADO_GS_LOCAL", self.rules())

    def test_docker_27_refuses_before_changing_stack_or_firewall(self):
        self.env["PADO_LAUNCHER_TEST_DOCKER_VERSION"] = "27.5.1"
        result = self.invoke("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("28", result.stderr)
        self.assertFalse(any("stop" in call or "up" in call
                             for call in self.calls() if call[0] == "docker"))
        self.assertEqual(self.rules(), {"DOCKER-USER": [], "INPUT": []})

    def test_documentation_address_cannot_be_used_as_lan_bind(self):
        self.env["PADO_LOCAL_BIND_IP"] = "192.0.2.1"
        result = self.invoke("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid LAN", result.stderr)
        self.assertFalse(any("up" in call for call in self.calls() if call[0] == "docker"))
        self.assertNotIn("PADO_GS_LOCAL", self.rules())

    def test_unisolated_live_docker_network_refuses_before_port_publish(self):
        for network in self.networks:
            if network["Name"].endswith("_portal_ros"):
                network["Internal"] = False
        self.network_json_path.write_text(json.dumps(self.networks), encoding="utf-8")
        result = self.invoke("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("network", result.stderr.lower())
        self.assertFalse(any("up" in call for call in self.calls() if call[0] == "docker"))
        self.assertNotIn("PADO_GS_LOCAL", self.rules())
        self.assertEqual(self.rules()["INPUT"], [])

    def test_status_fails_when_outbound_host_rule_is_missing(self):
        self.run_launcher("start")
        rules = self.rules()
        rules["INPUT"] = []
        self.rules_path.write_text(json.dumps(rules), encoding="utf-8")
        status = self.invoke("status")
        self.assertNotEqual(status.returncode, 0)
        self.assertIn("rule", status.stdout.lower() + status.stderr.lower())

    def test_status_fails_if_an_earlier_accept_bypasses_policy(self):
        for chain in ("DOCKER-USER", "PADO_GS_LOCAL"):
            with self.subTest(chain=chain):
                self.run_launcher("start")
                rules = self.rules()
                rules[chain].insert(0, ["-j", "ACCEPT"])
                self.rules_path.write_text(json.dumps(rules), encoding="utf-8")
                status = self.invoke("status")
                self.assertNotEqual(status.returncode, 0)
                self.assertIn("rule", status.stdout.lower() + status.stderr.lower())
                # Reset only the synthetic foreign rule before the next case.
                rules[chain].pop(0)
                self.rules_path.write_text(json.dumps(rules), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
