"""Catalog image permissions must not inherit a group-writable VM checkout."""

import shlex
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CatalogPackagingTests(unittest.TestCase):
    def test_catalog_copies_fix_read_only_permissions_before_service_user(self):
        lines = (ROOT / "docker/stage9/Dockerfile").read_text(encoding="utf-8").splitlines()
        service_user = next(index for index, line in enumerate(lines)
                            if line.strip() == "USER 10001:10001")
        for source in ("interfaces/command_db/commands.json",
                       "interfaces/pado_packets/draft_v0.json"):
            destination = "/app/" + source
            matches = [(index, shlex.split(line)) for index, line in enumerate(lines)
                       if line.startswith("COPY ") and shlex.split(line)[-1] == destination]
            self.assertEqual(len(matches), 1, destination)
            index, instruction = matches[0]
            self.assertEqual(instruction[-2:], [source, destination])
            self.assertIn("--chmod=0444", instruction,
                          "COPY must normalize unsafe checkout permissions")
            self.assertLess(index, service_user)


if __name__ == "__main__":
    unittest.main()
