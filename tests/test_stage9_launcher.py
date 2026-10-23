"""Exercise the local Stage 9 launcher without Docker or host firewall access."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


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
    if "account-status" in args:
        print(os.environ.get("PADO_LAUNCHER_TEST_ACCOUNT_STATUS", "configured"))
    elif args == ["info", "--format", "{{.ServerVersion}}"]:
        print("28.0.0")
    elif "--services" in args:
        services = ["portal", "ros-api", "mps-api", "ros-core-read",
                    "mps-core-read", "fds-synthetic-demo", "sim-ground", "sim-satellite"]
        if os.environ.get("PADO_LAUNCHER_TEST_SERVICES_INCOMPLETE") == "1":
            services.remove("mps-core-read")
        print("\n".join(services))
    elif "ps" in args:
        print("portal running 192.168.45.128:8080->8080/tcp")
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
        self.rules_path.write_text('{"DOCKER-USER": []}', encoding="utf-8")
        self.fixture_root = base / "checkout"
        (self.fixture_root / "deploy" / "stage9").mkdir(parents=True)
        (self.fixture_root / "out").mkdir()
        shutil.copyfile(LAUNCHER, self.fixture_root / "pado-gs.sh")
        shutil.copyfile(ROOT / "deploy" / "stage9" / "compose.local.yaml",
                        self.fixture_root / "deploy" / "stage9" / "compose.local.yaml")
        self.sentinel = self.fixture_root / "out" / "existing-output.txt"
        self.sentinel.write_text("keep repo output", encoding="utf-8")
        self.env = dict(os.environ,
                        PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
                        PADO_LAUNCHER_TEST_LOG=str(self.log_path),
                        PADO_LAUNCHER_TEST_IPTABLES=str(self.rules_path),
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
        self.assertTrue(any(call[0] == "curl" for call in first_calls), first_calls)
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

        self.run_launcher("start")
        self.assertEqual(self.rules(), first_rules, "start added duplicate firewall rules")

        self.run_launcher("stop")
        stop_calls = [call[1:] for call in self.calls()[len(first_calls):] if call[0] == "docker"]
        self.assertTrue(any("stop" in call for call in stop_calls), stop_calls)
        self.assertNotIn("PADO_GS_LOCAL", self.rules())
        self.assertFalse(any("PADO_GS_LOCAL" in " ".join(rule)
                             for rule in self.rules()["DOCKER-USER"]))
        forbidden = [call for call in self.calls() if call[0] == "ufw" and
                     any(arg in ("disable", "reset", "default") for arg in call[1:])]
        self.assertEqual(forbidden, [])

    def test_cleanup_removes_only_project_containers_images_and_volumes(self):
        self.run_launcher("start")
        self.run_launcher("cleanup", "--yes")
        docker_calls = [call[1:] for call in self.calls() if call[0] == "docker"]
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

    def test_http_probe_failure_stops_and_removes_firewall(self):
        self.env["PADO_LAUNCHER_TEST_CURL_FAIL"] = "1"
        result = self.invoke("start")
        self.assertNotEqual(result.returncode, 0)
        calls = self.calls()
        docker_calls = [call for call in calls if call[0] == "docker"]
        self.assertTrue(any(call[0] == "curl" for call in calls), calls)
        self.assertGreaterEqual(sum("stop" in call for call in docker_calls), 2, docker_calls)
        self.assertNotIn("PADO_GS_LOCAL", self.rules())
        self.assertEqual(self.rules()["DOCKER-USER"], [])

    def test_incomplete_service_set_stops_and_removes_firewall(self):
        self.env["PADO_LAUNCHER_TEST_SERVICES_INCOMPLETE"] = "1"
        result = self.invoke("start")
        self.assertNotEqual(result.returncode, 0)
        calls = self.calls()
        docker_calls = [call for call in calls if call[0] == "docker"]
        self.assertTrue(any("--services" in call for call in docker_calls), docker_calls)
        self.assertGreaterEqual(sum("stop" in call for call in docker_calls), 2, docker_calls)
        self.assertFalse(any(call[0] == "curl" for call in calls), calls)
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
        original = {"DOCKER-USER": [], "PADO_GS_LOCAL": [["-j", "ACCEPT"]]}
        self.rules_path.write_text(json.dumps(original), encoding="utf-8")
        result = self.invoke("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not owned", result.stderr)
        self.assertEqual(self.rules(), original)
        self.assertFalse(any("up" in call for call in self.calls() if call[0] == "docker"))

    def test_marked_interrupted_setup_can_be_recovered(self):
        interrupted = {"DOCKER-USER": [],
                       "PADO_GS_LOCAL": [["-m", "comment", "--comment",
                                          "pado-gs-local-http", "-j", "RETURN"]]}
        self.rules_path.write_text(json.dumps(interrupted), encoding="utf-8")
        self.run_launcher("start")
        self.assertEqual(len(self.rules()["DOCKER-USER"]), 1)
        self.assertEqual(len(self.rules()["PADO_GS_LOCAL"]), 3)
        self.run_launcher("stop")
        self.assertNotIn("PADO_GS_LOCAL", self.rules())

    def test_cleanup_requires_confirmation(self):
        self.run_launcher("start")
        result = self.invoke("cleanup", input_text="DELETE\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("down" in call for call in self.calls() if call[0] == "docker"))
        self.assertIn("PADO_GS_LOCAL", self.rules())
        self.assertEqual(self.sentinel.read_text(encoding="utf-8"), "keep repo output")


if __name__ == "__main__":
    unittest.main()
