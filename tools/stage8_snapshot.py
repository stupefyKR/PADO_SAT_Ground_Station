"""Create, verify, and restore an explicitly selected local test snapshot.

This does not encrypt data, snapshot a live database, or write to a NAS. The
manifest digest must be kept outside the snapshot to detect later replacement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path


MANIFEST = "snapshot-manifest.json"
MAX_FILES = 10000
CHUNK = 1024 * 1024
SECRET_COMPONENT = re.compile(r"(?:^|[._-])(key|secret|hmac|token|password|private)(?:$|[._-])", re.I)
SECRET_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".env"}
SECRET_NAMES = {".ssh", "authorized_keys", "id_rsa", "id_ed25519", "known_hosts"}


def relative_path(value: object) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("snapshot path must be a nonempty relative POSIX path")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"unsafe snapshot path: {value}")
    if any(part.lower() in SECRET_NAMES or SECRET_COMPONENT.search(part) or
           Path(part).suffix.lower() in SECRET_SUFFIXES
           for part in parts):
        raise ValueError(f"secret or key path must use the separate key procedure: {value}")
    return Path(*parts)


def selected_paths(selection: Path) -> list[tuple[str, Path]]:
    document = json.loads(selection.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or set(document) != {"version", "files"} or \
            document["version"] != 1 or not isinstance(document["files"], list) or \
            not 0 < len(document["files"]) <= MAX_FILES:
        raise ValueError("invalid snapshot selection")
    seen: set[str] = set()
    result = []
    for value in document["files"]:
        path = relative_path(value)
        folded = value.casefold()
        if folded in seen:
            raise ValueError(f"duplicate snapshot path: {value}")
        seen.add(folded)
        result.append((value, path))
    return sorted(result)


def forbid_symlinks(root: Path, path: Path) -> None:
    if root.is_symlink():
        raise ValueError(f"symlink root: {root}")
    current = root
    for component in path.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"symlink in snapshot path: {current}")


def copy_verified(source: Path, destination: Path, expected: dict | None = None) -> dict:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    digest = hashlib.sha256()
    with os.fdopen(os.open(source, flags), "rb") as reader:
        before = os.fstat(reader.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"not a regular file: {source}")
        with destination.open("xb") as writer:
            os.chmod(destination, 0o600)
            size = 0
            while block := reader.read(CHUNK):
                size += len(block)
                digest.update(block)
                writer.write(block)
            writer.flush()
            os.fsync(writer.fileno())
        after = os.fstat(reader.fileno())
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != \
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or \
            size != before.st_size:
        raise ValueError(f"source changed during snapshot: {source}")
    record = {"size": size, "sha256": digest.hexdigest()}
    if expected is not None and record != expected:
        raise ValueError(f"snapshot file mismatch: {source}")
    return record


def manifest_bytes(entries: list[dict]) -> bytes:
    return (json.dumps({"version": 1, "files": entries}, ensure_ascii=False,
                       sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _is_within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def create(source: Path, selection: Path, output: Path) -> str:
    if not source.is_dir() or source.is_symlink():
        raise ValueError("source must be a real directory")
    source_real = source.resolve()
    output_real = output.resolve(strict=False)
    if _is_within(output_real, source_real) or _is_within(source_real, output_real):
        raise ValueError("source and snapshot locations must be separate")
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError("snapshot destination must be new in an existing directory")
    paths = selected_paths(selection)
    staging = Path(tempfile.mkdtemp(prefix=".pado-snapshot-", dir=output.parent))
    try:
        os.chmod(staging, 0o700)
        entries = []
        for label, relative in paths:
            forbid_symlinks(source, relative)
            record = copy_verified(source / relative, staging / "files" / relative)
            entries.append({"path": label, **record})
        payload = manifest_bytes(entries)
        (staging / MANIFEST).write_bytes(payload)
        os.chmod(staging / MANIFEST, 0o600)
        digest = hashlib.sha256(payload).hexdigest()
        os.replace(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return digest


def verify(snapshot: Path, expected_manifest_sha256: str) -> list[dict]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256):
        raise ValueError("expected manifest SHA-256 must be 64 lowercase hex characters")
    if not snapshot.is_dir() or snapshot.is_symlink():
        raise ValueError("snapshot must be a real directory")
    manifest = snapshot / MANIFEST
    forbid_symlinks(snapshot, Path(MANIFEST))
    payload = manifest.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_manifest_sha256:
        raise ValueError("manifest digest does not match the independent record")
    document = json.loads(payload)
    if not isinstance(document, dict) or set(document) != {"version", "files"} or \
            document["version"] != 1 or not isinstance(document["files"], list) or \
            not 0 < len(document["files"]) <= MAX_FILES:
        raise ValueError("invalid snapshot manifest")
    expected_files = {MANIFEST}
    seen: set[str] = set()
    for entry in document["files"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
            raise ValueError("invalid snapshot file record")
        relative = relative_path(entry["path"])
        folded = entry["path"].casefold()
        if folded in seen or type(entry["size"]) is not int or entry["size"] < 0 or \
                not isinstance(entry["sha256"], str) or \
                not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise ValueError("invalid or duplicate snapshot file record")
        seen.add(folded)
        target = Path("files") / relative
        forbid_symlinks(snapshot, target)
        source = snapshot / target
        if not source.is_file():
            raise ValueError(f"missing snapshot file: {target}")
        if source.stat().st_size != entry["size"]:
            raise ValueError(f"snapshot file size mismatch: {target}")
        digest = hashlib.sha256()
        with source.open("rb") as reader:
            while block := reader.read(CHUNK):
                digest.update(block)
        if digest.hexdigest() != entry["sha256"]:
            raise ValueError(f"snapshot file hash mismatch: {target}")
        expected_files.add(target.as_posix())
    for parent, directories, files in os.walk(snapshot, followlinks=False):
        base = Path(parent)
        for name in directories:
            if (base / name).is_symlink():
                raise ValueError("snapshot contains a symlink directory")
        for name in files:
            item = base / name
            if item.is_symlink() or not item.is_file() or \
                    item.relative_to(snapshot).as_posix() not in expected_files:
                raise ValueError(f"unexpected snapshot file: {item}")
    return document["files"]


def restore(snapshot: Path, destination: Path, expected_manifest_sha256: str) -> None:
    entries = verify(snapshot, expected_manifest_sha256)
    snapshot_real = snapshot.resolve()
    destination_real = destination.resolve(strict=False)
    if _is_within(destination_real, snapshot_real) or \
            _is_within(snapshot_real, destination_real):
        raise ValueError("snapshot and restore locations must be separate")
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise ValueError("restore destination must be new in an existing directory")
    staging = Path(tempfile.mkdtemp(prefix=".pado-restore-", dir=destination.parent))
    try:
        os.chmod(staging, 0o700)
        for entry in entries:
            relative = relative_path(entry["path"])
            source = snapshot / "files" / relative
            forbid_symlinks(snapshot, Path("files") / relative)
            copy_verified(source, staging / relative,
                          {"size": entry["size"], "sha256": entry["sha256"]})
        os.replace(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    creating = commands.add_parser("create")
    creating.add_argument("--source", type=Path, required=True)
    creating.add_argument("--selection", type=Path, required=True)
    creating.add_argument("--output", type=Path, required=True)
    for action in ("verify", "restore"):
        sub = commands.add_parser(action)
        sub.add_argument("--snapshot", type=Path, required=True)
        sub.add_argument("--expected-manifest-sha256", required=True)
        if action == "restore":
            sub.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "create":
            digest = create(args.source, args.selection, args.output)
            print(json.dumps({"manifest_sha256": digest}))
        elif args.command == "verify":
            entries = verify(args.snapshot, args.expected_manifest_sha256)
            print(json.dumps({"verified_files": len(entries)}))
        else:
            restore(args.snapshot, args.destination, args.expected_manifest_sha256)
            print(json.dumps({"restored_to": str(args.destination)}))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"snapshot error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
