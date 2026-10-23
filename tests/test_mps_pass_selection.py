"""The MPS browser must retain the physical contact, not its rolling ordinal ID."""

import os
from pathlib import Path
import shutil
import subprocess
import unittest


APP = Path(__file__).resolve().parents[1] / "web/common/app.js"

NODE_TEST = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {document: {body: {dataset: {page: 'selection-test'}}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const same = context.mpsSamePhysicalPass;
const old = {station_id: 'yonsei', band: 'UHF', pass_id: 'yonsei-003',
  aos_utc_s: 10000, los_utc_s: 10600, clipped_start: false, clipped_end: false};
assert.equal(same(old, {...old, pass_id: 'yonsei-002', aos_utc_s: 10004,
  los_utc_s: 10605}), true, 'renumbered future pass keeps selection');
assert.equal(same(old, {...old, aos_utc_s: 15500, los_utc_s: 16100}), false,
  'reused ordinal ID must not select an orbit later');
assert.equal(same(old, {...old, station_id: 'kari', band: 'S_BAND',
  pass_id: 'kari-003'}), false, 'different station must not inherit selection');
const kari = {...old, station_id: 'kari', band: 'S_BAND', pass_id: 'kari-003'};
assert.equal(same(kari, {...kari, pass_id: 'kari-002', aos_utc_s: 10004,
  los_utc_s: 10605}), true, 'Daejeon pass selection also follows contact time');
assert.equal(same(old, {...old, pass_id: 'yonsei-004', aos_utc_s: 10620,
  los_utc_s: 11220}), false, 'nearby but disjoint contact is distinct');
assert.equal(same(old, {...old, clipped_start: true}), false,
  'rolling clipped boundary cannot identify a future pass');
"""


class MpsPassSelectionTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_selection_follows_contact_geometry_not_ordinal_id(self):
        result = subprocess.run(
            ["node", "-e", NODE_TEST, str(APP)],
            capture_output=True, text=True, timeout=10, check=False,
            env=os.environ.copy(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
