"""Restore a curated test baseline, then use C parsers on the restored files."""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import tempfile

from tools.stage8_snapshot import create, restore, verify


ROOT = pathlib.Path(__file__).resolve().parents[1]
FILES = ("interfaces/command_db/commands.json",
         "interfaces/tm_db/telemetry.json",
         "interfaces/n2_chart.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=pathlib.Path, required=True)
    args = parser.parse_args()
    build = args.build.resolve()
    with tempfile.TemporaryDirectory(prefix="build-stage8-recovery-", dir=ROOT) as tmp:
        root = pathlib.Path(tmp)
        source = root / "source"
        for name in FILES:
            destination = source / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        selection = root / "selection.json"
        selection.write_text(json.dumps({"version": 1, "files": FILES}),
                             encoding="utf-8")
        snapshot = root / "snapshot"
        digest = create(source, selection, snapshot)
        if len(verify(snapshot, digest)) != len(FILES):
            raise RuntimeError("snapshot did not include the full test baseline")
        restored = root / "restored"
        restore(snapshot, restored, digest)
        for name in FILES:
            if (restored / name).read_bytes() != (ROOT / name).read_bytes():
                raise RuntimeError(f"restored bytes differ: {name}")
        subprocess.run((str(build / "gs_mps_inputs_tests"),
                        str(restored / "interfaces/command_db/commands.json")),
                       check=True, capture_output=True, text=True, timeout=30)
        stage6_output = root / "stage6-output"
        stage6_output.mkdir()
        subprocess.run((str(build / "gs_stage6_tests"), str(stage6_output),
                        str(restored / "interfaces/n2_chart.json")),
                       check=True, capture_output=True, text=True, timeout=30)
        # Damage a snapshot after the independent manifest digest was recorded.
        damaged = snapshot / "files" / "interfaces" / "n2_chart.json"
        with damaged.open("ab") as writer:
            writer.write(b"x")
        try:
            restore(snapshot, root / "must-not-restore", digest)
        except ValueError:
            pass
        else:
            raise RuntimeError("damaged snapshot was accepted")
        if (root / "must-not-restore").exists():
            raise RuntimeError("failed restore left a destination behind")
        print("stage 8 recovery: 3 files restored, C parsers passed, "
              "damaged snapshot refused")


if __name__ == "__main__":
    main()
