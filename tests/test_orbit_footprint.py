"""The live white visibility boundary uses FDS ECEF positions in both views."""

from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "web/orbit/orbit.js"


NODE_TEST = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Context {
  constructor(){this.strokes=[];this.path=[];}
  clearRect(){this.strokes=[];}
  beginPath(){this.path=[];}
  moveTo(x,y){this.path.push(['M',x,y]);}
  lineTo(x,y){this.path.push(['L',x,y]);}
  stroke(){this.strokes.push({color:this.strokeStyle,width:this.lineWidth,path:this.path.slice()});}
  arc(){} fill(){} fillText(){}
}
class Element {
  constructor(){this.width=1000;this.height=500;this.textContent='';this.hidden=false;this.context=new Context();}
  getContext(){return this.context;}
  addEventListener(){} setAttribute(){}
}
const elements=new Map(),get=id=>{
  if(!elements.has(id))elements.set(id,new Element());return elements.get(id);
};
const scope={Image:class{},window:{devicePixelRatio:1},document:{createElement:()=>new Element(),getElementById:get}};
vm.createContext(scope);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
const R=6378137,r=R+500000,dot=(a,b)=>a.reduce((n,v,i)=>n+v*b[i],0);
const at=(lon,lat)=>{lon*=Math.PI/180;lat*=Math.PI/180;return[r*Math.cos(lat)*Math.cos(lon),r*Math.cos(lat)*Math.sin(lon),r*Math.sin(lat)];};
const close=(actual,expected,tolerance=1e-8)=>assert.ok(Math.abs(actual-expected)<tolerance,`${actual} != ${expected}`);
const horizon=scope.visibilityFootprint([r,0,0]);
close(horizon.angularRadiusRad,Math.acos(R/r));
assert.ok(horizon.groundRadiusM>2400000&&horizon.groundRadiusM<2500000);
assert.equal(horizon.points.length,257);
assert.deepEqual(horizon.points[0],horizon.points.at(-1),'outline closes exactly');
for(const [lon,lat] of [[0,0],[179,30],[-179,-30],[127,37],[0,90],[0,-90]]){
  const satellite=at(lon,lat);
  for(const elevation of [0,10,20]){
    const footprint=scope.visibilityFootprint(satellite,elevation);
    for(const point of footprint.points){
      close(Math.hypot(...point.xyz),R,1e-7);
      assert.ok(Number.isFinite(point.lon)&&Number.isFinite(point.lat));
      const delta=satellite.map((value,i)=>value-point.xyz[i]);
      const observed=Math.asin(dot(delta,point.xyz)/R/Math.hypot(...delta))*180/Math.PI;
      close(observed,elevation,1e-9,'boundary is a tangent/elevation contour');
    }
    if(elevation)assert.ok(footprint.angularRadiusRad<horizon.angularRadiusRad);
  }
}
for(const [xyz,elevation] of [[[R,0,0],0],[[R-1,0,0],0],[[NaN,0,0],0],
  [[Number.MAX_VALUE,Number.MAX_VALUE,Number.MAX_VALUE],0],[[r,0,0],-1],[[r,0,0],90]])
  assert.equal(scope.visibilityFootprint(xyz,elevation),null);

const dateline=scope.visibilityFootprint(at(179,30));let splits=0;
for(let index=1;index<dateline.points.length;index++){
  const parts=scope.footprintFlatSegments(dateline.points[index-1],dateline.points[index]);
  if(parts.length===2){splits++;close(parts[0][1].lat,parts[1][0].lat);close(Math.abs(parts[0][1].lon-parts[1][0].lon),360);}
  for(const pair of parts){
    assert.ok(Math.abs(pair[1].lon-pair[0].lon)<=180,'no line jumps across the map');
    for(const point of pair)assert.ok(point.lon>=-180&&point.lon<=180&&Math.abs(point.lat)<=90);
  }
}
assert.ok(splits>=2,'dateline crossings continue at both map edges');
for(const [first,last,expected] of [[179,-180,180],[-179,180,-180],[180,-180,180],[-180,180,-180]]){
  const a={lon:first,lat:10},b={lon:last,lat:11};
  const parts=scope.footprintFlatSegments(a,b);
  assert.equal(parts.length,1,'an exact boundary endpoint needs no artificial crossing');
  close(parts[0][1].lon,expected);
  close(parts[0][1].lat,11);
  assert.ok(Math.abs(parts[0][1].lon-parts[0][0].lon)<=1,'exact +/-180 endpoint never makes a map-wide chord');
  assert.equal(b.lon,last,'projection unwrapping does not mutate the world point');
}
for(const pole of [90,-90]){
  const footprint=scope.visibilityFootprint(at(0,pole));
  for(let index=1;index<footprint.points.length;index++)for(const pair of scope.footprintFlatSegments(footprint.points[index-1],footprint.points[index])){
    assert.ok(Math.abs(pair[1].lon-pair[0].lon)<5,'polar circle is continuous without map-wide chords');
    close(pair[0].lat,pole>0?90-horizon.angularRadiusRad*180/Math.PI:-90+horizon.angularRadiusRad*180/Math.PI);
  }
}

