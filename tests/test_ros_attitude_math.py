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
const predictNadir = scope.rosAttitudeMath.predictNadir;
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

// Identity body->ITRF: nadir in -X, Sun in +Z.
const identity = compute({attitude: attitude([1, 0, 0, 0]),
  orbitView: view([6878000, 0, 0], [0, 0, 1])});
near(identity.earthBody, [-1, 0, 0]);
near(identity.sunBody, [0, 0, 1]);
near(identity.satelliteItrf, [6878000, 0, 0]);
identity.bodyToItrf.forEach((axis, index) => near(axis,
  [[1,0,0],[0,1,0],[0,0,1]][index]));
assert.ok(identity.earthAngularRadiusRad > 1.1 && identity.earthAngularRadiusRad < 1.3);
assert.ok(identity.earthAlignment > 0.999999);

// Body -X is ITRF +Z after +90 deg about Y. Inverse rotation must recover -X.
const root = Math.sqrt(0.5);
const rotated = compute({attitude: attitude([root, 0, root, 0],
  '2026-10-26T05:00:30.000Z'),
  orbitView: view([0, 0, -6878000], [1, 0, 0])});
near(rotated.earthBody, [-1, 0, 0]);
near(rotated.sunBody, [0, 0, 1]);
near(rotated.bodyToItrf[0], [0, 0, -1]);
assert.ok(rotated.earthAlignment > 0.999999);

// An unrelated satellite, unsupported quaternion frame, or missing time
// coverage cannot be presented as a physical Earth/Sun direction.
const sample = view([0, 0, -6878000], [1, 0, 0]);
assert.equal(compute({attitude: attitude([1, 0, 0, 0]),
  orbitView: {...sample, norad_id: 25544}}), null);
assert.equal(compute({attitude: {...attitude([1, 0, 0, 0]), frame: 'TEST_FRAME'},
  orbitView: sample}), null);
assert.equal(compute({attitude: attitude([1, 0, 0, 0],
  '2026-10-26T05:02:00.000Z'), orbitView: sample}), null);

// FDS position samples imply along-track +Y. Nadir is ITRF -X, so the
// right-handed simulated body basis is +X radial outward, +Y=ITRF -Z,
// +Z along-track (with the radial velocity component removed).
const predictedView = {
  ...view([6878000, 0, 0], [0, 0, -1]),
  samples: [
    [t0, 6878000, 0, 0, 0, 0, 500000, 0, 0, -1],
    [t1, 6878000, 420000, 0, 0, 0, 500000, 0, 0, -1]
  ]
};
const prediction = predictNadir({orbitView: predictedView,
  utc: '2026-10-26T05:00:30.000Z'});
assert.equal(prediction.provenance, 'FDS_TEST_NADIR_PREDICTION');
near(prediction.earthBody, [-1, 0, 0]);
near(prediction.sunBody, [0, 1, 0]);
assert.ok(prediction.bodyToItrf[2][1] > 0.99,
  'body +Z follows the projected along-track direction');
assert.ok(Math.abs(prediction.bodyToItrf[0].reduce((sum, value, i) =>
  sum + value * prediction.bodyToItrf[2][i], 0)) < 1e-9);
near(prediction.bodyToItrf[1], [0, 0, -1]);
const radial = [6878000, 210000, 0];
const radialLength = Math.hypot(...radial);
near(prediction.bodyToItrf[0], radial.map((value) => value / radialLength));
near(prediction.satelliteItrf, [6878000, 210000, 0]);
assert.ok(prediction.earthAngularRadiusRad > 1.1 && prediction.earthAngularRadiusRad < 1.3);
assert.equal(prediction.earthAlignment, 1);
assert.equal(prediction.utc, '2026-10-26T05:00:30.000Z');
assert.ok(predictNadir({orbitView: predictedView, utc: t0}),
  'one-sided derivative at FDS coverage start');
assert.ok(predictNadir({orbitView: {...predictedView, source:'TLE'}, utc: t1}),
  'TLE view and one-sided derivative at coverage end');

// Predicted directions must never be advertised for another satellite, stale
// input, missing coverage, malformed Sun or a motionless position series.
assert.equal(predictNadir({orbitView: {...predictedView, norad_id:25544}, utc:t0}), null);
assert.equal(predictNadir({orbitView: {...predictedView,
  quality:'VALIDATED_FOR_OPERATIONS'}, utc:t0}), null);
assert.equal(predictNadir({orbitView: {...predictedView,
  input_epoch_utc:'2026-10-23T04:59:59.000Z'}, utc:t1}), null);
assert.equal(predictNadir({orbitView: predictedView,
  utc:'2026-10-26T05:02:00.000Z'}), null);
assert.equal(predictNadir({orbitView: {...predictedView,
  samples: predictedView.samples.map((row) => row.slice(0,7).concat([0,0,0]))},
  utc:t0}), null);
assert.equal(predictNadir({orbitView:view([6878000,0,0],[0,0,-1]), utc:t0}), null);

// Keep the most recent measured pose as a display reference through a gap,
// while Earth/Sun follow the selected position rather than the old sample.
const stateInput = {utc:t1,position_itrf_m:[0,0,-6878000],sun_itrf:[1,0,0],
  attitude:null,attitudeStatus:'UNKNOWN',displayAttitude:attitude([root,0,root,0]),
  displayAttitudeStatus:'LAST_KNOWN'};
const held=scope.rosAttitudeMath.computeState(stateInput);
assert.equal(held.orientationKnown,false);
assert.equal(held.orientationHeld,true);assert.equal(held.displayOrientationKnown,true);
assert.equal(held.displayAttitudeUtc,t0);assert.equal(held.earthAlignment,null);
near(held.earthBody,[-1,0,0]);near(held.sunBody,[0,0,1]);
assert.equal(held.provenance,'LAST_RECEIVED_ATTITUDE');
const future=scope.rosAttitudeMath.computeState({...stateInput,
  displayAttitude:attitude([1,0,0,0],'2026-10-26T05:02:00.000Z')});
assert.equal(future.orientationHeld,false,'future attitude cannot fill an earlier view');
const neutral=scope.rosAttitudeMath.computeState({...stateInput,displayAttitude:null});
assert.equal(neutral.orientationKnown,false);assert.equal(neutral.displayOrientationKnown,false);
near(neutral.sunBody,[1,0,0]);
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
