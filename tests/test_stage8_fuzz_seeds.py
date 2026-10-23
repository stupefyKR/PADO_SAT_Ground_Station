import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from tests.run_stage8_fuzz import seeds, tle_seed


class FuzzSeedTests(unittest.TestCase):
    def test_tle_seed_reaches_checksum_validation(self):
        value = tle_seed()
        self.assertEqual(len(value), 139)
        self.assertEqual(value[69], 10)
        self.assertEqual(value[2:7], value[72:77])
        for line in (value[:69], value[70:]):
            total = sum(char - 48 if 48 <= char <= 57 else char == 45
                        for char in line[:68])
            self.assertEqual(line[68], 48 + total % 10)

    def test_tm_seed_structure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seeds(root)
            frame = (root / "tm" / "valid").read_bytes()
            self.assertEqual(frame[:12], b"PSTSIM03\x01\x01\x7e\x57")
            self.assertEqual(struct.unpack_from(">H", frame, 12)[0], len(b"SIM-TM-A"))
            self.assertEqual(frame[18:-4], b"SIM-TM-A")
            self.assertEqual(struct.unpack_from(">I", frame, len(frame) - 4)[0],
                             zlib.crc32(frame[:-4]))


if __name__ == "__main__":
    unittest.main()
