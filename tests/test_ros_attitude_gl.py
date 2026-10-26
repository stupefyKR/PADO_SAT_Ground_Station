"""The CAD layer uses a depth buffer and preserves the 2D viewer projection."""

from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = r"""
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const calls=[], sources=[], uniforms=new Map();
let serial=0;
const gl={};
for (const name of ['VERTEX_SHADER','FRAGMENT_SHADER','COMPILE_STATUS','LINK_STATUS',
  'ARRAY_BUFFER','STATIC_DRAW','MAX_TEXTURE_SIZE','TEXTURE_2D','UNPACK_FLIP_Y_WEBGL',
  'UNPACK_PREMULTIPLY_ALPHA_WEBGL','TEXTURE_WRAP_S','TEXTURE_WRAP_T','CLAMP_TO_EDGE',
  'TEXTURE_MIN_FILTER','TEXTURE_MAG_FILTER','LINEAR','RGBA','UNSIGNED_BYTE',
  'COLOR_BUFFER_BIT','DEPTH_BUFFER_BIT','DEPTH_TEST','LEQUAL','CULL_FACE','BLEND',
  'FLOAT','TRIANGLES','SRC_ALPHA','ONE_MINUS_SRC_ALPHA','ONE','POLYGON_OFFSET_FILL','TEXTURE0'])
  gl[name]=++serial;
for (const name of ['createShader','createProgram','createBuffer','createTexture'])
  gl[name]=()=>({kind:name,id:++serial});
for (const name of ['compileShader','attachShader','linkProgram','bindBuffer','bufferData',
  'deleteBuffer','deleteTexture','deleteShader','deleteProgram','bindTexture','pixelStorei',
  'texParameteri','texImage2D','enableVertexAttribArray','vertexAttribPointer','viewport',
  'clearColor','clearDepth','clear','enable','disable','depthFunc','depthMask','useProgram',
  'drawArrays','blendFuncSeparate','polygonOffset','activeTexture','flush'])
  gl[name]=(...args)=>calls.push([name,...args]);
gl.shaderSource=(shader,source)=>sources.push(source);
gl.getShaderParameter=()=>true;
gl.getProgramParameter=()=>true;
gl.getAttribLocation=(_program,name)=>({a_position:0,a_normal:1,a_color:2,a_uv:3}[name]);
gl.getUniformLocation=(_program,name)=>name;
gl.getParameter=()=>4096;
gl.uniform3fv=(name,value)=>uniforms.set(name,Array.from(value));
gl.uniform4f=(name,...value)=>uniforms.set(name,value);
gl.uniform1i=(name,value)=>{uniforms.set(name,value);calls.push(['uniform1i',name,value]);};
gl.isContextLost=()=>false;
const canvas={width:1,height:1,addEventListener(){},getContext:(type)=>type==='webgl'?gl:null};
const scope={window:{},document:{createElement:()=>canvas}};
vm.createContext(scope);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),scope);
const mesh=scope.window.rosAttitudeViewer.meshFromJson(
  JSON.parse(fs.readFileSync(process.argv[3],'utf8')));
const compiled=scope.window.rosAttitudeViewer.appearanceFromJson(
  JSON.parse(fs.readFileSync(process.argv[4],'utf8')),mesh);
const images=new Map(Object.keys(compiled.textures).map((id)=>[id,{id}]));
const appearance={triangles:compiled.triangles.map((triangle)=>({...triangle,
  asset:{image:images.get(triangle.texture),width:1593,height:779}}))};
const renderer=scope.window.rosAttitudeGl.create(mesh);
assert.ok(renderer,'a supported browser creates the depth-buffered layer');
const basis={eye:[0,0,1],right:[1,0,0],up:[0,1,0]};
assert.equal(renderer.render(basis,165,760,500,appearance),canvas);
assert.deepEqual(calls.find((call)=>call[0]==='clearColor').slice(1),[0,0,0,0],
  'the CAD layer is transparent so the existing sky stays visible');
assert.ok(calls.some((call)=>call[0]==='enable'&&call[1]===gl.DEPTH_TEST));
assert.ok(calls.some((call)=>call[0]==='depthMask'&&call[1]===true));
assert.ok(calls.some((call)=>call[0]==='depthFunc'&&call[1]===gl.LEQUAL));
assert.ok(calls.some((call)=>call[0]==='pixelStorei'&&
  call[1]===gl.UNPACK_FLIP_Y_WEBGL&&call[2]===false),
  'source image UVs retain their top-left orientation');
const projection=uniforms.get('u_projection');
const [sx,sy,a,b]=projection;
const point=[0.2,-0.1,0.4],w=4.3-point[2];
const screenX=760/2+point[0]*sx/w*760/2;
const screenY=500/2-point[1]*sy/w*500/2;
assert.ok(Math.abs(screenX-(760/2+point[0]*165*4.3/w))<1e-9);
assert.ok(Math.abs(screenY-(500/2-point[1]*165*4.3/w))<1e-9);
assert.ok(Math.abs((a*0.1+b)/0.1+1)<1e-9,'near plane uses the full depth range');
assert.ok(Math.abs((a*20+b)/20-1)<1e-9,'far plane uses the full depth range');
assert.ok(sources.some((source)=>source.includes('dot(v_normal, u_eye)')&&
  source.includes('pixel.a <= 0.01')),'back surfaces and transparent cutouts do not write depth');
const uploads=calls.filter((call)=>call[0]==='texImage2D').length;
const buffers=calls.filter((call)=>call[0]==='bufferData').length;
assert.equal(uploads,images.size);
assert.equal(renderer.render(basis,165,760,500,appearance),canvas);
assert.equal(calls.filter((call)=>call[0]==='texImage2D').length,uploads,
  'dragging reuses uploaded source textures');
assert.equal(calls.filter((call)=>call[0]==='bufferData').length,buffers,
  'dragging reuses CAD and texture geometry buffers');
assert.equal(renderer.render(basis,200,900,600,appearance),canvas);
assert.equal(canvas.width,900);assert.equal(canvas.height,600);
assert.equal(renderer.render(basis,200,900,600,null),canvas);
assert.equal(renderer.groups.length,0);
assert.equal(calls.filter((call)=>call[0]==='deleteTexture').length,uploads);
renderer.dispose();
assert.equal(renderer.render(basis,165,760,500,appearance),null);
assert.ok(calls.some((call)=>call[0]==='deleteProgram'));
canvas.getContext=()=>null;
assert.equal(scope.window.rosAttitudeGl.create(mesh),null,
  'unsupported browsers return control to the colour-only Canvas fallback');
"""


class RosAttitudeGlTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_depth_projection_gpu_cache_and_fallback(self):
        result = subprocess.run([
            "node", "-e", NODE_TEST,
            str(ROOT / "web/ops/attitude_gl.js"),
            str(ROOT / "web/ops/attitude_viewer.js"),
            str(ROOT / "web/assets/pado-sat-deployed-mesh.json"),
            str(ROOT / "web/assets/pado-sat-appearance.json"),
        ], capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
