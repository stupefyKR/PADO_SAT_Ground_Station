"""Delayed synthetic TM stays historical in the ROS presentation."""

from pathlib import Path
import shutil
import subprocess
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "web/ops/workbench.js"
PAGE = Path(__file__).resolve().parents[1] / "web/ops/index.html"

NODE_TEST = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function element() {
  return {textContent: '', children: [], classList: {add(){}, toggle(){}},
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = [...items]; },
    setAttribute(){}, addEventListener(){}};
}
const nodes = new Map();
const document = {
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
  createElement: element,
  createElementNS: element,
};
const source = fs.readFileSync(process.argv[1], 'utf8');
const testable = source.replace(/\s+init\(\);\s*\}\)\(\);\s*$/,
  '\nwindow.rosTmUiTest = {tmTiming, showTmTiming, renderCurrentAlerts, renderTrend, setSnapshot(snapshot) { selectedTm = snapshot; selectedSensor = snapshot.sensors[0]; publishedReachable = true; }};\n})();');
assert.notEqual(testable, source, 'test hooks replace only the automatic page initialization');
const scope = {window: {}, document};
vm.createContext(scope);
vm.runInContext(testable, scope);
const ui = scope.window.rosTmUiTest;
const now = Math.floor(Date.now() / 1000) * 1000;
const utc = (ms) => new Date(ms).toISOString().slice(0, 19) + 'Z';
const snapshot = (age) => ({received_utc: utc(now), sensors: [{
  id: 'temp', name: 'temperature', group: 'thermal', unit: 'C', lower: 0, upper: 10,
  samples: [{utc: utc(now - age - 60000), value: 11},
    {utc: utc(now - age), value: 12}]
}]});

const historical = snapshot(10 * 60000);
assert.equal(ui.tmTiming(historical, now).delayed, true);
assert.equal(ui.tmTiming(historical, now).stale, true);
ui.setSnapshot(historical);
ui.showTmTiming(historical, now);
assert.match(document.getElementById('ros-tm-timing').textContent, /수신 UTC.*최근 계측 UTC.*과거 측정 TM/);
ui.renderCurrentAlerts();
assert.match(document.getElementById('ros-current-count').textContent, /현재 판정 불가/);
assert.equal(document.getElementById('ros-alarm-state').textContent, '현재 판정 불가');
ui.renderTrend();
assert.match(document.getElementById('selected-state').textContent, /과거 측정.*현재 판정 불가/);
assert.match(document.getElementById('event-count').textContent, /과거 측정.*2건/);
assert.equal(document.getElementById('alarm-rows').children.length, 2,
  'historical breaches remain in the event table');

const current = snapshot(30000);
assert.equal(ui.tmTiming(current, now).delayed, false);
assert.equal(ui.tmTiming(current, now).stale, false);
ui.setSnapshot(current);
ui.renderCurrentAlerts();
assert.match(document.getElementById('ros-current-count').textContent, /1개 초과/);
assert.match(document.getElementById('ros-alarm-state').textContent, /초과 1개/);
"""

BEACON_TEST = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = new Map();
function element() { return {textContent:'', disabled:false, children:[],
  replaceChildren(...items) { this.children = items; },
  append(...items) { this.children.push(...items); },
  classList: {toggle(){}}}; }
const document = {getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
  createElement: element};
let beacon = {received_utc: new Date().toISOString(), sampled_utc:'2026-10-27T01:00:00Z',
  fields:{beacon_counter:10, beacon_period_s:5, mode:1}};
const scope = {document, window:{}, api: async() => beacon};
vm.createContext(scope);
const source = fs.readFileSync(process.argv[1], 'utf8');
vm.runInContext(source.replace(/\s+init\(\);\s*\}\)\(\);\s*$/,
  '\nwindow.test = {refreshBeacon, showSimTmRequests, setup(role, rows) {session={role}; simTmAvailable=true; simTmRequests=rows;}};\n})();'), scope);
(async () => {
  const ui = scope.window.test;
  await ui.refreshBeacon();
  assert.equal(document.getElementById('sim-beacon-state').textContent, '비콘 수신 중');
  beacon.received_utc = new Date(Date.now()-60000).toISOString();
  await ui.refreshBeacon();
  assert.equal(document.getElementById('sim-beacon-state').textContent, '최근 비콘 오래됨');
  ui.setup('auditor', []); ui.showSimTmRequests();
  assert.equal(document.getElementById('sim-tm-request').disabled, true);
  ui.setup('operator', [{created_at:1800000000, created_by:'operator', status:'SENDING'}]);
  ui.showSimTmRequests(); assert.equal(document.getElementById('sim-tm-request').disabled, true);
  ui.setup('operator', [{created_at:1800000000, created_by:'operator', status:'ACKED', ack:{result:0}}]);
  ui.showSimTmRequests(); assert.equal(document.getElementById('sim-tm-request').disabled, false);
  const status = document.getElementById('sim-tm-request-rows').children[0].children[2].textContent;
  assert.match(status, /요청 수락/); assert.doesNotMatch(status, /완료/);
})().catch((err) => { console.error(err); process.exitCode=1; });
"""


class RosTmUiTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_beacon_freshness_and_dump_acceptance_are_visible(self):
        result = subprocess.run(["node", "-e", BEACON_TEST, str(SOURCE)],
                                capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_delayed_tm_is_historical_not_live_alarm(self):
        self.assertIn('id="ros-tm-timing"', PAGE.read_text(encoding="utf-8"))
        result = subprocess.run(
            ["node", "-e", NODE_TEST, str(SOURCE)], capture_output=True,
            text=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
