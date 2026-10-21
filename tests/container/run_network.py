"""Exercise the stage-2 TLS and SFTP libraries across an isolated Docker bridge."""

import argparse
import base64
import hashlib
import json
import pathlib
import subprocess
import tempfile
import time
import uuid


IMAGE = "pado-stage2-test:local"
PROBE = "/src/build-container/gs_container_net_probe"
SFTP_TEST = "/src/build-container/gs_sftp_tests"


def run(*args: str, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, text=True,
                          capture_output=quiet)


def openssl(root: pathlib.Path, *args: str) -> None:
    subprocess.run(("openssl", *args), cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def keygen(root: pathlib.Path, name: str) -> None:
    subprocess.run(("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", name),
                   cwd=root, check=True, stdout=subprocess.DEVNULL)


def tls_identity(root: pathlib.Path, name: str, ip: str) -> None:
    openssl(root, "req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
            "-nodes", "-keyout", f"{name}.key", "-out", f"{name}.csr",
            "-subj", f"/CN={name}")
    (root / f"{name}.ext").write_text(
        f"subjectAltName=IP:{ip}\nextendedKeyUsage=serverAuth,clientAuth\n",
        encoding="ascii")
    openssl(root, "x509", "-req", "-in", f"{name}.csr", "-CA", "ca.crt",
            "-CAkey", "ca.key", "-CAcreateserial", "-out", f"{name}.crt",
            "-days", "1", "-extfile", f"{name}.ext")


def manifest(directory: pathlib.Path, name: str, data: bytes,
             kind: str, sender: str, receiver: str) -> None:
    (directory / name).write_bytes(data)
    (directory / f"{name}.manifest.json").write_text(json.dumps({
        "file": name, "type": kind, "from": sender, "to": receiver,
        "version": 1, "created": "2026-10-21T00:00:00Z",
        "sha256": hashlib.sha256(data).hexdigest(),
    }), encoding="ascii")


def docker_ip(name: str) -> str:
    result = run("docker", "inspect", "-f",
                 "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                 name, quiet=True)
    ip = result.stdout.strip()
    if not ip:
        raise RuntimeError(f"Docker did not assign an IPv4 address to {name}")
    return ip


def wait_for_pid(name: str, path: str) -> None:
    for _ in range(50):
        result = subprocess.run(("docker", "exec", name, "test", "-f", path),
                                check=False, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            return
        time.sleep(0.1)
    raise RuntimeError("container SFTP server did not start")


def tls_test(server: str, client: str, server_ip: str, client_ip: str,
             wrong_peer: bool) -> None:
    expected = "127.0.0.9" if wrong_peer else client_ip
    server_command = ("docker", "exec", server, PROBE, "server", server_ip,
                      expected, "/tls/server.crt", "/tls/server.key", "/tls/ca.crt",
                      "25252")
    listener = subprocess.Popen(server_command, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(0.2)
        response = subprocess.run(("docker", "exec", client, PROBE, "client",
                                   server_ip, client_ip, server_ip, "/tls/client.crt",
                                   "/tls/client.key", "/tls/ca.crt", "25252"),
                                  check=False, capture_output=True, text=True, timeout=10)
        stdout, stderr = listener.communicate(timeout=10)
        expected_success = not wrong_peer
        if (response.returncode == 0) != expected_success or \
                (listener.returncode == 0) != expected_success:
            raise RuntimeError(f"TLS {'rejection' if wrong_peer else 'exchange'} failed: "
                               f"client={response.returncode} {response.stderr}; "
                               f"server={listener.returncode} {stdout} {stderr}")
    finally:
        if listener.poll() is None:
            listener.kill()
            listener.wait()


def prepare_sftp(server_dir: pathlib.Path, client_dir: pathlib.Path) -> str:
    source = client_dir / "source"
    inbox = server_dir / "inbox"
    outbox = server_dir / "outbox"
    for directory in (source, inbox, outbox, client_dir / "download",
                      client_dir / "wrong"):
        directory.mkdir()
    inbox.chmod(0o777)
    manifest(source, "ack.bin", b"ack-test", "ACK", "ros", "mps")
    manifest(source, "raw.bin", b"crest-test", "CREST_RAW",
             "dgs-gs", "dgs-archive")
    manifest(outbox, "raw.bin", b"crest-test", "CREST_RAW",
             "dgs-gs", "dgs-archive")
    outbox.chmod(0o555)
    keygen(server_dir, "host")
    keygen(client_dir, "client")
    (server_dir / "authorized_keys").write_bytes(
        (client_dir / "client.pub").read_bytes())
    # The sshd child reads this file as pado-test; it contains only a public key.
    (server_dir / "authorized_keys").chmod(0o644)
    (server_dir / "sshd_config").write_text("\n".join([
        "Port 2222", "ListenAddress 0.0.0.0", "HostKey /fixture/host",
        "PidFile /run/sshd-stage2.pid", "UsePAM no", "StrictModes no",
        "PasswordAuthentication no", "KbdInteractiveAuthentication no",
        "PubkeyAuthentication yes", "AuthenticationMethods publickey",
        "AuthorizedKeysFile /fixture/authorized_keys", "AllowUsers pado-test",
        "PermitRootLogin no", "Subsystem sftp internal-sftp", "LogLevel VERBOSE",
    ]) + "\n", encoding="ascii")
    raw_key = base64.b64decode((server_dir / "host.pub").read_text().split()[1])
    return hashlib.sha256(raw_key).hexdigest()


def isolation_test(image: str, network: str, server_ip: str) -> None:
    code = ("import socket; "
            "s=socket.socket(); s.settimeout(2); "
            f"r=s.connect_ex(('{server_ip}', 2222)); "
            "s.close(); raise SystemExit(0 if r else 1)")
    run("docker", "run", "--rm", "--network", network, image,
        "python3", "-c", code, quiet=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="build the test image")
    parser.add_argument("--image", default=IMAGE)
    args = parser.parse_args()
    root = pathlib.Path(__file__).resolve().parents[2]
    if args.build:
        run("docker", "build", "-t", args.image, "-f",
            str(root / "tests/container/Dockerfile"), str(root))
    suffix = uuid.uuid4().hex[:12]
    network = f"pado-stage2-{suffix}"
    isolated = f"pado-stage2-isolated-{suffix}"
    server = f"pado-stage2-server-{suffix}"
    client = f"pado-stage2-client-{suffix}"
    created_networks: list[str] = []
    created_containers: list[str] = []
    sshd: subprocess.Popen[str] | None = None
    with tempfile.TemporaryDirectory(prefix="pado-stage2-docker-") as temp:
        fixture = pathlib.Path(temp)
        tls = fixture / "tls"
        server_dir = fixture / "server"
        client_dir = fixture / "client"
        for directory in (tls, server_dir, client_dir):
            directory.mkdir()
        try:
            run("docker", "network", "create", "--internal", network, quiet=True)
            created_networks.append(network)
            run("docker", "network", "create", "--internal", isolated, quiet=True)
            created_networks.append(isolated)
            for name, directory in ((server, server_dir), (client, client_dir)):
                run("docker", "run", "-d", "--network", network, "--name", name,
                    "--mount", f"type=bind,source={tls},target=/tls,readonly",
                    "--mount", f"type=bind,source={directory},target=/fixture",
                    args.image, "sleep", "infinity", quiet=True)
                created_containers.append(name)
            server_ip = docker_ip(server)
            client_ip = docker_ip(client)
            openssl(tls, "req", "-x509", "-newkey", "ec", "-pkeyopt",
                    "ec_paramgen_curve:P-256", "-nodes", "-keyout", "ca.key",
                    "-out", "ca.crt", "-days", "1", "-subj", "/CN=PADO-Test-CA")
            tls_identity(tls, "server", server_ip)
            tls_identity(tls, "client", client_ip)
            tls_test(server, client, server_ip, client_ip, False)
            tls_test(server, client, server_ip, client_ip, True)
            print("mTLS exchange and wrong-peer rejection: PASS")
            pin = prepare_sftp(server_dir, client_dir)
            sshd = subprocess.Popen(("docker", "exec", server, "/usr/sbin/sshd",
                                     "-D", "-e", "-f", "/fixture/sshd_config"),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True)
            wait_for_pid(server, "/run/sshd-stage2.pid")
            (client_dir / "known_hosts").write_text(
                f"[{server_ip}]:2222 {(server_dir / 'host.pub').read_text()}",
                encoding="ascii")
            probe = subprocess.run(("docker", "exec", client, "ssh", "-i",
                                    "/fixture/client", "-p", "2222", "-o",
                                    "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o",
                                    "StrictHostKeyChecking=yes", "-o",
                                    "UserKnownHostsFile=/fixture/known_hosts",
                                    f"pado-test@{server_ip}", "true"),
                                   check=False, capture_output=True, text=True,
                                   timeout=10)
            if probe.returncode != 0:
                sshd.terminate()
                _, log = sshd.communicate(timeout=5)
                raise RuntimeError(f"OpenSSH probe failed: {probe.stderr}\n{log}")
            run("docker", "exec", client, SFTP_TEST, server_ip, "2222", "pado-test",
                "/fixture/client", pin, "/fixture/source/ack.bin",
                "/fixture/source/ack.bin.manifest.json", "/fixture/source/raw.bin",
                "/fixture/source/raw.bin.manifest.json", "/fixture/inbox",
                "/fixture/outbox", "/fixture/download", "/fixture/wrong")
            run("docker", "exec", server, "python3", "-c",
                "from pathlib import Path; assert Path('/fixture/inbox/ack.bin').read_bytes() == b'ack-test'")
            run("docker", "exec", client, "python3", "-c",
                "from pathlib import Path; assert Path('/fixture/download/raw.bin').read_bytes() == b'crest-test'; assert not list(Path('/fixture/wrong').iterdir())")
            print("SFTP upload, raw pull and rejection cases: PASS")
            isolation_test(args.image, isolated, server_ip)
            print("unconnected Docker network cannot reach SFTP listener: PASS")
            return 0
        finally:
            if sshd is not None and sshd.poll() is None:
                sshd.terminate()
                try:
                    sshd.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    sshd.kill()
                    sshd.communicate()
            for name in reversed(created_containers):
                subprocess.run(("docker", "rm", "-f", name), check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for name in reversed(created_networks):
                subprocess.run(("docker", "network", "rm", name), check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if (server_dir / "outbox").exists():
                (server_dir / "outbox").chmod(0o755)


if __name__ == "__main__":
    raise SystemExit(main())
