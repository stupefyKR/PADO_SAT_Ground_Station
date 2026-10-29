"""Bound the ROS CAD mesh and keep the camera interactive without live TM."""

import os
from pathlib import Path
import shutil
import subprocess
import unittest


VIEWER = Path(__file__).resolve().parents[1] / "web/ops/attitude_viewer.js"
GL_VIEWER = Path(__file__).resolve().parents[1] / "web/ops/attitude_gl.js"
ASSET = Path(__file__).resolve().parents[1] / "web/assets/pado-sat-deployed-mesh.json"
APPEARANCE = Path(__file__).resolve().parents[1] / "web/assets/pado-sat-appearance.json"
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
      if (name === 'drawSpaceScene' && this.directions?.earthBody) skyAngles.push(
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
  assert.equal(viewer.sunStatus,'EARTH_OCCLUDED');
  assert.ok(labels.includes('태양 · 지구에 가림'));
  viewer.setDirections({orientationKnown:false,orientationHeld:true,displayOrientationKnown:true,
    positionKnown:true,satelliteItrf:[6878137,0,0],earthBody:[-1,0,0],sunBody:[0,0,1]});
  assert.equal(viewer.orientationKnown,false);assert.equal(viewer.orientationHeld,true);
  assert.deepEqual(trace.slice(-3),['drawSpaceScene','drawMesh','drawBodyAxes'],
    'unknown current attitude still draws the CAD and body axes using its reference pose');
  assert.ok(labels.includes('자세 정보 없음 · 마지막 수신 자세 유지'));
  assert.equal(viewer.sunStatus,'OUT_OF_VIEW');
  assert.ok(labels.includes('태양 · 시야 밖'));
  assert.equal(viewer.viewSun(),true);
  assert.equal(viewer.sunStatus,'VISIBLE','Sun camera focus finds a valid off-screen Sun');
  assert.equal(viewer.orientationKnown,false,'camera focus never makes held attitude measured');
  // Camera focusing remains usable for polar Sun directions as well.
  for (const sun of [[1,0,0],[0,1,0],[0,0,-1]]) {
    viewer.setDirections({orientationKnown:false,positionKnown:false,earthBody:null,sunBody:sun});
    assert.equal(viewer.viewSun(),true);assert.equal(viewer.sunStatus,'VISIBLE');
  }
  viewer.setDirections({orientationKnown:false,positionKnown:false,earthBody:null,sunBody:null});
  assert.equal(viewer.sunStatus,'NO_DATA');assert.equal(viewer.viewSun(),false);
  assert.equal(viewer.viewEarth(),false);
  assert.ok(labels.includes('자세 정보 없음 · 기준 형상 표시'));
  // Time selection can move Earth through any body-frame direction while
  // attitude is held. Keep its viewing angle instead of losing it behind camera.
  viewer.setDirections({orientationKnown:false,orientationHeld:true,displayOrientationKnown:true,
    positionKnown:true,satelliteItrf:[6878137,0,0],earthBody:[-1,0,0],sunBody:[0,0,1]});
  assert.equal(viewer.viewEarth(),true);
  for (const earth of [[1,0,0],[0,1,0],[0,0,1],[0,-1,0],[0,0,-1]]) {
    const oldFrames=earthFrames.length;
    viewer.setDirections({orientationKnown:false,orientationHeld:true,displayOrientationKnown:true,
      positionKnown:true,satelliteItrf:earth.map(x=>-x*6878137),earthBody:earth,sunBody:[1,0,0]});
    assert.ok(earthFrames.length>oldFrames,'Earth limb remains rendered after a large timeline jump');
    assert.equal(viewer.orientationKnown,false,'camera tracking does not infer attitude');
  }
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
const appearance = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const surface = context.window.rosAttitudeViewer.appearanceFromJson(appearance, mesh);
assert.ok(surface.triangles.length > 100 && surface.triangles.length <= 512,
  'supplied CAD screenshots are mapped with a bounded number of texture triangles');
const path = require('node:path');
let totalPixels = 0;
for (const url of Object.values(surface.textures)) {
  const png = fs.readFileSync(path.join(path.dirname(process.argv[3]), path.basename(url)));
  assert.equal(png.subarray(0,8).toString('hex'), '89504e470d0a1a0a');
  const width = png.readUInt32BE(16), height = png.readUInt32BE(20);
  assert.ok(width > 0 && width <= 4096 && height > 0 && height <= 4096);
  totalPixels += width * height;
}
assert.ok(totalPixels <= 32 * 1024 * 1024);
"""

NODE_APPEARANCE_TEST = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const trace = [], requests = [];
const imageLoads = new Map();
const ctx = {};
for (const name of ['fillRect','stroke','beginPath','moveTo','lineTo','closePath',
  'clip','save','restore','setLineDash','arc','fillText']) ctx[name] = () => {};
ctx.fill = () => trace.push('cad');
ctx.drawImage = (image) => trace.push(image.src);
const canvas = {width:760,height:500,getContext:()=>ctx,addEventListener(){}};
class MockImage {
  set src(url) {
    this.url = url;
    imageLoads.set(url, (imageLoads.get(url) || 0) + 1);
    this.naturalWidth = url.includes('huge') ? 5000 : 64;
    this.naturalHeight = 32;
    Promise.resolve().then(() => url.includes('missing') ? this.onerror?.() : this.onload?.());
  }
  get src() { return this.url; }
}
let body;
const scope = {window:{}, Image:MockImage, TextDecoder,
  requestAnimationFrame:(callback)=>callback(),
  fetch:async (url) => {
    requests.push(url);
    const bytes = new TextEncoder().encode(JSON.stringify(body));
    return {ok:true,headers:{get:()=>String(bytes.length)},arrayBuffer:async()=>bytes.buffer};
  }};
vm.createContext(scope);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
const api = scope.window.rosAttitudeViewer;
const raw = {schema:'pado.cad-mesh.v1',vertices:[[0,0,0],[1,0,0],[0,1,0]],
  triangles:[[0,1,2,0]],materials:[{color:'#7199ad'}]};
const mesh = api.meshFromJson(raw);
assert.equal(mesh.scale,2.5);
assert.equal(mesh.centre.join(','),'0.5,0.5,0');
const patch = (id, texture, z, normal) => ({id,texture,
  corners:[[0,0,z],[1,0,z],[1,1,z],[0,1,z]],
  image_quad:[[0.1,0.1],[0.9,0.2],[0.8,0.9],[0.2,0.8]],
  normal,subdivisions:[2,2]});
const appearance = {schema:'pado.cad-appearance.v1',textures:{
  front:'/assets/pado-sat-surface-front.png',back:'/assets/pado-sat-surface-back.png'},
  patches:[patch('front','front',0.01,[0,0,1]),patch('back','back',-0.01,[0,0,-1])]};
const copy = (value) => JSON.parse(JSON.stringify(value));
const compiled = api.appearanceFromJson(appearance,mesh);
assert.equal(compiled.triangles.length,16);
const near = (actual, expected) => actual.forEach((value,i)=>
  assert.ok(Math.abs(value-expected[i])<1e-9,`${actual} != ${expected}`));
near(compiled.triangles[0].vertices[0].position,[-1.25,-1.25,0.025]);
near(compiled.triangles[0].vertices[0].uv,[0.1,0.1]);
near(compiled.triangles[6].vertices[2].uv,[0.8,0.9]);
assert.ok(compiled.triangles.every((triangle)=>triangle.vertices.every((vertex)=>
  vertex.uv.every((value)=>Number.isFinite(value)&&value>=0&&value<=1))));
let bad = copy(appearance); bad.textures.front='https://example.com/model.png';
assert.throws(()=>api.appearanceFromJson(bad,mesh),/로컬/);
bad=copy(appearance);bad.patches[0].subdivisions=[17,1];
assert.throws(()=>api.appearanceFromJson(bad,mesh),/패치/);
bad=copy(appearance);bad.patches[0].subdivisions=[16,16];
assert.throws(()=>api.appearanceFromJson(bad,mesh),/삼각형/);
bad=copy(appearance);bad.patches[0].image_quad=[[0,0],[1,1],[1,0],[0,1]];
assert.throws(()=>api.appearanceFromJson(bad,mesh),/볼록/);
bad=copy(appearance);bad.patches[0].corners[2][2]=0.1;
assert.throws(()=>api.appearanceFromJson(bad,mesh),/평면/);
bad=copy(appearance);bad.patches[0].corners[2][0]=100;
assert.throws(()=>api.appearanceFromJson(bad,mesh),/범위/);

(async()=>{
  const statuses=[];
  const viewer=api.create(canvas,(status)=>statuses.push(status));
  viewer.mesh=mesh;
  body=appearance;
  assert.equal(await viewer.loadAppearance(),true);
  assert.match(statuses.at(-1),/3D 표시 불가.*재질색/);
  assert.equal(await viewer.loadAppearance(),true);
  assert.equal(imageLoads.get(appearance.textures.front),1,'each local image is loaded once');
  const project=(eye)=>(position)=>({x:200+100*position[0],y:150-100*position[1],
    depth:position.reduce((sum,value,i)=>sum+value*eye[i],0)});
  trace.length=0;
  viewer.drawMesh(project([0,0,1]),[0,0,1]);
  assert.equal(trace.join(','),'cad',
    'Canvas fallback never displays incorrectly painter-sorted screenshot textures');
  let gpuCalls=0, disposed=0;
  const layer={src:'depth-buffered-layer'};
  viewer.glRenderer={render(basis,pixels,width,height,surfaces){
    gpuCalls++; assert.equal(surfaces,viewer.appearance);
    assert.ok(basis.eye.length===3&&pixels>0&&width===760&&height===500);
    return layer;
  },dispose(){disposed++;}};
  trace.length=0;viewer.draw();
  assert.equal(gpuCalls,1);
  assert.ok(trace.includes(layer.src),'depth-buffered CAD layer composites over the sky');
  viewer.glRenderer.render=()=>null;
  viewer.draw();
  assert.equal(disposed,1);
  assert.equal(viewer.glRenderer,null);
  assert.match(statuses.at(-1),/3D 표시 불가.*재질색/);

  body=copy(appearance);body.textures.front='/assets/pado-sat-surface-missing.png';
  body.patches=[body.patches[0]];
  assert.equal(await viewer.loadAppearance(),false);
  assert.equal(viewer.appearance,null);
  trace.length=0;viewer.drawMesh(project([0,0,1]),[0,0,1]);
  assert.equal(trace.join(','),'cad','a failed image preserves the CAD geometry and colour');
  body.textures.front='/assets/pado-sat-surface-huge.png';
  assert.equal(await viewer.loadAppearance(),false,'oversized decoded images are rejected');
  const count=requests.length;
  assert.equal(await viewer.loadAppearance('https://example.com/appearance.json'),false);
  assert.equal(requests.length,count,'external appearance URLs are rejected before fetch');

  viewer.mesh=api.meshFromJson(JSON.parse(fs.readFileSync(process.argv[2],'utf8')));
  body=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
  assert.equal(await viewer.loadAppearance(),true,'actual supplied appearance loads');
  for (const eye of [[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1]]) {
    assert.ok(viewer.appearance.triangles.some((triangle)=>
      triangle.normal.reduce((sum,value,i)=>sum+value*eye[i],0)>0),
      `actual appearance provides an outward surface for body side ${eye}`);
  }
})().catch((error)=>{console.error(error);process.exitCode=1;});
"""


class RosAttitudeViewerTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_actual_deployed_cad_asset_parses(self):
        self.assertTrue(ASSET.is_file(), "Deployed STEP mesh asset is missing")
        result = subprocess.run(
            ["node", "-e", NODE_ASSET_TEST, str(VIEWER), str(ASSET), str(APPEARANCE)],
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

    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_bounded_surface_textures_uv_and_canvas_fallback(self):
        result = subprocess.run(
            ["node", "-e", NODE_APPEARANCE_TEST, str(VIEWER), str(ASSET), str(APPEARANCE)], capture_output=True,
            text=True, timeout=10, check=False, env=os.environ.copy(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
