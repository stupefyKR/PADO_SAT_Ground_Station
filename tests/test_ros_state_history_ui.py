"""Backfill updates measured history; held CAD poses never claim TM coverage."""

from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "web/ops/state_history.js"

NODE_TEST = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context={};vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
const {Cache,Controller,WINDOW_MS}=context.rosStateHistory;
const base=Date.parse('2026-10-28T23:59:55Z'),iso=ms=>new Date(ms).toISOString();
const position=(id,time,received=base+60000)=>({id,utc:iso(time),received_utc:iso(received),norad_id:99999,
  position_itrf_m:[6878137,(time-base)*7.5,0],velocity_itrf_mps:[0,7500,0],valid:true});
const attitude=(id,time,q=[1,0,0,0],received=base+60000)=>({id,utc:iso(time),received_utc:iso(received),
  quaternion_wxyz:q,frame:'SIM_BODY_TO_ITRF',valid:true});
const page=(positions,attitudes,next,revision=next,more=false)=>({schema:'pado.state-history.v1',
  state:'AVAILABLE',positions,attitudes,next_after_id:next,revision,has_more:more});
const orbit=(rows,revision=1)=>({schema:'pado.orbit-history.v1',norad_id:99999,revision,sample_step_s:60,
  samples:rows.map(([time,xyz=[6878137,0,0],epoch=base-60000])=>[iso(time),...xyz,1,0,0,'TLE',iso(epoch)])});
const cache=new Cache();cache.setOrbit(orbit([[base-60000],[base],[base+60000],[base+120000]]));
cache.mergeTm(page([position(1,base),position(3,base+5000)],
  [attitude(2,base),attitude(4,base+5000,[Math.SQRT1_2,0,0,Math.SQRT1_2])],4));
let state=cache.resolve(base+2500);
assert.equal(state.positionSource,'GNSS_INTERPOLATED');assert.equal(state.attitudeStatus,'INTERPOLATED');
assert.ok(Math.abs(state.attitude.quaternion_wxyz[0]-Math.cos(Math.PI/8))<1e-9,'SLERP measured quaternions');
assert.equal(cache.resolve(base+5000).attitudeStatus,'MEASURED');
assert.equal(cache.resolve(base+5001).attitudeStatus,'UNKNOWN','history never extrapolates latest attitude');
assert.equal(cache.resolve(base+10000,{live:true}).attitudeStatus,'RECENT_SAMPLE');
assert.equal(cache.resolve(base+16000,{live:true}).attitudeStatus,'UNKNOWN','live recent sample expires in10s');
assert.equal(cache.resolve(base+20000).positionSource,'FDS_TLE','FDS position survives an attitude gap');
assert.equal(cache.resolve(base+20000).attitude,null);
state=cache.resolve(base+20000);
assert.equal(state.displayAttitudeStatus,'LAST_KNOWN');
assert.equal(state.displayAttitude.utc,iso(base+5000));
assert.equal(state.displayAttitude.received_utc,iso(base+60000));
assert.equal(state.displayAttitude.quaternion_wxyz[3],Math.SQRT1_2);
assert.equal(cache.resolve(base-1).displayAttitude,null,'a future TM cannot become an earlier held pose');
assert.equal(cache.resolve(base-1).displayAttitudeStatus,'UNKNOWN');
assert.equal(cache.resolve(base+5000).displayAttitudeStatus,'MEASURED');
assert.equal(cache.resolve(base+2500).displayAttitudeStatus,'INTERPOLATED');
assert.equal(cache.resolve(base+10000,{live:true}).displayAttitudeStatus,'RECENT_SAMPLE');
assert.equal(cache.resolve(base+16000,{live:true}).displayAttitudeStatus,'LAST_KNOWN');
assert.equal(cache.coverage()[0][1],base+5000,'holding a pose must not extend measured coverage');
assert.ok(Math.abs(context.rosStateHistory.slerp([1,0,0,0],[-1,0,0,0],.5)[0]-1)<1e-12,
  'equivalent quaternion signs do not spin through a zero quaternion');

