"""ORBIT polar diagrams annotate FDS events without estimating a sampled maximum."""

from pathlib import Path
import shutil
import subprocess
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "web/orbit/orbit.js"

NODE_TEST = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Context {
  constructor(){this.text=[];this.strokes=[];this.arcs=[];this.path=[];}
  clearRect(){this.text=[];this.strokes=[];this.arcs=[];}
  beginPath(){this.path=[];}
  arc(x,y,r){this.arcs.push({x,y,r});this.path.push(['A',x,y,r]);}
  moveTo(x,y){this.path.push(['M',x,y]);}
  lineTo(x,y){this.path.push(['L',x,y]);}
  stroke(){this.strokes.push({width:this.lineWidth,path:this.path.slice()});}
  fill(){}
  fillText(text,x,y){this.text.push({text,x,y});}
}
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.clientWidth=280;
    this.clientHeight=224;this.context=new Context();}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  setAttribute(){}
  getContext(){return this.context;}
}
const elements=new Map(),get=id=>{
  if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);
};
const scope={Image:class{},window:{devicePixelRatio:1},
  document:{createElement:tag=>new Element(tag),getElementById:get}};
vm.createContext(scope);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
const aos=Date.parse('2026-10-28T23:59:40Z'),peak=aos+73000,los=aos+180000;
const pass={aos,peak,los,max:67.8};
const station={id:'yonsei',minimum_elevation_deg:10,passes:[pass]};
scope.fixturePass=pass;scope.fixtureStation=station;scope.sampleCalls=[];
vm.runInContext(`
  sampleAt=(time)=>{sampleCalls.push(time);return {xyz:[time,0,0],lat:0,lon:0,alt:500000};};
  stationObservation=()=>({az:45,el:10});
  isCurrentData=()=>true;
  orbitState.stations=[fixtureStation];orbitState.time=fixturePass.aos-60000;
  orbitState.data={info:{norad_id:99999}};
`,scope);
for(const scale of [1,2]){
  scope.window.devicePixelRatio=scale;
  const canvas=new Element('canvas');scope.drawPolar(canvas,station,pass,aos-1000);
  const labels=canvas.context.text.map(item=>item.text);
  for(const label of ['0°','30°','60°','AOS','LOS','MAX'])assert.ok(labels.includes(label),label);
  assert.ok(scope.sampleCalls.includes(peak),'exact FDS peak time is interpolated between regular samples');
  const radius=Math.min(canvas.width*.37,canvas.height*.39),d=radius*(90-pass.max)/90;
  const x=canvas.width/2+Math.SQRT1_2*d,y=canvas.height/2-Math.SQRT1_2*d;
  const trajectory=canvas.context.strokes.find(item=>item.width===2.5*scale);
  assert.ok(trajectory.path.some(point=>Math.abs(point[1]-x)<1e-8&&Math.abs(point[2]-y)<1e-8),
    'FDS maximum elevation is a trajectory vertex, not a detached mark or coarse sampled maximum');
  assert.ok(canvas.context.arcs.some(point=>Math.abs(point.x-x)<1e-8&&Math.abs(point.y-y)<1e-8&&point.r===3.5*scale));
  assert.ok(canvas.context.text.every(item=>item.x>=0&&item.x<=canvas.width&&item.y>=0&&item.y<=canvas.height));
}
scope.updateOrbitUi();
const section=get('orbit-stations').children[0],caption=section.children.at(-1);
assert.deepEqual(caption.children.map(row=>row.textContent),[
  'AOS · 2026-10-28 23:59:40 UTC','MAX 67.8° · 2026-10-29 00:00:53 UTC',
  'LOS · 2026-10-29 00:02:40 UTC']);
const canvas=new Element('canvas');scope.drawPolar(canvas,station,{...pass,peak:NaN},aos-1000);
assert.ok(!canvas.context.text.some(item=>item.text==='MAX'),'missing peak metadata cannot become a fabricated maximum');
scope.drawPolar(canvas,station,{...pass,peak:aos,max:10},aos-1000);
const labels=canvas.context.text.filter(item=>['AOS','LOS','MAX'].includes(item.text));
for(let first=0;first<labels.length;first++)for(let next=first+1;next<labels.length;next++){
  const a=labels[first],b=labels[next],s=scope.window.devicePixelRatio;
  assert.ok(a.x+18*s<=b.x||b.x+18*s<=a.x||a.y+6*s<=b.y-6*s||b.y+6*s<=a.y-6*s,
    'coincident clipped-pass events retain separate readable labels');
}
scope.drawPolar(canvas,station,null,aos-1000);
assert.ok(canvas.context.text.some(item=>item.text==='예측 기간 내 PASS 없음'));
assert.ok(!canvas.context.text.some(item=>['AOS','LOS','MAX'].includes(item.text)));
"""


class OrbitPolarAnnotationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_ring_event_labels_peak_position_and_utc_date_rollover(self):
        result = subprocess.run([shutil.which("node"), "-e", NODE_TEST, str(SOURCE)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
