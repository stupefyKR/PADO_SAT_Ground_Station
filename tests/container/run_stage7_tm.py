"""Run the existing simulator TM binaries across two disposable Docker containers.

This is a lab-only application boundary test, not an operational service image.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import struct
import subprocess
import tempfile
import uuid


ROOT = pathlib.Path(__file__).resolve().parents[2]
IMAGE = "pado-stage2-test:local"
BIN = "/src/build-container"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=30)


def openssl(root: pathlib.Path, *args: str) -> None:
    subprocess.run(("openssl", *args), cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=30)


def identity(root: pathlib.Path, name: str, ip: str) -> None:
    openssl(root, "req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
            "-nodes", "-keyout", f"{name}.key", "-out", f"{name}.csr",
            "-subj", f"/CN={name}")
    (root / f"{name}.ext").write_text(
        f"subjectAltName=IP:{ip}\nextendedKeyUsage=serverAuth,clientAuth\n",
        encoding="ascii")
    openssl(root, "x509", "-req", "-in", f"{name}.csr", "-CA", "ca.crt",
            "-CAkey", "ca.key", "-CAcreateserial", "-out", f"{name}.crt",
            "-days", "1", "-extfile", f"{name}.ext")


def container_ip(name: str) -> str:
    return run("docker", "inspect", "-f",
               "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
               name).stdout.strip()


def records(path: pathlib.Path) -> list[tuple[int, bytes]]:
    data = path.read_bytes()
    parsed = []
    while data:
        if len(data) < 5:
            raise RuntimeError("truncated simulator record")
        size = struct.unpack_from(">I", data, 1)[0]
        if size > len(data) - 5:
            raise RuntimeError("invalid simulator record length")
        parsed.append((data[0], data[5:5 + size]))
        data = data[5 + size:]
    return parsed


def start_container(name: str, network: str, identity_dir: pathlib.Path,
                    data_dir: pathlib.Path, user: str) -> None:
    run("docker", "run", "-d", "--name", name, "--network", network,
        "--user", user, "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--tmpfs", "/tmp",
        "--mount", f"type=bind,source={identity_dir},target=/tls,readonly",
        "--mount", f"type=bind,source={data_dir},target=/data",
        "--log-driver", "journald", IMAGE, "sleep", "infinity")
    state = json.loads(run("docker", "inspect", name).stdout)[0]
    if state["HostConfig"]["PortBindings"] or not state["HostConfig"]["ReadonlyRootfs"]:
        raise RuntimeError(f"unsafe container settings: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    if os.name != "posix":
        raise RuntimeError("this Docker bind-mount test requires a Linux host")
    if args.build:
        run("docker", "build", "-t", IMAGE, "-f",
            str(ROOT / "tests/container/Dockerfile"), str(ROOT))
    suffix = uuid.uuid4().hex[:10]
    network = f"pado-s7-tm-{suffix}"
    dgs = f"pado-s7-tm-dgs-{suffix}"
    ros = f"pado-s7-tm-ros-{suffix}"
    created: list[str] = []
    with tempfile.TemporaryDirectory(prefix="pado-s7-tm-") as temporary:
        root = pathlib.Path(temporary)
        ca = root / "ca"
        ca.mkdir()
        for role in ("dgs", "ros"):
            (root / role / "tls").mkdir(parents=True)
            (root / role / "data").mkdir()
        try:
            run("docker", "network", "create", "--internal", network)
            user = f"{os.getuid()}:{os.getgid()}"
            for role, name in (("dgs", dgs), ("ros", ros)):
                start_container(name, network, root / role / "tls",
                                root / role / "data", user)
                created.append(name)
            dgs_ip, ros_ip = container_ip(dgs), container_ip(ros)
            if not dgs_ip or not ros_ip or dgs_ip == ros_ip:
                raise RuntimeError("Docker did not assign distinct container IPv4 addresses")
            openssl(ca, "req", "-x509", "-newkey", "ec", "-pkeyopt",
                    "ec_paramgen_curve:P-256", "-nodes", "-keyout", "ca.key",
                    "-out", "ca.crt", "-days", "1", "-subj", "/CN=PADO-Test-CA")
            for role, ip in (("dgs", dgs_ip), ("ros", ros_ip)):
                identity(ca, role, ip)
                for filename in (f"{role}.crt", f"{role}.key", "ca.crt"):
                    destination = root / role / "tls" / filename
                    shutil.copyfile(ca / filename, destination)
                    destination.chmod(0o600 if filename.endswith(".key") else 0o644)
            fixture = subprocess.run(("docker", "run", "--rm", "--network", "none",
                                      IMAGE, f"{BIN}/gs_fake_kari", "--sim-only"),
                                     check=True, capture_output=True, timeout=30).stdout
            gateway = subprocess.Popen(("docker", "exec", "-i", dgs,
                                        f"{BIN}/gs_dgs_gs_sim", "--sim-only",
                                        dgs_ip, ros_ip, "7001", "/tls/dgs.crt",
                                        "/tls/dgs.key", "/tls/ca.crt",
                                        "/data/quarantine.bin"),
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE)
            receiver = subprocess.Popen(("docker", "exec", ros,
                                         f"{BIN}/gs_ros_core_sim", "--sim-only",
                                         dgs_ip, ros_ip, "7001", "/tls/ros.crt",
                                         "/tls/ros.key", "/tls/ca.crt",
                                         "/data/received.bin"),
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                _, dgs_err = gateway.communicate(input=fixture, timeout=20)
                ros_out, ros_err = receiver.communicate(timeout=20)
            finally:
                for process in (gateway, receiver):
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
            if gateway.returncode or receiver.returncode:
                raise RuntimeError(f"container TM path failed: dgs={gateway.returncode} "
                                   f"{dgs_err}; ros={receiver.returncode} {ros_err}")
            received = records(root / "ros" / "data" / "received.bin")
            quarantine = records(root / "dgs" / "data" / "quarantine.bin")
            if [tag for tag, _ in received] != [1, 1] or len(quarantine) != 5:
                raise RuntimeError("container TM records differ from the simulator contract")
            ros_events = [json.loads(line) for line in ros_out.splitlines()]
            dgs_events = [json.loads(line) for line in dgs_err.splitlines()]
            if sum(e["event"] == "tm_recorded" for e in ros_events) != 2 or \
                    sum(e["event"] == "tm_quarantined" for e in dgs_events) != 5:
                raise RuntimeError("container TM event log count is wrong")
            unauthorized = subprocess.Popen(("docker", "exec", "-i", dgs,
                                             f"{BIN}/gs_dgs_gs_sim", "--sim-only",
                                             dgs_ip, ros_ip, "7001", "/tls/dgs.crt",
                                             "/tls/dgs.key", "/tls/ca.crt",
                                             "/data/unauthorized.bin"),
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE)
            try:
                attacker_code = ("import socket,sys,time\n"
                                 "for _ in range(40):\n"
                                 " s=socket.socket(); s.settimeout(1)\n"
                                 " try:\n"
                                 "  s.connect((sys.argv[1],7001)); s.close(); sys.exit(0)\n"
                                 " except OSError:\n"
                                 "  s.close(); time.sleep(.05)\n"
                                 "sys.exit(1)\n")
                run("docker", "run", "--rm", "--network", network,
                    "--read-only", "--cap-drop", "ALL", "--security-opt",
                    "no-new-privileges:true", IMAGE, "python3", "-c",
                    attacker_code, dgs_ip)
                unauthorized.communicate(input=b"", timeout=10)
            finally:
                if unauthorized.poll() is None:
                    unauthorized.kill()
                    unauthorized.communicate()
            if unauthorized.returncode == 0 or \
                    (root / "dgs" / "data" / "unauthorized.bin").exists():
                raise RuntimeError("DGS-GS accepted an unapproved peer")
            print("simulator DGS-GS -> ROS across two restricted containers: PASS")
            print("two TM records, five quarantines, wrong-peer rejection, "
                  "mTLS, no published ports")
            return 0
        finally:
            for name in reversed(created):
                subprocess.run(("docker", "rm", "-f", name), check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(("docker", "network", "rm", network), check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    raise SystemExit(main())
