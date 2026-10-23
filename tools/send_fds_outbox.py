"""Retry staged FDS N13/N15 pairs over pinned-host-key SFTP.

Receipt files mean only that the SFTP upload call succeeded. They are not an
MPS/Archive application ACK or acceptance of the preliminary orbit product.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

if __package__:
    from .stage_fds_outbox import FILES, FIELDS, _bytes, _unique_pairs
else:
    from stage_fds_outbox import FILES, FIELDS, _bytes, _unique_pairs


RUN_ID = re.compile(r"fds-[A-Za-z0-9_-]{1,64}\Z")


def load_peers(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= 8192:
        raise ValueError("bounded private peer configuration required")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid peer configuration") from error
    if not isinstance(value, dict) or set(value) != {"mps", "dgs-archive"}:
        raise ValueError("MPS and Archive peers required")
    for peer in value.values():
        if not isinstance(peer, dict) or set(peer) != {
                "host", "port", "user", "private_key", "host_key_sha256", "remote_dir"}:
            raise ValueError("incomplete SFTP peer")
        if (type(peer["port"]) is not int or not 1 <= peer["port"] <= 65535 or
                any(not isinstance(peer[k], str) or
                    not re.fullmatch(r"[A-Za-z0-9._:-]{1,255}", peer[k])
                    for k in ("host", "user")) or
                not isinstance(peer["host_key_sha256"], str) or
                not re.fullmatch(r"[0-9a-f]{64}", peer["host_key_sha256"]) or
                not isinstance(peer["remote_dir"], str) or
                not re.fullmatch(r"/[A-Za-z0-9._/-]{0,255}", peer["remote_dir"]) or
                ".." in Path(peer["remote_dir"]).parts or
                not isinstance(peer["private_key"], str) or
                not Path(peer["private_key"]).is_absolute() or
                Path(peer["private_key"]).is_symlink() or
                not Path(peer["private_key"]).is_file()):
            raise ValueError("invalid SFTP peer settings")
    return value


def _pair(directory: Path, run_id: str, spec: tuple) -> tuple[Path, Path, str, str]:
    name, kind, recipient, maximum, header = spec
    staged_name = name.removesuffix(".csv") + "-" + run_id + ".csv"
    body_path = directory / staged_name
    manifest_path = directory / (staged_name + ".manifest.json")
    body = _bytes(body_path, maximum)
    raw = _bytes(manifest_path, 4096)
    try:
        manifest = json.loads(raw, object_pairs_hook=_unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("staged manifest invalid") from error
    if (not body.startswith(header) or not isinstance(manifest, dict) or
            set(manifest) != FIELDS or manifest["file"] != staged_name or
            manifest["type"] != kind or manifest["from"] != "fds" or
            manifest["to"] != recipient or type(manifest["version"]) is not int or
            manifest["version"] != 1 or
            not isinstance(manifest["created"], str) or
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", manifest["created"]) or
            not isinstance(manifest["sha256"], str) or
            manifest["sha256"] != hashlib.sha256(body).hexdigest()):
        raise ValueError("staged FDS pair changed or has wrong route")
    try:
        datetime.strptime(manifest["created"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise ValueError("staged FDS timestamp invalid") from error
    return body_path, manifest_path, manifest["sha256"], hashlib.sha256(raw).hexdigest()


def _receipt(path: Path, digest: str, manifest_digest: str,
             recipient: str, peer_digest: str) -> bool:
    expected = {"body_sha256": digest, "manifest_sha256": manifest_digest,
                "to": recipient, "peer_sha256": peer_digest,
                "transport": "SFTP_UPLOAD_COMPLETED",
                "application_ack": False}
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024:
            raise ValueError("invalid transport receipt")
        try:
            current = json.loads(path.read_text(encoding="ascii"), object_pairs_hook=_unique_pairs)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("invalid transport receipt") from error
        if current != expected:
            raise ValueError("transport receipt differs from current pair")
        return True
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".fds-receipt-",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write((json.dumps(expected, separators=(",", ":")) + "\n").encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return False


def cycle(outbox: Path, receipts: Path, peers: dict, exchange_bin: Path,
          *, runner: Callable = subprocess.run) -> int:
    if outbox.is_symlink() or not outbox.is_dir() or receipts.is_symlink() or \
            (receipts.exists() and not receipts.is_dir()):
        raise ValueError("regular outbox and receipt directory required")
    if outbox.resolve() == receipts.resolve():
        raise ValueError("receipt state must be separate from the outbox")
    receipts.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        receipts.chmod(0o700)
    runs = sorted(path for path in outbox.iterdir() if RUN_ID.fullmatch(path.name))
    if len(runs) > 10000:
        raise ValueError("too many staged FDS runs")
    sent = 0
    for directory in runs:
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("staged run must be a regular directory")
        pairs = [_pair(directory, directory.name, spec) for spec in FILES]
        receipt_dir = receipts / directory.name
        if receipt_dir.is_symlink():
            raise ValueError("transport receipt directory is a symlink")
        receipt_dir.mkdir(exist_ok=True)
        if os.name == "posix":
            receipt_dir.chmod(0o700)
        for spec, (body, manifest, digest, manifest_digest) in zip(FILES, pairs):
            recipient = spec[2]
            peer = peers[recipient]
            peer_identity = {key: peer[key] for key in
                             ("host", "port", "user", "host_key_sha256", "remote_dir")}
            peer_digest = hashlib.sha256(json.dumps(peer_identity, sort_keys=True,
                                                    separators=(",", ":")).encode("utf-8")).hexdigest()
            marker = receipt_dir / (body.name + ".transport.json")
            if marker.exists() or marker.is_symlink():
                _receipt(marker, digest, manifest_digest, recipient, peer_digest)
                continue
            route = "push-n13" if recipient == "mps" else "push-n15"
            command = [str(exchange_bin), route, peer["host"], str(peer["port"]),
                       peer["user"], peer["private_key"], peer["host_key_sha256"],
                       peer["remote_dir"], str(body), str(manifest)]
            runner(command, check=True, timeout=60, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
            _receipt(marker, digest, manifest_digest, recipient, peer_digest)
            sent += 1
    return sent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outbox", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--peers", type=Path, required=True)
    parser.add_argument("--exchange-bin", type=Path, required=True)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()
    if args.poll_seconds < 5 or args.exchange_bin.is_symlink() or not args.exchange_bin.is_file():
        parser.error("valid exchange executable and poll interval >= 5 s required")
    try:
        peers = load_peers(args.peers)
    except (OSError, ValueError) as error:
        parser.exit(2, f"FDS sender configuration rejected: {error}\n")
    while True:
        try:
            print(f"transport uploads completed: {cycle(args.outbox, args.receipts, peers, args.exchange_bin)}",
                  flush=True)
        except (OSError, ValueError, subprocess.CalledProcessError,
                subprocess.TimeoutExpired) as error:
            if not args.watch:
                parser.exit(2, f"FDS outbox transport failed: {type(error).__name__}\n")
            print(f"FDS outbox transport waiting: {type(error).__name__}",
                  file=sys.stderr, flush=True)
        if not args.watch:
            break
        try:
            time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
