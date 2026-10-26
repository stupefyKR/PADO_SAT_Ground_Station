"""Bound the ROS CAD mesh and keep the camera interactive without live TM."""

import os
from pathlib import Path
import shutil
import subprocess
import unittest


VIEWER = Path(__file__).resolve().parents[1] / "web/ops/attitude_viewer.js"
ASSET = Path(__file__).resolve().parents[1] / "web/assets/pado-sat-deployed-mesh.json"
EARTH_ASSET = Path(__file__).resolve().parents[1] / "web/assets/earth-blue-marble-october.jpg"

NODE_TEST = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const raw = {schema: 'pado.cad-mesh.v1',
  vertices: [[0,0,0],[1,0,0],[0,1,0]],
  triangles: [[0,1,2,0]], materials: [{color:'#7199ad'}]};
const handlers = {};
const ctx = {};
const labels = [];
const axisMarkers = [];
const earthFrames = [];
const skyDraws = [];
let arcs = 0;
for (const name of ['fillRect','stroke','beginPath','moveTo','lineTo','closePath',
  'fill','clip','save','restore','setLineDash']) ctx[name] = () => {};
ctx.arc = (x, y, radius) => {
  arcs++;
  if (radius === 10) axisMarkers.push([x, y]);
  if (radius === 88) skyDraws.push('sun');
};
ctx.drawImage = () => { skyDraws.push('earth'); };
ctx.fillText = (value) => { labels.push(value); };
ctx.createRadialGradient = () => ({addColorStop() {}});
ctx.createLinearGradient = () => ({addColorStop() {}});
ctx.createImageData = (width, height) => ({width, height,
  data: new Uint8ClampedArray(width * height * 4)});
const earthCtx = {createImageData: ctx.createImageData,
  putImageData: (frame) => { earthFrames.push(frame); }};
const document = {createElement: () => ({width: 0, height: 0,
  getContext: () => earthCtx})};
const canvas = {width: 420, height: 300, getContext: () => ctx,
  addEventListener: (name, callback) => { handlers[name] = callback; },
  setPointerCapture() {}, focus() {}};
