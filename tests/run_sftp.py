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
    if len(sys.argv) != 4:
        return 2
    executable = pathlib.Path(sys.argv[1]).resolve()
    stage6_executable = pathlib.Path(sys.argv[2]).resolve()
    chart = json.loads(pathlib.Path(sys.argv[3]).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="pado-sftp-") as temp:
        root = pathlib.Path(temp)
        source = root / "source"
        inbox = root / "inbox"
        outbox = root / "outbox"
        download = root / "download"
        wrong = root / "wrong"
        mps_inbox = root / "mps_inbox"
        tle_outbox = root / "tle_outbox"
        tle_download = root / "tle_download"
        route_source = root / "route_source"
        route_remote = root / "route_remote"
        route_download = root / "route_download"
        for directory in (source, inbox, outbox, download, wrong,
                          mps_inbox, tle_outbox, tle_download, route_source,
                          route_remote, route_download):
            directory.mkdir()
        stage6_ids = {"N05", "N08", "N09", "N10", "N11", "N12",
                      "N13", "N14", "N15", "N16", "N17", "N18"}
        stage6_flows = [flow for flow in chart["approved_flows"]
                        if flow["id"] in stage6_ids]
        assert {flow["id"] for flow in stage6_flows} == stage6_ids
        for flow in stage6_flows:
            item = flow["id"]
            route_data = f"stage6-{item}".encode("ascii")
            route_file, route_manifest = fixture(route_source, f"{item}.bin",
                                                 route_data, flow["types"][0],
                                                 flow["data_from"], flow["data_to"])
            remote_dir = route_remote / item
            remote_dir.mkdir()
            (route_download / item).mkdir()
            if flow["transport"] == "SFTP_PULL":
                (remote_dir / route_file.name).write_bytes(route_file.read_bytes())
                (remote_dir / route_manifest.name).write_bytes(route_manifest.read_bytes())
                remote_dir.chmod(0o555)
        ack, ack_manifest = fixture(source, "ack.bin", b"ack-test", "ACK", "ros", "mps")
        raw, raw_manifest = fixture(source, "raw.bin", b"crest-test", "CREST_RAW",
                                    "dgs-gs", "dgs-archive")
        (outbox / raw.name).write_bytes(raw.read_bytes())
        (outbox / raw_manifest.name).write_bytes(raw_manifest.read_bytes())
        mission, mission_manifest = fixture(source, "mission.bin",
                                            b"test-only-mission", "MISSION_REQUEST",
                                            "sdc", "dgs-archive")
        tle, tle_manifest = fixture(source, "tle.bin", b"test-only-tle",
                                    "NORAD_TLE", "dgs-net", "fds")
        (tle_outbox / tle.name).write_bytes(tle.read_bytes())
        (tle_outbox / tle_manifest.name).write_bytes(tle_manifest.read_bytes())
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
                str(download), str(wrong), str(mission),
                str(mission_manifest), str(mps_inbox), str(tle_outbox),
                str(tle_download),
            ], check=False, timeout=30).returncode
            if result != 0:
                return result
            assert (inbox / "ack.bin").read_bytes() == b"ack-test"
            assert (inbox / "ack.bin.manifest.json").is_file()
            assert (download / "raw.bin").read_bytes() == b"crest-test"
            assert (download / "raw.bin.manifest.json").is_file()
            assert not list(wrong.iterdir())
            assert (inbox / "mission.bin").read_bytes() == b"test-only-mission"
            assert (mps_inbox / "mission.bin").read_bytes() == b"test-only-mission"
            forwarded = json.loads((mps_inbox / "mission.bin.manifest.json").read_text())
            assert forwarded["from"] == "dgs-archive" and forwarded["to"] == "mps"
            assert (tle_download / "tle.bin").read_bytes() == b"test-only-tle"
            route_result = subprocess.run([
                str(stage6_executable), "127.0.0.2", str(port), username,
                str(root / "client"), pin, str(route_source),
                str(route_remote), str(route_download),
            ], check=False, timeout=30).returncode
            if route_result != 0:
                return route_result
            for flow in stage6_flows:
                item = flow["id"]
                target = ((route_remote if flow["transport"] == "SFTP_PUSH"
                           else route_download) / item)
                assert (target / f"{item}.bin").read_bytes() == f"stage6-{item}".encode("ascii")
                assert json.loads((target / f"{item}.bin.manifest.json").read_text())[
                    "type"] == flow["types"][0]
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
