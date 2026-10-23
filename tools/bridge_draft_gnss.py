"""Turn one offline draft GNSS_PVT PDU into a test-only N11 file pair.

This bridge is for contract tests. The input is not an authenticated AX100/CSP
downlink and cannot establish the spacecraft OEM719A-to-OBC TM mapping.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from .pado_packet_codec import PacketError, decode, gnss_to_fds_properties
    from .run_fds_pipeline import verified_pair
else:
    from pado_packet_codec import PacketError, decode, gnss_to_fds_properties
    from run_fds_pipeline import verified_pair


def _write_once(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        if (path.is_symlink() or not path.is_file() or
                path.stat().st_size != len(content) or path.read_bytes() != content):
            raise ValueError("existing committed draft pair differs")
        return
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".draft-gnss-",
                                         suffix=".partial", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # The bridge is a single-writer test fixture; the receiver validates
        # the committed pair independently before making it current.
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def bridge(packet: bytes, inbox: Path, *, created: datetime | None = None) -> tuple[Path, Path]:
    """Commit body first and manifest last; N11 receiver sees only complete pairs."""
    decoded = decode(packet)
    body = gnss_to_fds_properties(decoded).encode("ascii")
    if inbox.is_symlink() or (inbox.exists() and not inbox.is_dir()):
        raise ValueError("N11 test inbox must be a non-symlink directory")
    inbox.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(packet).hexdigest()
    name = f"draft-gnss-{digest[:32]}.properties"
    body_path = inbox / name
    manifest_path = inbox / (name + ".manifest.json")
    when = (created or datetime.now(timezone.utc)).astimezone(timezone.utc)
    manifest = {"file": name, "type": "GNSS", "from": "ros", "to": "fds",
                "version": 1, "created": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "sha256": hashlib.sha256(body).hexdigest()}
    if manifest_path.exists() or manifest_path.is_symlink():
        # An existing complete pair is idempotent across calls and created times.
        if verified_pair(body_path, manifest_path, "gnss") != body:
            raise ValueError("existing committed draft pair differs")
        return body_path, manifest_path
    _write_once(body_path, body)
    _write_once(manifest_path,
                (json.dumps(manifest, separators=(",", ":")) + "\n").encode("ascii"))
    if verified_pair(body_path, manifest_path, "gnss") != body:
        raise ValueError("committed draft pair failed N11 verification")
    return body_path, manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path, help="one binary draft GNSS_PVT PDU")
    parser.add_argument("inbox", type=Path, help="test-only ROS-to-FDS N11 inbox")
    parser.add_argument("--test-only", action="store_true", required=True,
                        help="acknowledge this is not a flight downlink decoder")
    args = parser.parse_args()
    if args.packet.is_symlink() or not args.packet.is_file() or args.packet.stat().st_size > 200:
        parser.error("one regular PDU of at most 200 bytes required")
    try:
        body, manifest = bridge(args.packet.read_bytes(), args.inbox)
    except (OSError, ValueError, PacketError) as error:
        parser.exit(2, f"draft GNSS bridge rejected input: {error}\n")
    print(f"{body}\n{manifest}")


if __name__ == "__main__":
    main()