const forward=[1,0,0],back=scope.visibilityFootprint([-r,0,0]);
for(let index=1;index<back.points.length;index++)
  assert.equal(scope.footprintVisibleSegment(back.points[index-1],back.points[index],forward),null,'rear outline is hidden by Earth');
const limb=scope.visibilityFootprint(at(75,0));let visible=0,hidden=0,clipped=0;
for(let index=1;index<limb.points.length;index++){
  const a=limb.points[index-1],b=limb.points[index],part=scope.footprintVisibleSegment(a,b,forward);
  if(!part){hidden++;continue;}visible++;
  for(const point of part){assert.ok(dot(point.xyz,forward)>-1e-8);close(Math.hypot(...point.xyz),R,1e-7);}
  if(dot(a.xyz,forward)*dot(b.xyz,forward)<0){clipped++;close(Math.min(...part.map(point=>Math.abs(dot(point.xyz,forward)))),0,1e-7);}
}
assert.ok(visible&&hidden&&clipped===2,'both limb crossings are clipped exactly');

scope.now=Date.now();scope.r=r;
vm.runInContext(`
  orbitState.lon=0;orbitState.lat=0;orbitState.stations=[];orbitState.time=now;
  orbitState.data={start:now-60000,end:now+60000,inputEpoch:now-120000,info:{norad_id:99999,orbital_period_s:5500}};
  orbitState.samples=[{t:now-60000,xyz:[r,0,0],sun:[1,0,0],lon:0,lat:0,alt:500000},
    {t:now+60000,xyz:[r*.99,r*.1,0],sun:[1,0,0],lon:6,lat:0,alt:500000}];
`,scope);
const globe=new Element(),flat=new Element();
scope.drawOverlay(globe,false);scope.drawOverlay(flat,true);
const white=canvas=>canvas.context.strokes.find(stroke=>stroke.color==='#ffffff');
assert.ok(white(globe)?.path.length>100,'globe has a white visibility outline');
assert.ok(white(flat)?.path.length>100,'ground track has the same white visibility outline');
const before=JSON.stringify(white(globe).path);vm.runInContext('orbitState.time=now+20000',scope);scope.drawOverlay(globe,false);
assert.notEqual(JSON.stringify(white(globe).path),before,'boundary follows the interpolated current satellite');
vm.runInContext('readOrbitServer=()=>{};orbitRender=()=>{};',scope);
scope.chooseOrbitTarget('iss');scope.drawOverlay(globe,false);
assert.equal(white(globe),undefined,'a target switch cannot leave the old footprint');
vm.runInContext('orbitState.data={start:now-1000,end:now+1000,inputEpoch:now-73*3600000};orbitState.time=now',scope);
scope.drawOverlay(globe,false);assert.equal(white(globe),undefined,'expired FDS inputs have no footprint');
"""


class OrbitFootprintTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_elevation_geometry_wrap_limb_and_live_clear(self):
        result = subprocess.run([shutil.which("node"), "-e", NODE_TEST, str(SOURCE)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_both_views_describe_visibility_without_relabeling_contact(self):
        markup = (ROOT / "web/orbit/index.html").read_text(encoding="utf-8")
        self.assertIn('id="orbit-footprint-elevation"', markup)
        self.assertIn('0° · 지평선', markup)
        self.assertIn('지상국 컨택 예측은 PASS 기준', markup)
        self.assertNotIn('LTAN 06h', markup)


if __name__ == "__main__":
    unittest.main()
