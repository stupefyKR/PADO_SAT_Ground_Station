"""History scenes use measured quaternions and make unknown attitude visible."""

from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const text=[],trace=[];
const ctx={};
for(const name of ['fillRect','stroke','beginPath','moveTo','lineTo','closePath',
  'fill','clip','save','restore','setLineDash','arc']) ctx[name]=()=>{};
ctx.fillText=value=>text.push(value);
const canvas={width:760,height:500,getContext:()=>ctx,addEventListener(){}};
const scope={window:{},requestAnimationFrame:callback=>callback()};
vm.createContext(scope);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),scope);
const compute=scope.rosAttitudeMath.computeState;
const viewer=scope.window.rosAttitudeViewer.create(canvas);
viewer.mesh={};
viewer.drawStars=()=>{};
viewer.drawMesh=()=>trace.push('CAD');
viewer.drawBodyAxes=()=>trace.push('BODY_AXES');
viewer.drawAxisLabels=()=>trace.push('BODY_LABELS');
viewer.drawEarthHorizon=(_basis,_pixels,earth,sun)=>trace.push({earth:Array.from(earth),sun:sun&&Array.from(sun)});
viewer.drawSunDisc=()=>trace.push('SUN');
const utc='2026-10-29T00:00:05Z',s=Math.sqrt(.5);
const attitude={utc,frame:'SIM_BODY_TO_ITRF',quaternion_wxyz:[s,0,0,s]};
const inputs={utc,position_itrf_m:[6878137,0,0],sun_itrf:[0,0,1],
  positionSource:'GNSS_TM',attitudeStatus:'MEASURED'};
const near=(actual,expected)=>actual.forEach((value,index)=>
  assert.ok(Math.abs(value-expected[index])<1e-9,`${actual} != ${expected}`));

const known=compute({...inputs,attitude});
assert.equal(known.orientationKnown,true);
assert.equal(known.positionKnown,true);
assert.equal(known.displayFrame,'BODY');
near(known.satelliteItrf,[6878137,0,0]);
near(known.earthBody,[0,1,0]);
near(known.sunBody,[0,0,1]);
near(known.bodyToItrf[0],[0,1,0]);
assert.ok(Math.abs(known.earthAlignment)<1e-9,'received non-nadir attitude is not forced to -X');
viewer.setDirections(known);
assert.ok(trace.includes('CAD'));
assert.ok(trace.includes('BODY_AXES'));

trace.length=0;text.length=0;
const unknown=compute({...inputs,attitude:null,attitudeStatus:'UNKNOWN'});
assert.equal(unknown.orientationKnown,false);
assert.equal(unknown.displayFrame,'ITRF');
near(unknown.earthBody,[-1,0,0]);
assert.equal(unknown.earthAlignment,null,'there is no implied nadir alignment');
viewer.setDirections(unknown);
assert.ok(!trace.includes('CAD'),'no CAD orientation is displayed when the attitude is unknown');
assert.ok(!trace.includes('BODY_AXES'));
assert.ok(!trace.includes('BODY_LABELS'));
assert.ok(trace.some(item=>item.earth),'the known position still places Earth in the scene');
assert.ok(text.includes('자세 정보 없음'));
assert.ok(text.some(value=>value.includes('위치만 표시')));

// A late quaternion at this same historical time can restore the measured
// scene. A subsequent gap clears it again; no old pose survives the update.
trace.length=0;
viewer.setDirections(compute({...inputs,attitude}));
assert.ok(trace.includes('CAD'));
trace.length=0;
viewer.setDirections(compute({...inputs,attitude,attitudeStatus:'UNKNOWN'}));
assert.ok(!trace.includes('CAD'),'UNKNOWN cannot accidentally retain a supplied old quaternion');
trace.length=0;text.length=0;
viewer.setDirections(null);
assert.equal(viewer.directions,null);
assert.equal(viewer.orientationKnown,false);
assert.ok(!trace.includes('CAD'));
assert.ok(text.includes('위치 정보도 없습니다'));

// Sun coverage is independent of GNSS and attitude coverage. Do not invent a
// solar direction or a day/night terminator to make a historical scene full.
trace.length=0;text.length=0;
const noSun=compute({...inputs,sun_itrf:null,attitude});
assert.equal(noSun.sunKnown,false);
assert.equal(noSun.sunBody,null);
viewer.setDirections(noSun);
assert.ok(trace.includes('CAD'));
assert.ok(!trace.includes('SUN'));
assert.ok(trace.some(item=>item.earth&&item.sun===null));
assert.ok(text.includes('태양 방향 자료 없음'));

const noPosition=compute({...inputs,position_itrf_m:null,attitude});
assert.equal(noPosition.orientationKnown,true);
assert.equal(noPosition.positionKnown,false);
assert.equal(noPosition.earthBody,null);
trace.length=0;text.length=0;
viewer.setDirections(noPosition);
assert.ok(trace.includes('CAD'));
assert.ok(!trace.some(item=>item.earth));
assert.ok(text.some(value=>value.includes('위치 정보 없음')));
for(const frame of ['BODY_TO_ITRF','SIM_BODY_TO_ITRF_NADIR'])
  assert.equal(compute({...inputs,attitude:{...attitude,frame}}).orientationKnown,true);
for(const invalid of [{...attitude,frame:'BODY_TO_UNKNOWN'},
  {...attitude,quaternion_wxyz:[3,0,0,0]},
  {...attitude,quaternion_wxyz:[NaN,0,0,0]}])
  assert.equal(compute({...inputs,attitude:invalid}).orientationKnown,false);
assert.equal(compute({...inputs,utc:'not-a-time',attitude}),null);
assert.equal(compute({...inputs,position_itrf_m:[0,0,0]}).positionKnown,false);
"""


class RosPoseSceneTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js required for scene logic")
    def test_measured_pose_unknown_gaps_position_and_missing_sun(self):
        result = subprocess.run([shutil.which("node"), "-e", NODE_TEST,
            str(ROOT / "web/ops/attitude_math.js"), str(ROOT / "web/ops/attitude_viewer.js")],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
