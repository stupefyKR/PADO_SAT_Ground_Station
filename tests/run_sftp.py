"""Start a temporary key-only SFTP server and test approved upload/pull paths."""

import base64
import hashlib
import json
import os
import pathlib
import pwd
import socket
import subprocess
import sys
import tempfile
import time


def run(*args: str, cwd: pathlib.Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def fixture(directory: pathlib.Path, name: str, data: bytes,
            kind: str, sender: str, receiver: str) -> tuple[pathlib.Path, pathlib.Path]:
    data_path = directory / name
    manifest_path = directory / f"{name}.manifest.json"
    data_path.write_bytes(data)
    manifest_path.write_text(json.dumps({
        "file": name, "type": kind, "from": sender, "to": receiver,
        "version": 1, "created": "2026-10-21T00:00:00Z",
        "sha256": hashlib.sha256(data).hexdigest(),
    }), encoding="ascii")
    return data_path, manifest_path


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    executable = pathlib.Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="pado-sftp-") as temp:
        root = pathlib.Path(temp)
        source = root / "source"
        inbox = root / "inbox"
        outbox = root / "outbox"
        download = root / "download"
        wrong = root / "wrong"
        for directory in (source, inbox, outbox, download, wrong):
            directory.mkdir()
        ack, ack_manifest = fixture(source, "ack.bin", b"ack-test", "ACK", "ros", "mps")
        raw, raw_manifest = fixture(source, "raw.bin", b"crest-test", "CREST_RAW",
                                    "dgs-gs", "dgs-archive")
        (outbox / raw.name).write_bytes(raw.read_bytes())
        (outbox / raw_manifest.name).write_bytes(raw_manifest.read_bytes())
        outbox.chmod(0o555)
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", "host", cwd=root)
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", "client", cwd=root)
        (root / "authorized_keys").write_bytes((root / "client.pub").read_bytes())
        (root / "authorized_keys").chmod(0o600)
        raw_key = base64.b64decode((root / "host.pub").read_text().split()[1])
        pin = hashlib.sha256(raw_key).hexdigest()
        username = pwd.getpwuid(os.getuid()).pw_name
        with socket.socket() as probe:
            probe.bind(("127.0.0.2", 0))
            port = probe.getsockname()[1]
        config = root / "sshd_config"
        config.write_text("\n".join([
            f"Port {port}", "ListenAddress 127.0.0.2", f"HostKey {root / 'host'}",
            f"PidFile {root / 'sshd.pid'}", "UsePAM yes", "StrictModes no",
            "PasswordAuthentication no", "KbdInteractiveAuthentication no",
            "PubkeyAuthentication yes", "AuthenticationMethods publickey",
            f"AuthorizedKeysFile {root / 'authorized_keys'}",
            f"AllowUsers {username}", "Subsystem sftp internal-sftp", "LogLevel VERBOSE",
        ]) + "\n", encoding="ascii")
        run("sudo", "install", "-d", "-m", "0755", "/run/sshd", cwd=root)
        server = subprocess.Popen(["sudo", "-n", "/usr/sbin/sshd", "-D", "-e", "-f",
                                   str(config)], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError("temporary sshd exited")
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.2", port)) == 0:
                        break
                time.sleep(0.05)
            else:
                raise RuntimeError("temporary sshd did not start")
            (root / "known_hosts").write_text(
                f"[127.0.0.2]:{port} {(root / 'host.pub').read_text()}", encoding="ascii")
            ssh_probe = subprocess.run([
                "ssh", "-i", str(root / "client"), "-p", str(port),
                "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                "-o", "StrictHostKeyChecking=yes", "-o",
                f"UserKnownHostsFile={root / 'known_hosts'}",
                f"{username}@127.0.0.2", "true",
            ], check=False, capture_output=True, text=True, timeout=10)
            if ssh_probe.returncode != 0:
                print(f"OpenSSH probe failed: {ssh_probe.stderr}", file=sys.stderr)
                server.terminate()
                server.wait(timeout=5)
                print(server.stderr.read().decode(errors="replace"), file=sys.stderr)
                return 1
            result = subprocess.run([
                str(executable), "127.0.0.2", str(port), username,
                str(root / "client"), pin, str(ack), str(ack_manifest),
                str(raw), str(raw_manifest), str(inbox), str(outbox),
                str(download), str(wrong),
            ], check=False, timeout=30).returncode
            if result != 0:
                return result
            assert (inbox / "ack.bin").read_bytes() == b"ack-test"
            assert (inbox / "ack.bin.manifest.json").is_file()
            assert (download / "raw.bin").read_bytes() == b"crest-test"
            assert (download / "raw.bin.manifest.json").is_file()
            assert not list(wrong.iterdir())
            return 0
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
