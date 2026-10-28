"""ROS browser controls reserve an ordered sequence with one shared start."""

from pathlib import Path
import shutil
import subprocess
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "web/ops/workbench.js"

NODE_TEST = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element {
  constructor(tag='div') {this.tag=tag;this.children=[];this.listeners={};this.value='';
    this.disabled=false;this.textContent='';this.classList={toggle(){}};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  add(node){this.append(node);}
  setAttribute(){}
  addEventListener(type,fn){this.listeners[type]=fn;}
  async fire(type){if(!this.disabled) return this.listeners[type]?.();}
}
const nodes=new Map();
const document={getElementById(id){if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);},
  createElement(tag){return new Element(tag);}};
const now=Math.floor(Date.now()/1000),sha='a'.repeat(64);
const sequence={sha256:sha,pass_id:'yonsei-001',aos_utc_s:now+60,los_utc_s:now+660,
  steps:['mm_get_mode','eps_get_hk','hk_dump'].map((name,index)=>({step_id:index+1,offset_s:25,
    command_name:name,parameters:name==='hk_dump'?{start_utc_ms:0,end_utc_ms:0}:{}}))};
let reservations=[];const calls=[];
const api=async(path,options)=>{
  if(!options) return {items:path.endsWith('sequences')?[sequence]:reservations};
  const body=JSON.parse(options.body);calls.push({path,body,headers:options.headers});
  if(path.endsWith('/cancel')){
    reservations=reservations.map(row=>({...row,status:'CANCELLED'}));return {status:'CANCELLED',count:3};
  }
  reservations=sequence.steps.map(step=>({id:step.step_id,sha256:sha,step_id:step.step_id,
    command_name:step.command_name,target_utc_s:sequence.aos_utc_s+25,start_offset_s:25,
    batch_id:17,batch_size:3,status:'SCHEDULED',ack:null})).reverse();
  return {batch_id:17,sha256:sha,target_utc_s:sequence.aos_utc_s+25,count:3,reservations};
};
const original=fs.readFileSync(process.argv[1],'utf8');
const source=original.replace(/\s+init\(\);\s*\}\)\(\);\s*$/,
  '\nwindow.test={showSimSequence,showSimReservations,setData(s,q,r){session=s;simSequences=q;simReservations=r;}};\n})();');
const scope={document,window:{},api,Option:function(text,value){const e=new Element('option');e.textContent=text;e.value=value;return e;}};
vm.createContext(scope);vm.runInContext(source,scope);
const ui=scope.window.test,session={role:'planner',csrf:'unit-csrf'};
const get=id=>document.getElementById(id);
(async()=>{
  ui.setData(session,[sequence],[]);get('sim-sequence-select').value=sha;
  ui.showSimSequence();
  assert.equal(get('sim-sequence-steps').children.length,3);
  assert.ok(get('sim-sequence-steps').children.every(row=>row.children.length===3),
    'TC rows contain order, command and arguments without individual reservation buttons');
  assert.match(get('sim-sequence-timing').textContent,/AOS 25초 후/);
  assert.ok(get('sim-sequence-timing').textContent.includes(
    new Date((sequence.aos_utc_s+25)*1000).toISOString().replace('.000Z','Z')));
  const reserve=get('sim-sequence-controls').children[0];
  assert.equal(reserve.textContent,'시퀀스 전체 예약');
  await reserve.fire('click');
  assert.equal(calls.length,1);
  assert.deepEqual(calls[0].body,{sha256:sha});
  assert.equal(calls[0].headers['X-CSRF-Token'],'unit-csrf');
  assert.equal(get('sim-sequence-controls').children[0].disabled,true);
  const rows=get('sim-reservation-rows').children;
  assert.deepEqual(rows.map(row=>row.children[1].textContent),
    ['1/3 · mm_get_mode','2/3 · eps_get_hk','3/3 · hk_dump']);
  assert.ok(rows.every(row=>row.children[2].textContent.includes('AOS 25초 후')));
  const cancellations=rows.flatMap(row=>row.children[4].children);
  assert.equal(cancellations.length,1,'one cancellation covers all pending sequence steps');
  await cancellations[0].fire('click');
  assert.deepEqual(calls[1].body,{batch_id:17});

  ui.setData({role:'auditor'},[sequence],reservations.map(row=>({...row,status:'SCHEDULED'})));
  ui.showSimSequence();ui.showSimReservations();
  assert.equal(get('sim-sequence-controls').children[0].disabled,true);
  assert.ok(get('sim-reservation-rows').children.every(row=>!row.children[4].children.length));
  const mixed={...sequence,steps:sequence.steps.map((step,i)=>({...step,offset_s:i*10}))};
  ui.setData(session,[mixed],[]);ui.showSimSequence();
  assert.equal(get('sim-sequence-controls').children[0].disabled,true);
  assert.match(get('sim-sequence-timing').textContent,/기존 초안/);
  const legacy={id:90,sha256:sha,step_id:1,command_name:'hk_get',target_utc_s:now+100,status:'SCHEDULED'};
  ui.setData(session,[sequence],[legacy]);ui.showSimReservations();
  await get('sim-reservation-rows').children[0].children[4].children[0].fire('click');
  assert.deepEqual(calls.at(-1).body,{id:90},'old reservations retain legacy cancellation compatibility');
  ui.setData(session,[],[]);ui.showSimSequence();
  assert.equal(get('sim-sequence-controls').children.length,0);
  assert.equal(get('sim-sequence-steps').children[0].children[0].colSpan,3);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


class TcBatchUiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js required for browser logic")
    def test_group_reservation_order_utc_cancel_and_legacy_guards(self):
        result = subprocess.run([shutil.which("node"), "-e", NODE_TEST, str(SOURCE)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
