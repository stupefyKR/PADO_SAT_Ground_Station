"""Run the lab-only fake KARI -> DGS-GS -> ROS TM path with ephemeral mTLS IDs."""

import json
import pathlib
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import zlib


def openssl(root: pathlib.Path, *args: str) -> None:
    subprocess.run(("openssl", *args), cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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


def records(path: pathlib.Path) -> list[tuple[int, bytes]]:
    data = path.read_bytes()
    result = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 5:
            raise RuntimeError("truncated record header")
        tag = data[offset]
        length = struct.unpack_from(">I", data, offset + 1)[0]
        offset += 5
        if length > len(data) - offset:
            raise RuntimeError("truncated record body")
        result.append((tag, data[offset:offset + length]))
        offset += length
    return result


def check_frame(frame: bytes, sequence: int, payload: bytes) -> None:
    if len(frame) != 22 + len(payload) or frame[:8] != b"PSTSIM03" or \
            frame[8:10] != b"\x01\x01" or frame[10:12] != b"~W" or \
            struct.unpack_from(">H", frame, 12)[0] != len(payload) or \
            struct.unpack_from(">I", frame, 14)[0] != sequence or \
            frame[18:-4] != payload or \
            struct.unpack_from(">I", frame, len(frame) - 4)[0] != zlib.crc32(frame[:-4]):
        raise RuntimeError("ROS recorded an unexpected simulator frame")


def main() -> int:
    if len(sys.argv) != 4:
        return 2
    fake, dgs, ros = (str(pathlib.Path(value).resolve()) for value in sys.argv[1:])
    with tempfile.TemporaryDirectory(prefix="pado-stage3-") as temporary:
        root = pathlib.Path(temporary)
        openssl(root, "req", "-x509", "-newkey", "ec", "-pkeyopt",
                "ec_paramgen_curve:P-256", "-nodes", "-keyout", "ca.key",
                "-out", "ca.crt", "-days", "1", "-subj", "/CN=PADO-Test-CA")
        identity(root, "dgs", "127.0.0.2")
        identity(root, "ros", "127.0.0.3")
        with socket.socket() as probe:
            probe.bind(("127.0.0.2", 0))
            port = str(probe.getsockname()[1])
        fixture = subprocess.run((fake, "--sim-only"), check=True,
                                 capture_output=True).stdout
        quarantine = root / "quarantine.bin"
        stored = root / "ros.bin"
        gateway = subprocess.Popen((dgs, "--sim-only", "127.0.0.2", "127.0.0.3",
                                    port, str(root / "dgs.crt"), str(root / "dgs.key"),
                                    str(root / "ca.crt"), str(quarantine)),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        receiver = subprocess.Popen((ros, "--sim-only", "127.0.0.2", "127.0.0.3",
                                     port, str(root / "ros.crt"), str(root / "ros.key"),
                                     str(root / "ca.crt"), str(stored)),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            dgs_out, dgs_err = gateway.communicate(input=fixture, timeout=15)
            ros_out, ros_err = receiver.communicate(timeout=15)
        finally:
            for process in (gateway, receiver):
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        if gateway.returncode != 0 or receiver.returncode != 0:
            raise RuntimeError(f"TM path failed: dgs={gateway.returncode} {dgs_out} "
                               f"{dgs_err}; ros={receiver.returncode} {ros_out} {ros_err}")
        received = records(stored)
        rejected = records(quarantine)
        if len(received) != 2 or [tag for tag, _ in received] != [1, 1]:
            raise RuntimeError("ROS did not record exactly the two valid frames")
        check_frame(received[0][1], 1, b"SIM-TM-A")
        check_frame(received[1][1], 4, b"SIM-TM-B")
        if [tag for tag, _ in rejected] != [6, 5, 2, 1, 1] or \
                [len(body) for _, body in rejected][-2:] != [4, 4]:
            raise RuntimeError("DGS-GS did not quarantine all invalid inputs")
        if stat.S_IMODE(stored.stat().st_mode) != 0o600 or \
                stat.S_IMODE(quarantine.stat().st_mode) != 0o600:
            raise RuntimeError("TM record files are not private")
        ros_events = [json.loads(line) for line in ros_out.splitlines()]
        dgs_events = [json.loads(line) for line in dgs_err.splitlines()]
        if sum(event["event"] == "tm_recorded" for event in ros_events) != 2 or \
                sum(event["event"] == "tm_quarantined" for event in dgs_events) != 5:
            raise RuntimeError("TM audit events do not match recorded results")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
