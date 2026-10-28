"""Sensor clocks, UTC axes and inspection retain real measurement times."""

from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "web/ops/workbench.js"

NODE_TEST = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element {
  constructor(tag='div') {this.tag=tag;this.children=[];this.listeners={};this.attributes={};this.value='';
    this.textContent='';this.className='';this.classList={add(){},toggle(){}};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(key,value){this.attributes[key]=String(value);}
  addEventListener(name,fn){(this.listeners[name]??=new Set()).add(fn);}
  removeEventListener(name,fn){this.listeners[name]?.delete(fn);}
  getBoundingClientRect(){return {left:100,width:Number(this.attributes.viewBox?.split(' ')[2]||300)};}
  fire(name,event={}){for(const fn of [...(this.listeners[name]||[])])fn({preventDefault(){},...event});}
}
const nodes=new Map();
const document={activeElement:null,getElementById(id){if(!nodes.has(id))nodes.set(id,new Element(id==='telemetry-chart'?'svg':'div'));return nodes.get(id);},
  createElement(tag){return new Element(tag);},createElementNS(_ns,tag){return new Element(tag);}};
const get=id=>document.getElementById(id);
const source=fs.readFileSync(process.argv[1],'utf8').replace(/\s+init\(\);\s*\}\)\(\);\s*$/,
  '\nwindow.timeTest={measurementUtc,nearestSampleIndex,sensorSparkline,renderSensors,renderTrend,setSnapshot(snapshot,range=5){selectedTm=snapshot;selectedSensor=snapshot.sensors[0];minutes=range;publishedReachable=true;}};\n})();');
const scope={document,window:{}};vm.createContext(scope);vm.runInContext(source,scope);
const ui=scope.window.timeTest;
const sensor={id:'voltage',name:'battery',group:'EPS',unit:'V',lower:0,upper:20,samples:[
  {utc:'2026-10-27T23:58:17Z',value:1},{utc:'2026-10-27T23:59:58Z',value:3},
  {utc:'2026-10-28T00:00:13Z',value:2}]};
const snapshot={received_utc:'2026-10-28T00:20:00Z',sensors:[sensor]};
const byClass=(node,name)=>node.children.find(child=>child.attributes?.class===name||child.className===name);
const layer=chart=>byClass(chart,'sample-inspection-layer');
const tooltip=chart=>layer(chart).children.find(child=>child.attributes.class==='sample-tooltip-time');
const value=chart=>layer(chart).children.find(child=>child.attributes.class==='sample-tooltip-value');
const pointX=(stamp,width,left,right,end,minutes=5)=>100+left+(Date.parse(stamp)-(end-minutes*60000))/(minutes*60000)*(right-left);

ui.setSnapshot(snapshot);ui.renderSensors();
const card=get('sensor-list').children[0].children[0];
const measured=card.children.find(child=>child.tag==='time');
assert.equal(measured.dateTime,'2026-10-28T00:00:13Z');
assert.equal(measured.textContent,'측정 2026-10-28 00:00:13 UTC');
assert.ok(!measured.textContent.includes('00:20:00'),'card clocks must not use the later downlink time');
assert.equal(get('selected-measured-time').textContent,'2026-10-28 00:00:13 UTC');
assert.match(get('selected-state').textContent,/과거 측정/);
const mini=card.children.find(child=>child.tag==='svg');
assert.deepEqual(mini.children.filter(child=>child.attributes.class==='sparkline-time-tick').map(child=>child.textContent),
  ['23:55:13','23:57:43','00:00:13']);
assert.equal(byClass(mini,'sparkline-time-context').textContent,'2026-10-27 → 2026-10-28 UTC');
const end=Date.parse(sensor.samples.at(-1).utc);
mini.fire('pointermove',{clientX:pointX('2026-10-27T23:59:00Z',300,5,295,end)});
assert.equal(tooltip(mini).textContent,'2026-10-27 23:58:17 UTC');
assert.equal(value(mini).textContent,'1 V','irregular samples are selected by actual UTC, not array spacing');
mini.fire('pointermove',{clientX:pointX('2026-10-27T23:59:50Z',300,5,295,end)});
assert.equal(tooltip(mini).textContent,'2026-10-27 23:59:58 UTC');
assert.equal(value(mini).textContent,'3 V');
mini.fire('pointerleave');assert.equal(layer(mini).attributes.visibility,'hidden');

