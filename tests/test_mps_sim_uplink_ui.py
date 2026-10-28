"""A saved hk_dump plan must be transferable through the MPS browser controls."""

from pathlib import Path
import shutil
import subprocess
import unittest


LIVE = Path(__file__).resolve().parents[1] / "web/mps/live.js"

NODE_TEST = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag='div') { this.tag=tag; this.children=[]; this.listeners={};
    this.disabled=false; this.value=''; this.dataset={}; this._text=''; }
  set textContent(value) { this._text=String(value); this.children=[]; }
  get textContent() { return this._text; }
  set innerHTML(value) { this.children=[new Element('tbody')]; }
  append(...nodes) { this.children.push(...nodes); }
  add(node) { this.append(node); }
  replaceChildren(...nodes) { this.children=nodes; this._text=''; }
  setAttribute() {}
  addEventListener(type, handler) { this.listeners[type]=handler; }
  async fire(type) { if (!this.disabled) await this.listeners[type]?.(); }
  querySelectorAll(tag) {
    return this.children.filter(x=>x instanceof Element).flatMap(x=>[
      ...(x.tag===tag ? [x] : []), ...x.querySelectorAll(tag)]);
  }
  querySelector(tag) { return this.querySelectorAll(tag)[0] ?? null; }
}
const scroll=new Element(), actions=new Element(), tag=new Element(), panel=new Element();
panel.querySelector=selector=>({'.table-scroll':scroll,'.panel-actions':actions,'.panel-head .tag':tag})[selector];
const nodes=new Map();
const document={querySelector:selector=>selector==='.draft-panel' ? panel : null,
  createElement:name=>new Element(name),
  getElementById:id=>{ if(!nodes.has(id)) nodes.set(id,new Element()); return nodes.get(id); }};
const commands=[{name:'hk_dump',group:'CDHS',fields_defined:true,
  field_types:{start_utc_ms:'u64',end_utc_ms:'u64'}},
  {name:'hk_get',group:'CDHS',fields_defined:true,field_types:{}},
  {name:'mm_get_mode',group:'CDHS',fields_defined:true,field_types:{}},
  {name:'mm_set_mode',group:'CDHS',fields_defined:true,field_types:{mode:'u8'}}];
const calls=[];
let revision=0;
const context={document,structuredClone,mpsDraftCommands:commands,value(){},
  showError(message){throw Error(message);},
  Option:function(text,value){const e=new Element('option');e.textContent=text;e.value=value;return e;},
  api:async(path,options)=>{
    const body=JSON.parse(options.body); calls.push({path,body});
    if(path==='/api/mps/test-sequences') return {...body,revision:++revision};
    if(path==='/api/mps/test-sequences/handoff') return {};
    throw Error(path);
  }};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
context.mpsDrawPass=()=>{};
context.mpsRenderN10Feedback=()=>{};
const now=Math.floor(Date.now()/1000);
const pass={pass_id:'yonsei-001',station_id:'yonsei',band:'UHF',aos_utc_s:now+60,los_utc_s:now+660};
const schedule={schedule_sha256:'a'.repeat(64)};
const session={role:'planner',csrf:'test-csrf'};

(async()=>{
  context.mpsRenderSequenceEditor(pass,schedule,session,[]);
  const form=scroll.children[0], select=form.children[0], fields=form.children[1], add=form.children[2];
  const table=scroll.children[1], timing=scroll.children[2], offset=timing.children[0].children[0];
  const timingUtc=timing.children[1];
  assert.equal(form.querySelectorAll('input').length,0,'command composer has no per-TC time field');
  assert.equal(offset.value,'10','shared default acquisition margin is independent of selected command');
  select.value='hk_dump'; await select.fire('change');
  assert.deepEqual(fields.querySelectorAll('input').map(x=>x.value),['0','0']);
  assert.match(fields.children.at(-1).textContent,/저장 TM 전체/);
  await add.fire('click');
  select.value='hk_get'; await select.fire('change'); await add.fire('click');
  select.value='mm_get_mode'; await select.fire('change'); await add.fire('click');
  const body=table.querySelector('tbody');
  assert.equal(body.children.length,3);
  await body.children[1].children.at(-1).children[0].fire('click');
  offset.value='25'; await offset.fire('input');
  assert.match(timingUtc.textContent,/AOS 25초 후/);
  assert.ok(timingUtc.textContent.includes(new Date((pass.aos_utc_s+25)*1000).toISOString().replace('.000Z','Z')),
    'UTC preview reflects the shared AOS-relative start immediately');
  let save=actions.children[0], handoff=actions.children[1];
  assert.equal(handoff.disabled,true,'unsaved plan cannot be sent');
  await save.fire('click');
  assert.deepEqual(calls[0].body.steps.map(x=>x.command_name),['hk_get','hk_dump','mm_get_mode']);
  assert.deepEqual(calls[0].body.steps.map(x=>x.offset_s),[25,25,25],
    'one shared time is serialized for every TC without changing command order');
  assert.deepEqual(calls[0].body.steps[1].parameters,{start_utc_ms:0,end_utc_ms:0});
  assert.equal(handoff.disabled,false,'saved common start enables ROS handoff');
  offset.value='26'; await offset.fire('input');
  assert.equal(handoff.disabled,true,'changed start requires save');
  offset.value='25'; await offset.fire('input');
  await handoff.fire('click');
  assert.equal(calls[1].path,'/api/mps/test-sequences/handoff');
  assert.deepEqual(calls[1].body,{pass_id:'yonsei-001',schedule_sha256:'a'.repeat(64),expected_revision:1});
  for(const invalid of ['', '-1', '1.5', String(pass.los_utc_s-pass.aos_utc_s)]) {
    offset.value=invalid; await offset.fire('input');
    assert.equal(save.disabled,true,'invalid common offset cannot be saved');
    assert.equal(handoff.disabled,true);
  }
  offset.value='25'; await offset.fire('input');
  select.value='mm_set_mode'; await select.fire('change');
  fields.querySelectorAll('input')[0].value='1'; await add.fire('click');
  await save.fire('click');
  assert.equal(handoff.disabled,true,'non-simulator commands still block handoff');

  const saved=[{pass_id:pass.pass_id,revision:1,steps:[{offset_s:10,command_name:'hk_dump',
    parameters:{start_utc_ms:0,end_utc_ms:0}},{offset_s:20,command_name:'hk_get',parameters:{}}]}];
  const before=calls.length;
  context.mpsRenderSequenceEditor(pass,schedule,session,saved);
  assert.equal(actions.children[1].disabled,true,'legacy mixed offsets require explicit resave');
  assert.match(scroll.children[3].textContent,/기존 초안/);
  assert.equal(calls.length,before,'opening an old draft must not mutate it');
  assert.deepEqual(saved[0].steps.map(x=>x.offset_s),[10,20]);
  await actions.children[0].fire('click');
  assert.deepEqual(calls.at(-1).body.steps.map(x=>x.offset_s),[10,10]);
  assert.equal(actions.children[1].disabled,false);
  context.mpsRenderSequenceEditor(pass,schedule,{role:'auditor',csrf:'x'},saved);
  assert.equal(actions.children[0].disabled,true);
  assert.equal(actions.children[1].disabled,true);
  context.mpsRenderSequenceEditor({...pass,station_id:'kari',band:'S_BAND'},schedule,session,[]);
  assert.equal(actions.children.length,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


class MpsSimUplinkUiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js required for browser logic test")
    def test_hk_dump_editor_save_handoff_and_role_guards(self):
        result = subprocess.run([shutil.which("node"), "-e", NODE_TEST, str(LIVE)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
