"""ROS simulator attitude vectors use the FDS ITRF frame consistently."""

from pathlib import Path
import shutil
import subprocess
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "web/ops/attitude_math.js"
NODE_TEST = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const scope = {};
vm.createContext(scope);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), scope);
const compute = scope.rosAttitudeMath.compute;
const t0 = '2026-10-26T05:00:00.000Z';
const t1 = '2026-10-26T05:01:00.000Z';
const view = (position, sun) => ({
  schema: 'fds.orbit-view.v1', quality: 'PRELIMINARY_NOT_FOR_OPERATIONS',
  norad_id: 99999, source: 'GNSS', input_epoch_utc: t0,
  start_utc: t0, end_utc: t1, sample_step_s: 60,
  samples: [t0, t1].map((time) =>
    [time, ...position, 0, 0, 500000, ...sun])
});
const attitude = (q, time = t0) => ({
  utc: time, frame: 'SIM_BODY_TO_ITRF_NADIR', quaternion_wxyz: q
});
const near = (actual, expected) => {
  assert.equal(actual.length, expected.length);
  actual.forEach((value, index) => assert.ok(Math.abs(value - expected[index]) < 1e-9,
    `${value} differs from ${expected[index]} at ${index}`));
};

// Identity body->ITRF: nadir in +Z, Sun in +X.
const identity = compute({attitude: attitude([1, 0, 0, 0]),
  orbitView: view([0, 0, -6878000], [1, 0, 0])});
near(identity.earthBody, [0, 0, 1]);
near(identity.sunBody, [1, 0, 0]);
assert.ok(identity.earthAlignment > 0.999999);

// Body +Z is ITRF +X after +90 deg about Y. Inverse rotation must recover +Z.
const root = Math.sqrt(0.5);
const rotated = compute({attitude: attitude([root, 0, root, 0],
  '2026-10-26T05:00:30.000Z'),
  orbitView: view([-6878000, 0, 0], [0, 0, -1])});
near(rotated.earthBody, [0, 0, 1]);
near(rotated.sunBody, [1, 0, 0]);

// An unrelated satellite, unsupported quaternion frame, or missing time
// coverage cannot be presented as a physical Earth/Sun direction.
const sample = view([0, 0, -6878000], [1, 0, 0]);
assert.equal(compute({attitude: attitude([1, 0, 0, 0]),
  orbitView: {...sample, norad_id: 25544}}), null);
assert.equal(compute({attitude: {...attitude([1, 0, 0, 0]), frame: 'TEST_FRAME'},
  orbitView: sample}), null);
assert.equal(compute({attitude: attitude([1, 0, 0, 0],
  '2026-10-26T05:02:00.000Z'), orbitView: sample}), null);
"""


class RosAttitudeMathTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_simulator_body_earth_and_sun_vectors(self):
        result = subprocess.run(["node", "-e", NODE_TEST, str(SOURCE)],
                                capture_output=True, text=True, timeout=10,
                                check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
