"""Catalog image permissions must not inherit a group-writable VM checkout."""

import os
import shlex
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CatalogPackagingTests(unittest.TestCase):
    def test_catalog_paths_are_traversable_and_files_read_only_before_service_user(self):
        lines = (ROOT / "docker/Dockerfile").read_text(encoding="utf-8").splitlines()
        instructions = []
        pending = ""
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pending += line.rstrip("\\").strip() + " "
            if not line.endswith("\\"):
                instructions.append(shlex.split(pending))
                pending = ""
        service_user = instructions.index(["USER", "10001:10001"])
        commands = []
        for index, instruction in enumerate(instructions[:service_user]):
            if instruction[0] != "RUN":
                continue
            command = []
            for token in [*instruction[1:], "&&"]:
                if token == "&&":
                    commands.append((index, command))
                    command = []
                else:
                    command.append(token)
        directories = {"/app/interfaces", "/app/interfaces/command_db",
                       "/app/interfaces/pado_packets"}
        for directory in directories:
            self.assertTrue(any(command[:2] == ["chmod", "0755"] and directory in command[2:]
                                for _, command in commands), directory)
        for source in ("interfaces/command_db/commands.json",
                       "interfaces/pado_packets/draft_v0.json"):
            destination = "/app/" + source
            matches = [(index, instruction) for index, instruction in enumerate(instructions)
                       if instruction[0] == "COPY" and instruction[-1] == destination]
            self.assertEqual(len(matches), 1, destination)
            index, instruction = matches[0]
            self.assertEqual(instruction[-2:], [source, destination])
            self.assertLess(index, service_user)
            self.assertTrue(any(index < run_index < service_user and
                                command[:2] == ["chmod", "0444"] and destination in command[2:]
                                for run_index, command in commands), destination)

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0,
                         "Directory traversal regression needs a non-root POSIX user")
    def test_read_only_file_still_requires_parent_directory_execute_permission(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "catalog"
            directory.mkdir()
            catalog = directory / "commands.json"
            catalog.write_text('{"version":1}', encoding="utf-8")
            catalog.chmod(0o444)
            directory.chmod(0o444)
            try:
                with self.assertRaises(PermissionError):
                    catalog.stat()
                with self.assertRaises(PermissionError):
                    catalog.read_text(encoding="utf-8")
            finally:
                directory.chmod(0o755)
            self.assertEqual(catalog.stat().st_mode & 0o777, 0o444)
            self.assertEqual(catalog.read_text(encoding="utf-8"), '{"version":1}')


if __name__ == "__main__":
    unittest.main()
