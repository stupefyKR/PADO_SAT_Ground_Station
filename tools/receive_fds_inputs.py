"""Receive N05 by authenticated SFTP and stage committed N05/N11 pairs for FDS.

N11 arrives in a separately configured SFTP inbox. Its raw TM decoder is not
implemented; only the explicit normalized GNSS file contract is accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if __package__:
    from .run_fds_pipeline import _regular_bytes, verified_pair
else:
    from run_fds_pipeline import _regular_bytes, verified_pair


def _atomic_marker(root: Path, name: str, value: str) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".ingress-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(value.encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, root / name)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _root(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("ingress root must not be a symlink")
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def ingest_inbox(route: str, inbox: Path, destination: Path) -> Path | None:
    """Commit the newest manifest-backed pair, or block a corrupt committed pair."""
    if route not in ("tle", "gnss"):
        raise ValueError("unsupported FDS route")
    if inbox.is_symlink() or not inbox.is_dir():
        raise ValueError(f"{route}: SFTP inbox must be a directory")
    root = _root(destination)
    manifests = [path for path in inbox.iterdir()
                 if path.name.endswith(".manifest.json") and not path.name.endswith(".partial")]
    if len(manifests) > 10000:
        raise ValueError(f"{route}: inbox entry limit exceeded")
    if not manifests:
        return None  # The sender has not committed a complete pair.
    latest = max(manifests, key=lambda path: (path.lstat().st_mtime_ns, path.name))
    body = inbox / latest.name.removesuffix(".manifest.json")
    try:
        payload = verified_pair(body, latest, route)
        assert payload is not None
        metadata = _regular_bytes(latest, 4096)
        digest = hashlib.sha256(route.encode("ascii") + b"\0" + payload + b"\0" + metadata).hexdigest()
        final = root / digest
        if final.is_symlink():
            raise ValueError(f"{route}: snapshot must not be a symlink")
        if not final.exists():
            with tempfile.TemporaryDirectory(prefix=".fds-ingress-", dir=root) as temporary:
                stage = Path(temporary)
                for name, content in ((body.name, payload), (latest.name, metadata)):
                    with (stage / name).open("xb") as handle:
                        handle.write(content)
                        handle.flush()
                        os.fsync(handle.fileno())
                stage.rename(final)
        # A prior snapshot may have been tampered with on disk; verify before pointing at it.
        confirmed = verified_pair(final / body.name, final / latest.name, route)
        if confirmed != payload or _regular_bytes(final / latest.name, 4096) != metadata:
            raise ValueError(f"{route}: existing snapshot differs from committed input")
        _atomic_marker(root, "current.txt", digest + "\n")
        (root / "blocked.txt").unlink(missing_ok=True)
        return final
    except (OSError, ValueError, AssertionError, UnicodeError) as error:
        _atomic_marker(root, "blocked.txt", "invalid committed input\n")
        raise ValueError(f"{route}: committed inbox pair rejected: {error}") from error


def pull_n05(args: argparse.Namespace, destination: Path) -> Path:
    """Use the existing pinned-host-key C client; never copy unverified SFTP bytes."""
    root = _root(destination)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", args.n05_filename) or \
            args.n05_filename in (".", ".."):
        raise ValueError("invalid N05 remote filename")
    with tempfile.TemporaryDirectory(prefix=".n05-pull-", dir=root) as temporary:
        command = [str(args.exchange_bin.resolve()), "pull-n05", args.n05_host,
                   str(args.n05_port), args.n05_user, str(args.n05_key.resolve()),
                   args.n05_host_key_sha256, args.n05_remote_dir, args.n05_filename,
                   str(Path(temporary).resolve())]
        subprocess.run(command, check=True, timeout=30)
        result = ingest_inbox("tle", Path(temporary), root)
        if result is None:
            raise ValueError("N05 SFTP pull returned no committed pair")
        return result


def cycle(args: argparse.Namespace) -> None:
    error: Exception | None = None
    if args.n05_host:
        try:
            print(f"N05 current: {pull_n05(args, args.destination / 'tle')}", flush=True)
        except (OSError, ValueError, subprocess.CalledProcessError,
                subprocess.TimeoutExpired) as failure:
            # An unavailable peer does not invalidate a previously verified TLE.
            # A malformed committed pair is blocked by ingest_inbox itself.
            error = failure
    if args.n11_inbox:
        try:
            result = ingest_inbox("gnss", args.n11_inbox, args.destination / "gnss")
            if result is not None:
                print(f"N11 current: {result}", flush=True)
        except (OSError, ValueError) as failure:
            error = failure
    if error is not None:
        raise error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--n11-inbox", type=Path, help="N11 committed SFTP push inbox")
    parser.add_argument("--exchange-bin", type=Path, help="Linux gs_fds_exchange executable")
    parser.add_argument("--n05-host")
    parser.add_argument("--n05-port", type=int)
    parser.add_argument("--n05-user")
    parser.add_argument("--n05-key", type=Path)
    parser.add_argument("--n05-host-key-sha256")
    parser.add_argument("--n05-remote-dir")
    parser.add_argument("--n05-filename")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()
    n05 = (args.exchange_bin, args.n05_host, args.n05_port, args.n05_user,
           args.n05_key, args.n05_host_key_sha256, args.n05_remote_dir, args.n05_filename)
    if not args.n11_inbox and not any(n05):
        parser.error("configure N05 SFTP pull or N11 inbox")
    if any(n05) and (not all(n05) or not 1 <= args.n05_port <= 65535 or
                     not re.fullmatch(r"[0-9a-f]{64}", args.n05_host_key_sha256)):
        parser.error("complete N05 SFTP settings and 64-character hex host-key pin required")
    if args.poll_seconds < 5:
        parser.error("poll-seconds must be >= 5")
    while True:
        try:
            cycle(args)
        except (OSError, ValueError, subprocess.CalledProcessError,
                subprocess.TimeoutExpired) as error:
            if not args.watch:
                parser.exit(2, f"FDS ingress failed: {error}\n")
            print(f"FDS ingress waiting: {error}", file=sys.stderr, flush=True)
        if not args.watch:
            break
        try:
            time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
