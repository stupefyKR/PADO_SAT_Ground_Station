"""ROS overview keeps engineering context together and inspects exact PDUs."""

from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import build_stage9_preview


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "web/ops/index.html"
PACKET_BROWSER = ROOT / "web/ops/packet_browser.js"
WORKBENCH = ROOT / "web/ops/workbench.js"


class _Sections(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sections = []
        self.ids = []
        self.parents = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section":
            self.sections.append(attrs.get("id"))
        ident = attrs.get("id")
        if ident:
            self.ids.append(ident)
            self.parents[ident] = set(self.sections)

    def handle_endtag(self, tag):
        if tag == "section":
            self.sections.pop()


BROWSER_TEST = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag='div') { this.tag=tag; this.children=[]; this.listeners={};
    this.textContent=''; this.value=''; this.hidden=false; this.disabled=false;
    this.attributes={}; this.classList={toggle(){}}; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children=items; }
  setAttribute(key,value) { this.attributes[key]=value; }
  addEventListener(name,fn) { this.listeners[name]=fn; }
  async fire(name) { if (!this.disabled) return this.listeners[name]?.({preventDefault(){}}); }
  set innerHTML(_value) { throw Error('archive content must never enter innerHTML'); }
}
const nodes=new Map();
const document={getElementById(id) {if(!nodes.has(id)) nodes.set(id,new Element());return nodes.get(id);},
  createElement(tag) {return new Element(tag);}};
const calls=[];
let resolveSlow;
function detail(id) { return {id,label:`TM${String(id).padStart(4,'0')}`,name:'GNSS_PVT',
  packet_id:8225,sequence:id,sampled_utc:'2026-10-27T04:00:00Z',
  received_utc:'2026-10-27T04:00:01Z',size_bytes:4,raw_hex:'505300ff',
  header:{kind:'TM',packet_id:8225},fields:{x_m:6878000,unsafe:'<img src=x onerror=alert(1)>'},
  full_hk:id===10 ? {state:'complete',parameters:{nested:{voltage:5.0}}} : null}; }
const api=async(path)=>{
  calls.push(path);
  if(path.startsWith('/api/ros/received-tm?')) return {items:[detail(10),detail(9)],
    next_before_id:9,total:10,operational:false};
  if(path==='/api/ros/received-tm/11') return new Promise(resolve=>{resolveSlow=resolve;});
  const id=Number(path.split('/').at(-1));
  return detail(id);
};
const scope={window:{},document,URLSearchParams};
vm.createContext(scope);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
(async()=>{
  const ui=scope.window.rosPacketBrowser.create({api});
  await ui.refresh();
  assert.equal(calls[0],'/api/ros/received-tm?limit=50');
  assert.match(nodes.get('packet-detail-title').textContent,/TM0010.*GNSS_PVT/);
  assert.match(nodes.get('packet-raw-hex').textContent,/0000  50 53 00 ff/);
  assert.equal(nodes.get('packet-field-rows').children[1].children[1].textContent,
    '<img src=x onerror=alert(1)>');
  assert.equal(nodes.get('packet-full-hk-detail').hidden,false);
  assert.match(nodes.get('packet-full-hk-json').textContent,/voltage/);
  await nodes.get('packet-tab-raw').fire('click');
  assert.equal(nodes.get('packet-detail-raw').hidden,false);
  assert.equal(nodes.get('packet-detail-parsed').hidden,true);
  document.getElementById('packet-id-filter').value='TM0009';
  await nodes.get('packet-open-id').fire('click');
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(nodes.get('packet-detail-title').textContent,/TM0009/);
  assert.equal(nodes.get('packet-full-hk-detail').hidden,true);
  document.getElementById('packet-name-filter').value='MODE_BEACON';
  await nodes.get('packet-filter-form').fire('submit');
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(calls.includes('/api/ros/received-tm?limit=50&name=MODE_BEACON'));
  await nodes.get('packet-older').fire('click');
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(calls.includes('/api/ros/received-tm?limit=50&before_id=9&name=MODE_BEACON'));
  const slow=ui.select(11);
  await ui.select(12);
  resolveSlow(detail(11)); await slow;
  assert.match(nodes.get('packet-detail-title').textContent,/TM0012/,'stale response cannot replace selection');
  const count=calls.length;
  document.getElementById('packet-id-filter').value='../../tokens';
  await nodes.get('packet-open-id').fire('click');
  assert.equal(calls.length,count);
  assert.match(nodes.get('packet-browser-state').textContent,/번호/);
  assert.throws(()=>scope.window.rosPacketBrowser.hexDump('zz'),/HEX/);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""

SPARKLINE_TEST = r"""
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const document={createElementNS(_ns,tag) {return {tag,attributes:{},children:[],textContent:'',
  setAttribute(key,value){this.attributes[key]=value;},append(...children){this.children.push(...children);}};}};
const original=fs.readFileSync(process.argv[1],'utf8');
const source=original.replace(/\s+init\(\);\s*\}\)\(\);\s*$/,
  '\nwindow.test={sensorSparkline};\n})();');
const scope={document,window:{}};vm.createContext(scope);vm.runInContext(source,scope);
const now=Math.floor(Date.now()/1000)*1000;
const sensor={name:'temperature',lower:-10,upper:10,samples:Array.from({length:721},(_,i)=>({
  utc:new Date(now+(i-720)*5000).toISOString(),value:i===617 ? 25 : 0}))};
const plot=scope.window.test.sensorSparkline(sensor);
const path=plot.children.find(node=>node.attributes.class==='sparkline-value');
assert.ok(path.attributes.d.split(/[ML]/).filter(Boolean).length<=104,'all equipment mini plots are bounded');
assert.ok(plot.children.some(node=>node.tag==='circle'),'bucket extrema retain a narrow limit excursion');
"""


class RosConsoleUiTests(unittest.TestCase):
    def test_three_tabs_group_overview_packets_and_tc_without_duplicate_ids(self):
        parser = _Sections()
        page = PAGE.read_text(encoding="utf-8")
        parser.feed(page)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        for ident in ("attitude-canvas", "sensor-list", "telemetry-chart", "alarm-rows",
                      "ros-current-alerts", "spacecraft-event-rows"):
            self.assertIn("ros-view-status", parser.parents[ident])
        for ident in ("packet-record-rows", "packet-raw-hex", "packet-field-rows"):
            self.assertIn("ros-view-telemetry", parser.parents[ident])
        self.assertIn("ros-view-tc", parser.parents["sim-reservation-rows"])
        self.assertIn("01 종합 관제", page)
        self.assertIn("02 수신 TM", page)
        self.assertIn("/assets/ros-packet-browser.js", page)

    def test_standalone_preview_has_no_external_scripts_or_api_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(build_stage9_preview, "OUTPUT", Path(directory)):
                paths = build_stage9_preview.build()
            page = paths[0].read_text(encoding="utf-8")
            self.assertNotIn('<script src="/assets/', page)
            self.assertNotIn("  init();", page)
            self.assertIn("서버/API 연결 없음", page)
            self.assertIn('id="packet-record-rows"', page)
            self.assertIn("오프라인 합성 예시", page)

    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_raw_parsed_selection_paging_filter_and_stale_response(self):
        result = subprocess.run(["node", "-e", BROWSER_TEST, str(PACKET_BROWSER)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_multi_sensor_sparklines_are_bounded_and_keep_limit_extrema(self):
        result = subprocess.run(["node", "-e", SPARKLINE_TEST, str(WORKBENCH)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
