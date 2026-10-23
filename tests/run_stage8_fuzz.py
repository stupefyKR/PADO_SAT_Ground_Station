"""Run bounded ASan/UBSan libFuzzer campaigns with generated lab-only seeds."""

from __future__ import annotations

import argparse
import json
import pathlib
import struct
import subprocess
import tempfile
import zlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGETS = {
    "gs_manifest_fuzzer": "manifest",
    "gs_mps_catalog_fuzzer": "mps",
    "gs_tm_fuzzer": "tm",
    "gs_tle_fuzzer": "tle",
}


def tle_seed() -> bytes:
    first = bytearray(b" " * 69)
    second = bytearray(b" " * 69)
    first[:2], second[:2] = b"1 ", b"2 "
    first[2:7] = second[2:7] = b"12345"
    first[18:23] = b"26123"
    for line in (first, second):
        checksum = sum(value - 48 if 48 <= value <= 57 else value == 45
                       for value in line[:68]) % 10
        line[68] = 48 + checksum
    return bytes(first) + b"\n" + bytes(second)


def seeds(root: pathlib.Path) -> None:
    for name in TARGETS.values():
        (root / name).mkdir()
        (root / name / "empty").write_bytes(b"")
    manifest = {"file": "stage1-data.tmp", "type": "MISSION_SCHEDULE",
                "from": "mps", "to": "ros", "version": 3,
                "created": "2026-10-21T01:00:00Z",
                "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"}
    (root / "manifest" / "valid").write_bytes(json.dumps(manifest).encode("ascii"))
    (root / "mps" / "catalog").write_bytes(
        (ROOT / "interfaces/command_db/commands.json").read_bytes())
    payload = b"SIM-TM-A"
    frame = (b"PSTSIM03\x01\x01\x7e\x57" + struct.pack(">HI", len(payload), 1)
             + payload)
    (root / "tm" / "valid").write_bytes(frame + struct.pack(">I", zlib.crc32(frame)))
    (root / "tle" / "valid").write_bytes(tle_seed())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=pathlib.Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="pado-stage8-fuzz-") as temporary:
        root = pathlib.Path(temporary)
        seeds(root)
        for binary, corpus in TARGETS.items():
            executable = args.build / binary
            result = subprocess.run((str(executable), "-runs=20000",
                                     "-max_total_time=20", "-max_len=4096",
                                     "-timeout=5", f"-artifact_prefix={root}/",
                                     str(root / corpus)), capture_output=True,
                                    text=True, check=False, timeout=90)
            if result.returncode != 0:
                raise RuntimeError(f"{binary} failed ({result.returncode}):\n"
                                   f"{result.stdout}\n{result.stderr}")
            summary = next((line for line in reversed(result.stderr.splitlines())
                            if "DONE" in line or "Done" in line), "completed")
            print(f"{binary}: {summary}")


if __name__ == "__main__":
    main()