let responseOK = false;
const context = {window: {}, document, TextDecoder,
  requestAnimationFrame: (callback) => callback(),
  fetch: async () => responseOK ? {
    ok: true, headers: {get: () => String(JSON.stringify(raw).length)},
    arrayBuffer: async () => new TextEncoder().encode(JSON.stringify(raw)).buffer,
  } : {ok: false, status: 404}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const module = context.window.rosAttitudeViewer;
assert.ok(module.meshFromJson(raw));
assert.throws(() => module.meshFromJson({...raw, vertices_frame:'STEP'}), /형식/);
assert.throws(() => module.meshFromJson({...raw, triangles:[[0,1,3,0]]}), /인덱스/);
assert.throws(() => module.meshFromJson({...raw, materials:[{color:'url(evil)'}]}), /재질/);
assert.throws(() => module.meshFromJson({...raw, triangles:Array(25001).fill([0,1,2,0])}), /크기/);

(async () => {
  const statuses = [];
  const viewer = module.create(canvas, (status) => statuses.push(status));
  assert.ok(arcs >= 90, 'stable starfield replaces the vertical background grid');
  assert.equal(await viewer.load('/assets/missing.json'), false);
  assert.match(statuses.at(-1), /표시 불가/);
  assert.equal(viewer.mesh, null);
  responseOK = true;
  assert.equal(await viewer.load('/assets/model.json'), true);
  assert.match(statuses.at(-1), /CAD/);
  viewer.setDirections({earthBody:[0,0,0], sunBody:[1,0,0]});
  assert.equal(viewer.directions, null, 'zero direction is never rendered');
  viewer.setDirections({earthBody:[-1,0,0], sunBody:[0,0,1]});
  assert.ok(viewer.directions);
  const limb = earthFrames.at(-1);
  assert.ok(limb, 'spatial Earth horizon produces an image layer');
  const opaquePixels = limb.data.filter((_, index) => index % 4 === 3 &&
    limb.data[index] === 255).length;
  assert.ok(opaquePixels > 0 && opaquePixels < limb.width * limb.height,
    'default camera shows a large Earth limb with both planet and open space');
  let leftEarth = 0;
  for (let y = 0; y < limb.height; y++)
    for (let x = 0; x < Math.floor(limb.width / 2); x++)
      if (limb.data[(y * limb.width + x) * 4 + 3] === 255) leftEarth++;
  assert.ok(leftEarth / opaquePixels > 0.8,
    'default camera places the Earth limb on the left behind the satellite');
  for (const label of ['+X', '+Y', '+Z'])
    assert.ok(labels.includes(label), `missing scene label: ${label}`);
  assert.ok(!labels.includes('+Z 본체축'), 'detached +Z body-axis marker was removed');
  assert.equal(typeof viewer.drawCelestial, 'undefined',
    'small decorative Earth and Sun icons are removed');
  const trace = [];
  const skyAngles = [];
  for (const name of ['drawSpaceScene', 'drawMesh', 'drawBodyAxes']) {
    const original = viewer[name];
    viewer[name] = function(...args) {
      trace.push(name);
      if (name === 'drawSpaceScene') skyAngles.push(
        args[0].eye.reduce((sum, value, index) =>
          sum + value * this.directions.earthBody[index], 0));
      return original.apply(this, args);
    };
  }
  viewer.draw();
  assert.deepEqual(trace.slice(-3), ['drawSpaceScene', 'drawMesh', 'drawBodyAxes'],
    'distant Earth/Sun render behind the spacecraft and centered axes overlay it');
  const beforeDrag = skyAngles.at(-1);
  const oldYaw = viewer.yaw;
  handlers.pointerdown({pointerId:1,clientX:40,clientY:20});
  handlers.pointermove({pointerId:1,clientX:70,clientY:20});
  assert.ok(viewer.yaw > oldYaw, 'drag rotates the 3D orbit camera');
  assert.notEqual(skyAngles.at(-1), beforeDrag,
    'Earth direction changes relative to the camera while the spacecraft stays centered');
  const oldZoom = viewer.zoom;
  handlers.wheel({deltaY:-1,preventDefault(){}});
  assert.ok(viewer.zoom > oldZoom, 'wheel zooms the orbit camera');
  handlers.keydown({key:'Home',preventDefault(){}});
  assert.equal(viewer.zoom, 1, 'keyboard can restore the camera');
  viewer.yaw = Math.PI;
  viewer.pitch = 0;
  const earthLayerCount = earthFrames.length;
  viewer.draw();
  assert.equal(earthFrames.length, earthLayerCount,
    'Earth is out of sight when its direction is behind the camera');
  assert.ok(axisMarkers.some(([x, y]) => x === canvas.width / 2 &&
    y === canvas.height / 2), 'camera-aligned X uses a centered end-on axis marker');
  for (const label of ['+X', '+Y', '+Z'])
    assert.ok(labels.includes(label), `${label} remains identified at an end-on angle`);
  viewer.yaw = 0;
  viewer.setDirections({earthBody:[0,1,0], sunBody:[-1,0,0]});
  assert.ok(skyDraws.includes('sun'),
    'Sun is shown as a distant source in the same scene when it enters view');
  viewer.yaw = 0;
  const beforeEclipse = skyDraws.length;
  viewer.setDirections({earthBody:[-1,0,0], sunBody:[-1,0,0]});
  assert.deepEqual(skyDraws.slice(beforeEclipse), ['sun', 'earth'],
    'Earth is composited over the Sun when their sky directions coincide');
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""

NODE_ASSET_TEST = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {window: {}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const raw = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
assert.equal(raw.schema, 'pado.cad-mesh.v1');
assert.equal(raw.units, 'mm');
assert.equal(raw.vertices_frame, 'BODY');
assert.match(raw.source_sha256, /^[0-9a-f]{64}$/);
assert.ok(raw.materials.length >= 2, 'reference-based material grouping is present');
assert.ok(raw.vertices.length >= 1000, 'deployed STEP geometry is present');
assert.ok(raw.triangles.length >= 1000, 'deployed STEP faces are present');
const mesh = context.window.rosAttitudeViewer.meshFromJson(raw);
assert.equal(mesh.vertices.length, raw.vertices.length);
assert.equal(mesh.triangles.length, raw.triangles.length);
assert.ok(mesh.vertices.every((point) => point.every(Number.isFinite)));
assert.ok(mesh.vertices.every((point) => point.every((value) => Math.abs(value) <= 1.26)));
"""


class RosAttitudeViewerTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_actual_deployed_cad_asset_parses(self):
        self.assertTrue(ASSET.is_file(), "Deployed STEP mesh asset is missing")
        result = subprocess.run(
            ["node", "-e", NODE_ASSET_TEST, str(VIEWER), str(ASSET)],
            capture_output=True, text=True, timeout=10, check=False,
            env=os.environ.copy(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_bounded_mesh_and_camera_controls(self):
        self.assertTrue(EARTH_ASSET.is_file(), "Local Earth texture is missing")
        result = subprocess.run(
            ["node", "-e", NODE_TEST, str(VIEWER)], capture_output=True,
            text=True, timeout=10, check=False, env=os.environ.copy(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