// Receipt IDs increase while measurement time goes backwards: backfill an old gap.
cache.mergeTm(page([], [attitude(10,base+20000,[0,1,0,0],base+3600000)],10));
state=cache.resolve(base+20000);assert.equal(state.attitudeStatus,'MEASURED');
assert.equal(state.attitude.quaternion_wxyz[1],1);assert.equal(state.attitude.received_utc,iso(base+3600000));
assert.equal(state.displayAttitudeStatus,'MEASURED','backfill replaces a held pose at its measurement time');
state=cache.resolve(base+20001);
assert.equal(state.attitudeStatus,'UNKNOWN');
assert.equal(state.displayAttitudeStatus,'LAST_KNOWN');
assert.equal(state.displayAttitude.quaternion_wxyz[1],1,'newly received old TM also updates subsequent held display');
assert.equal(cache.resolve(base+15000).displayAttitude.utc,iso(base+5000),'later TM never leaks into a prior gap');
cache.mergeTm(page([], [attitude(11,base+25000,[0,Math.SQRT1_2,Math.SQRT1_2,0])],11));
assert.equal(cache.resolve(base+22500).attitudeStatus,'INTERPOLATED');
cache.mergeTm(page([], [{...attitude(12,base+22000),valid:false,reason:'invalid_quaternion',quaternion_wxyz:null}],12));
assert.equal(cache.resolve(base+21000).attitudeStatus,'UNKNOWN','invalid records break interpolation');
assert.equal(cache.resolve(base+22000).attitudeReason,'invalid_quaternion');
assert.equal(cache.resolve(base+23000).attitudeStatus,'UNKNOWN');
assert.equal(cache.resolve(base+23000).displayAttitude.utc,iso(base+20000),'invalid TM cannot replace the last valid display pose');
assert.equal(cache.coverage().length,3,'invalid sample splits green coverage');

// A later correction at the same measurement timestamp wins by receipt ID.
const before=cache.mutationVersion;
cache.mergeTm(page([], [attitude(13,base+22000,[0,0,1,0])],13));
assert.equal(cache.resolve(base+22000).attitudeStatus,'MEASURED');assert.ok(cache.mutationVersion>before);
cache.mergeTm(page([], [{...attitude(1,base+22000),frame:'UNDECLARED_FRAME'}],13));
assert.equal(cache.resolve(base+22000).attitude.quaternion_wxyz[2],1,'old id never overwrites a newer correction');
const old=base-WINDOW_MS-1000;
cache.mergeTm(page([], [attitude(14,old,[1,0,0,0],base+60000)],14));cache.prune(base+60000);
assert.equal(cache.attitudes.has(old),false,'rolling72h bound');
const expired=new Cache();expired.mergeTm(page([], [attitude(1,old)],1));expired.prune(base+60000);
assert.equal(expired.resolve(base).displayAttitude,null,'a held pose is discarded with the 72h TM history');

const jump=new Cache();jump.setOrbit(orbit([[base,[6878137,0,0]],[base+60000,[-6878137,0,0]]]));
assert.equal(jump.resolve(base+30000).position_itrf_m,null,'no interpolation through a scenario teleport');
const futureEpoch=new Cache();futureEpoch.setOrbit(orbit([[base,[6878137,0,0],base],[base+60000,[6878137,450000,0],base+60000]]));
assert.equal(futureEpoch.resolve(base+30000).position_itrf_m,null,'future input epoch cannot fill an earlier state');
const reset=cache.mergeTm({schema:'pado.state-history.v1',state:'RESET_REQUIRED',next_after_id:0});
assert.equal(reset.reset,true);assert.equal(cache.cursor,0);assert.equal(cache.attitudes.size,0);
assert.equal(cache.resolve(base+30000).displayAttitude,null,'archive reset clears held display poses too');