const detail=get('telemetry-chart');
assert.ok(detail.children.filter(child=>child.attributes.class==='chart-time-tick').every(child=>/^\d\d:\d\d:\d\d$/.test(child.textContent)));
assert.match(get('chart-time-range').textContent,/2026-10-27 23:55:13 UTC → 2026-10-28 00:00:13 UTC/);
detail.fire('pointermove',{clientX:pointX('2026-10-27T23:59:50Z',800,40,770,end)});
assert.match(get('chart-inspection').textContent,/2026-10-27 23:59:58 UTC · 3 V · 표본 2\/3/);
document.activeElement=detail;detail.fire('focus');
detail.fire('keydown',{key:'Home'});
assert.equal(tooltip(detail).textContent,'2026-10-27 23:58:17 UTC');
detail.fire('keydown',{key:'ArrowRight'});
assert.equal(tooltip(detail).textContent,'2026-10-27 23:59:58 UTC');
detail.fire('keydown',{key:'End'});
assert.equal(tooltip(detail).textContent,'2026-10-28 00:00:13 UTC');
detail.fire('keydown',{key:'ArrowLeft'});
ui.renderTrend();
assert.match(get('chart-inspection').textContent,/23:59:58 UTC/,'refresh retains the inspected real sample');
assert.equal(detail.listeners.pointermove.size,1,'polling must replace old listeners');
assert.equal(detail.listeners.keydown.size,1);
detail.fire('keydown',{key:'Escape'});assert.equal(layer(detail).attributes.visibility,'hidden');
document.activeElement=null;detail.fire('blur');

const dense={...sensor,id:'dense',samples:Array.from({length:721},(_,i)=>({
  utc:new Date(end+(i-720)*5000).toISOString(),value:i}))};
ui.setSnapshot({received_utc:snapshot.received_utc,sensors:[dense]},60);
const denseMini=ui.sensorSparkline(dense);
const path=byClass(denseMini,'sparkline-value');
assert.ok(path.attributes.d.split(/[ML]/).filter(Boolean).length<=104);
const target=dense.samples[47];
denseMini.fire('pointermove',{clientX:pointX(target.utc,300,5,295,end,60)});
assert.equal(tooltip(denseMini).textContent,ui.measurementUtc(target.utc));
assert.equal(value(denseMini).textContent,'47 V','inspection uses all real samples even if drawing is decimated');

const only={...sensor,id:'one',samples:[sensor.samples[2]]};
ui.setSnapshot({received_utc:snapshot.received_utc,sensors:[only]});
const one=ui.sensorSparkline(only);one.fire('pointermove',{clientX:100});
assert.equal(tooltip(one).textContent,'2026-10-28 00:00:13 UTC');
assert.equal(layer(one).children.find(child=>child.attributes.class==='sample-hover-dot').attributes.cx,'295',
  'a single sample marker remains at its actual time rather than the pointer');
process.env.TZ='Pacific/Honolulu';
assert.equal(ui.measurementUtc('2026-10-28T00:00:13Z'),'2026-10-28 00:00:13 UTC');
ui.setSnapshot(snapshot);ui.renderSensors();
get('sensor-filter').value='no-such-sensor';ui.renderSensors();
assert.equal(get('selected-sensor').textContent,'선택 안 됨');
assert.equal(get('selected-measured-time').textContent,'—');
assert.equal(get('latest-value').textContent,'—');
assert.equal(get('chart-inspection').textContent,'선택한 센서가 없습니다');
assert.match(get('chart-time-range').textContent,/센서 선택 대기/);
assert.equal(detail.children.length,1,'filtering away every sensor clears the old graph');
assert.equal(detail.listeners.pointermove.size,0);
assert.equal(detail.listeners.keydown.size,0,'old sample inspection is detached when selection disappears');
"""


class _ChartMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chart = None
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.add(attributes["id"])
        if attributes.get("id") == "telemetry-chart":
            self.chart = attributes


class RosSensorTimeUiTests(unittest.TestCase):
    def test_detailed_chart_has_keyboard_focus_and_measurement_context(self):
        markup = _ChartMarkup()
        markup.feed((ROOT / "web/ops/index.html").read_text(encoding="utf-8"))
        self.assertEqual(markup.chart["tabindex"], "0")
        for ident in ("selected-measured-time", "chart-time-range", "chart-inspection", "chart-inspection-help"):
            self.assertIn(ident, markup.ids)
        self.assertIn("chart-inspection-help", markup.chart["aria-describedby"])

    @unittest.skipUnless(shutil.which("node"), "Node.js required for browser logic")
    def test_actual_utc_axes_card_hover_sparse_rollover_and_keyboard_samples(self):
        result = subprocess.run([shutil.which("node"), "-e", NODE_TEST, str(SOURCE)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
