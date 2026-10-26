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


class RosTmUiTest(unittest.TestCase):
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
