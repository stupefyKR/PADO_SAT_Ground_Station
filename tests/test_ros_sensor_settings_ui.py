"""The ROS settings panel validates and saves ranges with explicit conflicts."""

from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element {
  constructor(tag='div'){this.tag=tag;this.children=[];this.listeners={};this.value='';this.disabled=false;
    this.textContent='';this.attributes={};this.classList={toggle(){}};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  setAttribute(name,value){this.attributes[name]=value;}
  async fire(name){return this.listeners[name]?.({preventDefault(){}});}
}
const nodes=new Map();
const document={getElementById(id){if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);},
  createElement(tag){return new Element(tag);}};
const get=id=>document.getElementById(id);
const scope={window:{},document};vm.createContext(scope);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
let view={sha256:'a'.repeat(64),sensors:[{key:'EPS_P60.battery_voltage_v',id:'battery',name:'battery',
  group:'EPS',unit:'V',lower:11.8,upper:16}]};
let posts=[],fail=false,saved=0;
async function api(path,options){
  assert.equal(path,'/api/ros/settings');
  if(!options)return view;
  assert.equal(options.method,'POST');assert.equal(options.headers['X-CSRF-Token'],'csrf');
  const payload=JSON.parse(options.body);posts.push(payload);
  if(fail)throw new Error('settings_conflict');
  const limits=payload.limits['EPS_P60.battery_voltage_v'];
  view={...view,sha256:'b'.repeat(64),sensors:[{...view.sensors[0],...limits}]};
  return view;
}
const lower=()=>get('sensor-settings-rows').children[0].children[1].children[0];
const upper=()=>get('sensor-settings-rows').children[0].children[2].children[0];
async function run(){
  const controller=scope.window.rosSensorSettings.create({api,session:{role:'planner',csrf:'csrf'},
    onSaved(result){saved++;assert.equal(result.sensors[0].upper,15);}});
  await controller.refresh();assert.equal(lower().value,'11.8');assert.equal(get('sensor-settings-save').disabled,true);
  controller.updateMeasurements({sensors:[{id:'battery',samples:[{value:14,utc:'2026-10-29T00:00:00Z'}]}]});
  const row=get('sensor-settings-rows').children[0];
  assert.equal(row.children[4].textContent,'14 V');assert.equal(row.children[5].textContent,'2026-10-29T00:00:00Z');
  upper().value='15';await upper().fire('input');assert.equal(get('sensor-settings-save').disabled,false);
  controller.updateMeasurements({sensors:[]});assert.equal(upper().value,'15','TM polling must not overwrite unsaved form');
  await get('sensor-settings-form').fire('submit');assert.equal(posts.length,1);assert.equal(saved,1);
  assert.equal(posts[0].expected_sha256,'a'.repeat(64));
  assert.equal(Object.keys(posts[0]).sort().join(','),'expected_sha256,limits');
  assert.equal(get('sensor-settings-save').disabled,true);
  lower().value='20';await lower().fire('input');
  await get('sensor-settings-form').fire('submit');assert.equal(posts.length,1,'inverted ranges are rejected before request');
  lower().value='';await lower().fire('input');
  await get('sensor-settings-form').fire('submit');assert.equal(posts.length,1,'empty values are not zero');
  lower().value='12';await lower().fire('input');fail=true;
  await get('sensor-settings-form').fire('submit');assert.equal(posts.length,2);
  assert.match(get('sensor-settings-status').textContent,/다른 작업/);
  assert.equal(lower().value,'12','conflict preserves user edit until explicit reload');
  assert.equal(lower().disabled,true);assert.equal(get('sensor-settings-save').disabled,true);
  await get('sensor-settings-reload').fire('click');assert.equal(lower().value,'11.8');assert.equal(lower().disabled,false);
  nodes.clear();
  const auditor=scope.window.rosSensorSettings.create({api,session:{role:'auditor',csrf:'csrf'}});
  await auditor.refresh();assert.equal(lower().disabled,true);assert.equal(upper().disabled,true);
  await get('sensor-settings-form').fire('submit');assert.equal(posts.length,2,'auditor never writes');
  auditor.updateMeasurements({sensors:[{id:'battery',samples:[{value:99,utc:'now'}]}]},'manual');
  assert.equal(get('sensor-settings-rows').children[0].children[4].textContent,'수신값 없음');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
"""


class SensorSettingsUi(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"),"Node is required")
    def test_range_editor_conflicts_and_read_only_role(self):
        result=subprocess.run([shutil.which("node"),"-e",NODE_TEST,
            str(ROOT/"web/ops/sensor_settings.js")],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__ == "__main__":unittest.main()
