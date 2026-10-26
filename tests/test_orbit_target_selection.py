"""The ORBIT selector must never display another satellite's last position."""

from pathlib import Path
import shutil
import subprocess
import unittest


ORBIT_JS = Path(__file__).resolve().parents[1] / "web" / "orbit" / "orbit.js"


NODE_TEST = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const elements = new Map();
const element = (id) => {
  if (!elements.has(id)) elements.set(id, {textContent: '', hidden: false,
    addEventListener() {}});
  return elements.get(id);
};
const now = Date.now();
const iso = (offset) => new Date(now + offset).toISOString();
const fixture = (noradId) => ({
  schema: 'fds.orbit-view.v1', quality: 'PRELIMINARY_NOT_FOR_OPERATIONS',
  source: 'TLE', model: 'SGP4', norad_id: noradId,
  input_sha256: 'a'.repeat(64), generated_utc: iso(0),
  input_epoch_utc: iso(-120000), start_utc: iso(-60000), end_utc: iso(300000),
  orbital_period_s: 5500,
  samples: [iso(-60000), iso(300000)].map((time) =>
    [time, 6878137, 0, 0, 0, 0, 500000, 1, 0, 0]),
  stations: ['yonsei', 'kari'].map((id) => ({id, latitude_deg: 37,
    longitude_deg: 127, altitude_m: 100, minimum_elevation_deg: 10, passes: []})),
});
const requests = [];
let serve = (url) => ({ok: true, status: 200, headers: {get: () => null},
  json: async () => fixture(url.endsWith('iss') ? 25544 : 99999)});
const context = {
  Image: class {},
  document: {createElement: () => ({}), getElementById: element},
  fetch: async (url) => {requests.push(url); return serve(url);},
  window: {location: {assign: () => {throw Error('unexpected login redirect');}}},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
// Rendering uses canvas and WebGL; this test exercises data selection and state.
vm.runInContext('orbitRender = () => {};', context);
const read = () => context.readOrbitServer();
const select = (target) => context.chooseOrbitTarget(target);
const currentId = () => vm.runInContext('orbitState.data?.info.norad_id', context);
const currentTarget = () => vm.runInContext('orbitState.target', context);
const settle = () => new Promise((resolve) => setImmediate(resolve));

(async () => {
  await read();
  assert.equal(requests.at(-1), '/api/fds/orbit?target=synthetic');
  assert.equal(currentId(), 99999);
  select('iss');
  await settle();
  assert.equal(requests.at(-1), '/api/fds/orbit?target=iss');
  assert.equal(currentId(), 25544);
  assert.equal(currentTarget(), 'iss');

  serve = () => ({ok: false, status: 503, headers: {get: () => null}});
  select('operational');
  await settle();
  assert.equal(currentId(), undefined, 'unavailable target clears previous ISS orbit');
  assert.match(element('orbit-data-state').textContent, /유효한 FDS 결과가 아직 없습니다/);
  assert.equal(element('orbit-model').textContent, '—');

  serve = () => ({ok: true, status: 200, headers: {get: () => null},
    json: async () => fixture(99999)});
  select('iss');
  await settle();
  assert.equal(currentId(), undefined, 'wrong NORAD may not be shown as ISS');
  assert.match(element('orbit-data-state').textContent, /식별자가 일치하지 않습니다/);

  let resolveOld;
  serve = () => new Promise((resolve) => {resolveOld = resolve;});
  select('synthetic');
  assert.equal(currentId(), undefined);
  serve = () => ({ok: false, status: 503, headers: {get: () => null}});
  select('operational');
  await settle();
  resolveOld({ok: true, status: 200, headers: {get: () => null},
    json: async () => fixture(99999)});
  await settle();
  assert.equal(currentTarget(), 'operational');
  assert.equal(currentId(), undefined, 'late response from prior selection is ignored');
})().catch((error) => {console.error(error); process.exitCode = 1;});
"""


class OrbitTargetSelectionTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_target_switching_clears_unavailable_and_mismatched_data(self):
        result = subprocess.run(
            ["node", "-e", NODE_TEST, str(ORBIT_JS)],
            capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
