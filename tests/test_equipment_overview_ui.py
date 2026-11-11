"""Fixed engineering CAD callouts stay aligned and respect TM quality."""

from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "web/ops/equipment_overview.js"


NODE_TEST = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes=new Map(),drawing=[],listeners=[],renders=[],scrolled=[];
class Element {
  constructor(tag='div') {this.tag=tag;this.children=[];this.attributes={};this.dataset={};
    this.textContent='';this.className='';this.width=1200;this.height=860;
    this.classList={add:name=>{this.className+=' '+name;}};this.events={};}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=[...items];}
  setAttribute(key,value){this.attributes[key]=String(value);}
  addEventListener(name,fn){this.events[name]=fn;listeners.push([this.tag,name]);}
  querySelector(selector){const id=selector.match(/data-equipment="([^"]+)"/)?.[1];
    return all(this).find(node=>node.dataset.equipment===id);}
  scrollIntoView(){scrolled.push(this.dataset.equipment);}
  getContext(){return context2d;}
  set innerHTML(_value){throw Error('telemetry must not be injected as HTML');}
}
const context2d=new Proxy({}, {get(target,name){
  if(name==='getImageData') return (_x,_y,w,h)=>({data:new Uint8ClampedArray(w*h*4)});
  if(!(name in target)) target[name]=(...args)=>{drawing.push([name,...args]);};
  return target[name];
},set(target,name,value){target[name]=value;return true;}});
const element=id=>{if(!nodes.has(id))nodes.set(id,new Element(id==='equipment-cad-canvas'?'canvas':'div'));return nodes.get(id);};
const document={getElementById:element,createElement:tag=>new Element(tag),createElementNS:(_ns,tag)=>new Element(tag)};
const meshRaw=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const appearanceRaw=JSON.parse(fs.readFileSync(process.argv[4],'utf8'));
const response=value=>({ok:true,headers:{get:()=>null},arrayBuffer:async()=>{
  const bytes=Buffer.from(JSON.stringify(value));return bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength);}});
let fetched=[];
class Image {
  constructor(){this.naturalWidth=2;this.naturalHeight=2;}
  set src(path){fetched.push(path);Promise.resolve().then(()=>this.onload?.());}
}
let mesh,rendererCount=0;
const scope={window:{},document,Image,TextDecoder,Uint8ClampedArray,setTimeout,clearTimeout,
  fetch:async path=>{fetched.push(path);return response(path.endsWith('mesh.json')?meshRaw:appearanceRaw);}};
vm.createContext(scope);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
scope.window.rosAttitudeGl={create(value){mesh=value;rendererCount++;return {render(basis,pixels,width,height,appearance){
  renders.push({basis,pixels,width,height,appearance});return new Element('cad-layer');}};}};
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),scope);
const ui=scope.window.rosEquipmentOverview,now=Date.now(),iso=offset=>new Date(now+offset).toISOString();
const reading=(id,value,extra={})=>({id,label:id,unit:'V',value,decimals:2,
  sampled_utc:iso(-10000),received_utc:iso(-5000),freshness:'CURRENT',
  limit_state:'IN_RANGE',lower:4.5,upper:5.5,max_age_s:120,...extra});
const equipment=(id,order,readings,side='left')=>({id,label:id,group:'EPS',status:'CURRENT',
  anchor_body_mm:[10,5,order*25-32.5],anchor_kind:'BOARD_CENTER_PROXY',callout:{side,order},readings});
const fixture=()=>({schema:'pado.equipment-status.v1',generated_utc:iso(0),
  layout:{schema:'pado.equipment-layout.v1',units:'mm',frame:'BODY',camera:{yaw_rad:-0.98,pitch_rad:.48,zoom:1},
    source:{file:'stack.html',revision:'261026',plan:'p2',provisional:true}},
  equipment:[equipment('pdu1',0,[reading('voltage',5)]),
    equipment('ax100',1,[reading('temperature',80,{unit:'°C',lower:0,upper:45,limit_state:'OUT_OF_RANGE'})],'right'),
    equipment('bpx',2,[reading('battery',14.4,{lower:11.8,upper:16,sampled_utc:iso(-900000)})]),
    equipment('ifb',3,[],'right'),
    equipment('state',4,[reading('mode',true,{lower:null,upper:null,unit:'',limit_state:'UNCONFIGURED'})],'right')]});
const all=(node,result=[])=>{result.push(node);for(const child of node.children||[])all(child,result);return result;};
const settle=async()=>{for(let i=0;i<15;i++)await new Promise(resolve=>setImmediate(resolve));};

