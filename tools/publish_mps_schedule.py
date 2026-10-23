"""Publish a validated N13 schedule snapshot for the read-only MPS service."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

if __package__:
    from .stage_fds_outbox import FILES, _pair
else:
    from stage_fds_outbox import FILES, _pair


def publish(run: Path, destination: Path) -> Path:
    if run.is_symlink() or not run.is_dir() or not re.fullmatch(r"fds-[A-Za-z0-9_-]{1,64}", run.name):
        raise ValueError("completed FDS run directory required")
    name, kind, recipient, maximum, header = FILES[0]
    body, manifest = _pair(run, name, kind, recipient, maximum, header)
    if destination.is_symlink():
        raise ValueError("schedule root must not be a symlink")
    destination.mkdir(parents=True, exist_ok=True)
    final = destination / run.name
    if final.exists() or final.is_symlink():
        raise ValueError("schedule run already published")
    with tempfile.TemporaryDirectory(prefix=".n13-", dir=destination) as temporary:
        staging = Path(temporary)
        for path, content in (
            (staging / name, body),
            (staging / (name + ".manifest.json"),
             (json.dumps(manifest, separators=(",", ":")) + "\n").encode("ascii")),
        ):
            with path.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            path.chmod(0o644)
        staging.chmod(0o755)
        staging.rename(final)
    marker: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination, prefix=".n13-current-",
                                         delete=False) as handle:
            marker = Path(handle.name)
            handle.write((run.name + "\n").encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
        marker.chmod(0o644)
        os.replace(marker, destination / "current.txt")
    finally:
        if marker is not None:
            marker.unlink(missing_ok=True)
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(publish(args.run, args.destination))


if __name__ == "__main__":
    main()