(async()=>{
  let now=base+60000,resolveFirst;const calls=[],states=[];
  const first=new Promise(resolve=>resolveFirst=resolve);
  let tmCall=0;
  const api=async url=>{
    calls.push(url);
    if(url.startsWith('/api/fds'))return orbit([[base-60000],[base],[base+60000],[base+120000]]);
    tmCall++;
    if(tmCall===1)return first;
    if(tmCall===2)return page([], [attitude(2,base+20000,[1,0,0,0])],2,2,false);
    if(tmCall===3)return page([], [attitude(3,base+20000,[0,0,0,1],base+3600000)],3,3,false);
    return page([],[],3,3,false);
  };
  const controller=new Controller({api,now:()=>now,onChange:state=>states.push(state)});
  controller.seek(base+20000);
  const pending=controller.refresh();controller.refresh();
  assert.equal(tmCall,1,'overlapping refreshes share one request stream');
  resolveFirst(page([position(1,base+20000)],[],1,2,true));await pending;
  assert.equal(controller.cursor,base+20000);assert.equal(controller.live,false);
  assert.ok(calls.some(url=>url.includes('after_id=1')),'paged cursor advances by receipt id');
  assert.equal(controller.state().attitudeStatus,'MEASURED');
  const version=controller.cache.mutationVersion;await controller.refresh();
  assert.equal(controller.cursor,base+20000,'late TM refresh never jumps the selected past cursor');
  assert.equal(controller.state().attitude.quaternion_wxyz[3],1);
  assert.ok(controller.cache.mutationVersion>version,'same window revision update repaints coverage');
  controller.setSpeed(10);controller.togglePlay();now+=1000;controller.tick();
  assert.equal(controller.cursor,base+30000,'playback advances selectedtime at requested speed');
  controller.togglePlay();now+=1000;controller.tick();assert.equal(controller.cursor,base+30000,'pause preserves cursor');
  controller.goLive();now+=1000;controller.tick();assert.equal(controller.cursor,now);
  controller.seek(base-WINDOW_MS-1);assert.equal(controller.cursor,now-WINDOW_MS,'seek is bounded');
  assert.ok(states.some(state=>state.attitudeStatus==='UNKNOWN'));
  controller.seek(base+15000);controller.stepSample(1);assert.equal(controller.cursor,base+20000,'jump to an actual attitude sample');
  let resetCalls=0;
  const resetting=new Controller({now:()=>now,api:async url=>{
    if(url.startsWith('/api/fds'))return orbit([]);
    if(++resetCalls===1)return {schema:'pado.state-history.v1',state:'RESET_REQUIRED',next_after_id:0};
    return page([], [attitude(1,base+20000)],1,1,false);
  }});
  resetting.cache.mergeTm(page([], [attitude(20,base)],20));
  await resetting.refresh();
  assert.equal(resetCalls,2,'an archive reset restarts pagination from0 once');
  assert.equal(resetting.cache.cursor,1);assert.equal(resetting.cache.attitudes.has(base),false);
  assert.equal(resetting.cache.resolve(base+20000).attitudeStatus,'MEASURED');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""

WORKBENCH_TEST = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element {
  constructor(){this.value='';this.textContent='';this.listeners={};this.width=760;this.height=16;}
  setAttribute(name,value){this[name]=value;}
  addEventListener(type,fn){this.listeners[type]=fn;}
  fire(type){this.listeners[type]?.({target:this});}
  getContext(){return {fillStyle:'',fillRect(){}};}
}
const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
const frames=[];
const scope={window:{rosAttitudeMath:{computeState:state=>({...state,
  orientationKnown:!!state.attitude,displayOrientationKnown:!!(state.attitude||state.displayAttitude),
  orientationHeld:!state.attitude&&state.displayAttitudeStatus==='LAST_KNOWN'})}},
  document:{getElementById:get},api:async()=>{throw Error('offline');}};
vm.createContext(scope);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
scope.window.rosStateHistory=scope.rosStateHistory;
const original=fs.readFileSync(process.argv[2],'utf8');
const source=original.replace(/\s+init\(\);\s*\}\)\(\);\s*$/,
  '\nwindow.historyTest={setupStateHistory,renderAttitude,controller:()=>historyController,setViewer(v){attitudeViewer=v;},setSource(s){tmSource=s;}};\n})();');
vm.runInContext(source,scope);
const ui=scope.window.historyTest;ui.setViewer({setDirections:value=>frames.push(value)});ui.setupStateHistory();
const control=ui.controller(),base=Date.parse('2026-10-28T23:59:55Z');
control.clock=()=>base+60000;control.offset=0;control.goLive();
const stale={utc:new Date(base).toISOString(),frame:'SIM_BODY_TO_ITRF_NADIR',quaternion_wxyz:[1,0,0,0]};
ui.renderAttitude(stale);
assert.equal(frames.at(-1).orientationKnown,false,'a stale snapshot cannot synthesize a measured nadir pose');
assert.equal(frames.at(-1).displayOrientationKnown,false,'without previous history the CAD has a neutral reference pose');
assert.equal(get('attitude-source').textContent,'자세 정보 없음 · 기준 형상 표시');
get('state-history-slider').value=String((base+20000)/1000);get('state-history-slider').fire('input');
assert.equal(control.live,false);assert.equal(control.cursor,base+20000);
const page={schema:'pado.state-history.v1',revision:2,next_after_id:2,has_more:false,
  positions:[{id:1,utc:new Date(base+20000).toISOString(),received_utc:new Date(base+60000).toISOString(),
    norad_id:99999,position_itrf_m:[6878137,150000,0],velocity_itrf_mps:[0,7500,0],valid:true}],
  attitudes:[{id:2,utc:new Date(base+20000).toISOString(),received_utc:new Date(base+60000).toISOString(),
    quaternion_wxyz:[0,0,0,1],frame:'SIM_BODY_TO_ITRF',valid:true}]};
