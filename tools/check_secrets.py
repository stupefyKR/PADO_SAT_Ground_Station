"""Reject common secret files and credential literals from the Git index.

This is a local guard, not a substitute for a full repository secret audit.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import PurePosixPath


BLOCKED_NAMES = {".env", "id_rsa", "id_ed25519", "id_ecdsa"}
BLOCKED_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".asc"}
BLOCKED_DIRS = {"secrets", "keys", "srv"}
PRIVATE_KEY = re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")
TOKEN = re.compile(rb"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b")
AWS_ACCESS_KEY = re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
CREDENTIAL_ASSIGNMENT = re.compile(
    rb"(?im)^\s*[\"']?(?:password|passwd|secret|api[_-]?key|access[_-]?token)"
    rb"[\"']?\s*[:=]\s*[\"']?[^\s\"'#;]{8,}"
)


def inspect_blob(path: str, data: bytes) -> list[str]:
    """Return reasons that a staged path or its contents need human review."""
    normalized = path.replace("\\", "/")
    name = PurePosixPath(normalized).name.lower()
    parts = {part.lower() for part in PurePosixPath(normalized).parts}
    reasons = []
    if (
        name in BLOCKED_NAMES
        or name.startswith(".env.") and name != ".env.example"
        or PurePosixPath(name).suffix in BLOCKED_SUFFIXES
        or parts.intersection(BLOCKED_DIRS)
    ):
        reasons.append("secret file path")
    for pattern, label in (
        (PRIVATE_KEY, "private key block"),
        (TOKEN, "token-shaped value"),
        (AWS_ACCESS_KEY, "cloud access key"),
        (CREDENTIAL_ASSIGNMENT, "credential assignment"),
    ):
        if pattern.search(data):
            reasons.append(label)
    return reasons


def git_output(*args: str) -> bytes:
    result = subprocess.run(
        ["git", *args], capture_output=True, check=False
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def check_staged() -> int:
    # The index is scanned in full so an older tracked secret cannot slip past
    # a commit that happens to modify only unrelated files.
    paths = git_output("ls-files", "--cached", "-z").split(b"\0")
    failures = []
    for raw_path in filter(None, paths):
        path = raw_path.decode("utf-8", errors="surrogateescape")
        data = git_output("show", f":{path}")
        for reason in inspect_blob(path, data):
            failures.append(f"{path}: {reason}")
    if failures:
        for failure in failures:
            print(f"secret check: {failure}", file=sys.stderr)
        return 1
    print(f"secret check: {len(paths) - 1} staged files checked")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="scan the Git index")
    args = parser.parse_args()
    if not args.staged:
        parser.error("--staged is required")
    try:
        return check_staged()
    except RuntimeError as exc:
        print(f"secret check: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