(async()=>{
  const value=reading('temperature',80,{unit:'°C',lower:0,upper:45,limit_state:'OUT_OF_RANGE'});
  assert.equal(ui.readingState(value,now).limit_state,'OUT_OF_RANGE');
  assert.equal(ui.readingState({...value,sampled_utc:iso(-900000)},now).limit_state,'INDETERMINATE');
  assert.equal(ui.readingState({...value,sampled_utc:iso(-900000),max_age_s:1200},now).freshness,'CURRENT');
  assert.equal(ui.readingState({...value,value:null},now).freshness,'MISSING');
  assert.equal(ui.readingState(reading('mode',true,{lower:null,upper:null}),now).limit_state,'UNCONFIGURED');
  assert.equal(ui.readingState(reading('fsw_version','FSW-1.2',{lower:null,upper:null}),now).limit_state,'UNCONFIGURED');
  assert.equal(ui.readingState(reading('voltage',5,{limit_state:'OUT_OF_RANGE'}),now).limit_state,'INDETERMINATE',
    'inconsistent limit labels cannot become healthy or alarm claims');
  const normalized=ui.normalizePayload(fixture(),now);
  assert.equal(normalized.equipment[1].status,'ALARM');
  assert.equal(normalized.equipment[2].status,'STALE');
  assert.equal(normalized.equipment[3].status,'MISSING');
  assert.equal(normalized.equipment[4].status,'CURRENT','fresh status is distinct from configured healthy state');
  assert.equal(normalized.equipment[4].readings[0].limit_state,'UNCONFIGURED');
  assert.throws(()=>ui.normalizePayload({...fixture(),equipment:[{...fixture().equipment[0],anchor_body_mm:[NaN,0,0]}]}));
  let calls=0,serve=async()=>fixture();
  const controller=ui.create({api:async path=>{assert.equal(path,'/api/ros/equipment-status');calls++;return serve();}});
  await controller.refresh();await settle();
  assert.ok(mesh&&mesh.triangles.length===meshRaw.triangles.length&&mesh.triangles.length>1000,
    'fixed view uses the same detailed STEP-derived CAD');
  assert.ok(renders.length,'existing depth-buffered CAD renderer is reused');
  assert.ok(renders.at(-1).appearance?.triangles.length,'provided surface images are reused');
  assert.ok(fetched.includes('/assets/pado-sat-deployed-mesh.json'));
  assert.ok(!listeners.some(([tag,type])=>tag==='canvas'&&['pointerdown','wheel','keydown'].includes(type)),
    'fixed CAD does not install orbit-camera controls');
  const first=nodes.get('equipment-callouts').children.find(item=>item.attributes['data-equipment']==='pdu1');
  const points=first.children.find(item=>item.tag==='polyline').attributes.points.split(' ')[0].split(',').map(Number);
  const frame=renders.at(-1),expected=ui.projectAnchor([10,5,-32.5],mesh,frame.basis,frame.pixels,frame.width,frame.height);
  assert.ok(Math.abs(points[0]-expected.x)<1e-9&&Math.abs(points[1]-expected.y)<1e-9,
    'callout origin and CAD share centre,scale,camera and perspective');
  assert.equal(new Set(renders.map(item=>JSON.stringify(item.basis))).size,1,'telemetry does not rotate the fixed camera');
  const content=all(nodes.get('equipment-matrix'));
  assert.ok(content.some(item=>item.className==='equipment-reading reading-in_range'));
  assert.ok(content.some(item=>item.className==='equipment-reading reading-stale'));
  assert.ok(content.some(item=>item.textContent==='범위 미설정'));
  assert.ok(!content.some(item=>item.textContent==='정상'));
  assert.ok(content.some(item=>item.tag==='time'&&item.textContent.includes('UTC')));

  for (const balanced of [true,false]) {
    const dense=fixture();
    dense.equipment=Array.from({length:32},(_unused,index)=>equipment('board'+index,index,
      [reading('temperature',5),reading('voltage',5),reading('current',5)],balanced&&index%2?'right':'left'));
    serve=async()=>dense;await controller.refresh();
    const boxes=ui.calloutLayout(dense.equipment);
    assert.ok(boxes.every(box=>box.headerBaseline>0&&box.headerBaseline<=box.height-2));
    assert.ok(boxes.every(box=>!box.readingCount||box.readingBaseline+
      (box.readingCount-1)*box.lineHeight<=box.height-8),'dense layouts omit summary rows before they overflow');
    for(const group of nodes.get('equipment-callouts').children){
      const rect=group.children.find(child=>child.tag==='rect');
      for(const label of group.children.filter(child=>child.tag==='text')){
        assert.ok(+label.attributes.y>=+rect.attributes.y);
        assert.ok(+label.attributes.y<=+rect.attributes.y+ +rect.attributes.height-2,
          'all summary text stays inside its component card');
      }
    }
    const last=nodes.get('equipment-callouts').children.find(item=>item.attributes['data-equipment']==='board31');
    last.events.click();
    assert.equal(scrolled.at(-1),'board31','dense callouts reveal all component readings in the central matrix');
    const selectedRow=all(nodes.get('equipment-matrix')).find(item=>item.dataset.equipment==='board31');
    assert.equal(all(selectedRow).filter(item=>item.className==='equipment-reading reading-in_range').length,3);
  }
  serve=async()=>fixture();await controller.refresh();
  let resolveExpiry;serve=()=>new Promise(resolve=>{resolveExpiry=resolve;});
  const expiring=controller.refresh();
  scope.savedDateNow=vm.runInContext('Date.now',scope);scope.expiryNow=now+180000;
  vm.runInContext('Date.now=()=>expiryNow',scope);
  await controller.refresh();
  assert.ok(!all(nodes.get('equipment-matrix')).some(item=>item.className.includes('reading-in_range')),
    'pending network request cannot preserve current styling beyond measurement expiry');
  resolveExpiry(fixture());await expiring;
  vm.runInContext('Date.now=savedDateNow',scope);delete scope.savedDateNow;delete scope.expiryNow;
  serve=async()=>fixture();await controller.refresh();

  serve=async()=>{throw Error('network');};await controller.refresh();
  assert.match(nodes.get('equipment-status').textContent,/조회 불가/);
  assert.ok(!all(nodes.get('equipment-matrix')).some(item=>item.className.includes('reading-in_range')),
    'failed reads clear previously current healthy styling');
  let resolveLate;serve=()=>new Promise(resolve=>{resolveLate=resolve;});
  const pending=controller.refresh(),before=calls;
  await controller.refresh();assert.equal(calls,before,'refresh does not overlap its own request');
  controller.stop();resolveLate(fixture());await pending;
  assert.match(nodes.get('equipment-status').textContent,/조회 불가/,'late response afterstop cannot restore good readings');
  const malicious=fixture();malicious.equipment[0].label='<img src=x onerror=alert(1)>';
  malicious.equipment[0].readings.push(reading('version','<script>bad()</script>',{lower:null,upper:null}));
  serve=async()=>malicious;await controller.start();
  assert.ok(all(nodes.get('equipment-matrix')).some(item=>item.textContent==='<img src=x onerror=alert(1)>'));
  assert.ok(all(nodes.get('equipment-matrix')).some(item=>item.textContent==='<script>bad()</script> V'),
    'bounded string telemetry is plain text and does not become a health claim');
  assert.match(nodes.get('equipment-layout-source').textContent,/임시 배치/);
  controller.stop();
  const prior=rendererCount;
  const restarted=ui.create({api:async()=>fixture()});
  restarted.stop();await restarted.start();await settle();
  assert.equal(rendererCount,prior+1,'stop/start during CAD load installs only the current renderer');
  restarted.stop();
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


class SectionParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sections = []
        self.parents = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "section":
            self.sections.append(values.get("id"))
        if values.get("id"):
            self.parents[values["id"]] = set(self.sections)

    def handle_endtag(self, tag):
        if tag == "section":
            self.sections.pop()


class EquipmentOverviewUiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_fixed_cad_callout_projection_quality_and_request_lifecycle(self):
        result = subprocess.run([shutil.which("node"), "-e", NODE_TEST,
            str(ROOT / "web/ops/attitude_viewer.js"), str(SOURCE),
            str(ROOT / "web/assets/pado-sat-deployed-mesh.json"),
            str(ROOT / "web/assets/pado-sat-appearance.json")],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_overview_keeps_interactive_attitude_and_existing_tabs(self):
        markup = (ROOT / "web/ops/index.html").read_text(encoding="utf-8")
        parsed = SectionParser()
        parsed.feed(markup)
        for ident in ("equipment-overview", "equipment-cad-canvas", "attitude-canvas", "sensor-list",
                      "state-history-slider", "spacecraft-event-rows"):
            self.assertIn("ros-view-status", parsed.parents[ident])
        self.assertIn("ros-view-telemetry", parsed.parents["packet-record-rows"])
        self.assertIn("ros-view-tc", parsed.parents["sim-reservation-rows"])
        self.assertIn("부품 중심 기준·스택 임시 배치", markup)
        self.assertLess(markup.index("/assets/ros-attitude-viewer.js"), markup.index("/assets/ros-equipment-overview.js"))
        self.assertLess(markup.index("/assets/ros-equipment-overview.js"), markup.index("/assets/ros-workbench.js"))


if __name__ == "__main__":
    unittest.main()