control.cache.mergeTm(page);control.emit();
assert.equal(frames.at(-1).orientationKnown,true,'late recorded attitude fills the already selected past cursor');
assert.equal(get('attitude-source').textContent,'수신 자세 TM');
assert.equal(get('attitude-quaternion').textContent,'0.0000, 0.0000, 0.0000, 1.0000');
assert.match(get('attitude-time').textContent,/2026-10-29 00:00:15 UTC/);
assert.match(get('attitude-received-time').textContent,/2026-10-29 00:00:55 UTC/);
assert.equal(control.cursor,base+20000);
ui.renderAttitude(stale);assert.equal(frames.at(-1).orientationKnown,true,'normal snapshot refresh keeps the selected history state');
get('state-history-speed').value='300';get('state-history-speed').fire('change');assert.equal(control.speed,300);
get('state-history-play').fire('click');assert.equal(control.playing,true);
get('state-history-play').fire('click');assert.equal(control.playing,false);
get('state-history-live').fire('click');assert.equal(control.live,true);
assert.equal(frames.at(-1).orientationKnown,false,'holding a historical pose never claims current attitude knowledge');
assert.equal(frames.at(-1).displayOrientationKnown,true);
assert.equal(frames.at(-1).orientationHeld,true);
assert.equal(get('attitude-source').textContent,'자세 정보 없음 · 마지막 수신 자세 유지');
assert.equal(get('attitude-quaternion').textContent,'0.0000, 0.0000, 0.0000, 1.0000');
assert.match(get('attitude-time').textContent,/2026-10-29 00:00:15 UTC.*마지막 자세 측정/);
assert.equal(get('state-history-live')['aria-pressed'],'true');
assert.equal(get('state-history-previous').disabled,false);
get('state-history-previous').fire('click');
assert.equal(control.cursor,base+20000);assert.equal(control.live,false);
assert.equal(frames.at(-1).orientationKnown,true,'previous TM button jumps to its precise measurement timestamp');
assert.equal(get('state-history-next').disabled,true);
control.seek(base+15000);
assert.equal(frames.at(-1).orientationKnown,false);
assert.equal(frames.at(-1).displayOrientationKnown,false,'future received attitude is not held at an earlier selected time');
assert.equal(get('attitude-source').textContent,'자세 정보 없음 · 기준 형상 표시');
assert.equal(get('attitude-quaternion').textContent,'—');
"""


class _Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = {}
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids[attributes["id"]] = attributes
        if tag == "script":
            self.scripts.append(attributes.get("src"))


class RosStateHistoryUiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js required for replay controller")
    def test_measurement_time_gaps_backfill_quaternions_and_playback_controller(self):
        result = subprocess.run([shutil.which("node"), "-e", NODE_TEST, str(SOURCE)],
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_history_controls_and_script_order_are_available(self):
        markup = _Markup()
        markup.feed((ROOT / "web/ops/index.html").read_text(encoding="utf-8"))
        for ident in ("state-history-slider", "state-history-live", "state-history-play", "state-history-speed",
                      "state-history-selected", "state-history-position", "attitude-received-time"):
            self.assertIn(ident, markup.ids)
        self.assertEqual(markup.ids["state-history-slider"]["type"], "range")
        self.assertLess(markup.scripts.index("/assets/ros-state-history.js"),
                        markup.scripts.index("/assets/ros-workbench.js"))
        source = (ROOT / "web/ops/workbench.js").read_text(encoding="utf-8")
        self.assertNotIn("predictNadir", source)

    @unittest.skipUnless(shutil.which("node"), "Node.js required for replay controls")
    def test_ros_controls_preserve_selected_history_and_label_held_unknown_orientation(self):
        result = subprocess.run([shutil.which("node"), "-e", WORKBENCH_TEST, str(SOURCE),
                                 str(ROOT / "web/ops/workbench.js")],
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
