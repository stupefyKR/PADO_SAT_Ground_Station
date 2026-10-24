"""Visible clock contract for the four Stage 9 web entry pages."""

from html.parser import HTMLParser
from pathlib import Path
import os
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
PAGES = ("login", "ops", "mps", "orbit")


class PageStructure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.ids = []
        self.scripts = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.tags.append(tag)
        if "id" in attributes:
            self.ids.append((attributes["id"], tag, len(self.tags) - 1))
        if tag == "script" and "src" in attributes:
            self.scripts.append(attributes["src"])

    def handle_data(self, data):
        self.text.append(data)


NODE_CLOCK_TEST = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

let epoch = Date.parse('2026-10-23T14:59:59Z');
class TestDate extends Date {
  constructor(...args) { super(...(args.length ? args : [epoch])); }
  static now() { return epoch; }
}
const nodes = {
  'utc-clock': { textContent: '' },
  'kst-clock': { textContent: '' },
};
let tick = null;
const context = {
  Date: TestDate,
  document: {
    body: { dataset: { page: 'clock-test' } },
    getElementById: (id) => nodes[id] || null,
  },
  setInterval: (callback, milliseconds) => {
    assert.equal(milliseconds, 1000);
    assert.equal(tick, null, 'only one clock timer should be registered');
    tick = callback;
  },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
if (!tick) vm.runInContext('setupUtcClock()', context);
assert.match(nodes['utc-clock'].textContent, /14:59:59/);
assert.match(nodes['kst-clock'].textContent, /23:59:59/);
assert.match(nodes['utc-clock'].textContent, /2026-10-23/);
assert.match(nodes['kst-clock'].textContent, /2026-10-23/);
assert.ok(tick, 'clock should refresh without a page reload');
epoch += 1000;
tick();
assert.match(nodes['utc-clock'].textContent, /15:00:00/);
assert.match(nodes['kst-clock'].textContent, /00:00:00/);
assert.match(nodes['utc-clock'].textContent, /2026-10-23/);
assert.match(nodes['kst-clock'].textContent, /2026-10-24/);
"""

NODE_PASS_TEST = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let epoch = Date.parse('2026-10-23T10:00:00Z');
class TestDate extends Date {
  constructor(...args) { super(...(args.length ? args : [epoch])); }
  static now() { return epoch; }
}
const element = () => ({
  textContent: '', children: [],
  classList: { names: new Set(), add(name) { this.names.add(name); },
    remove(name) { this.names.delete(name); } },
  replaceChildren(...items) { this.children = items; },
  append(...items) { this.children.push(...items); },
});
const nodes = Object.fromEntries(['ros-pass-state', 'ros-pass-yonsei',
  'ros-pass-kari'].map((id) => [id, element()]));
const context = { Date: TestDate, document: {
  body: { dataset: { page: 'pass-test' } },
  getElementById: (id) => nodes[id] || null,
  createElement: () => element(),
}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const base = Math.floor(epoch / 1000);
vm.runInContext(`rosPassPlan = ${JSON.stringify({
  start: epoch - 1000, end: epoch + 7200000, inputEpoch: epoch - 1000,
  passes: [
    {station_id: 'yonsei', aos_utc_s: base + 600, los_utc_s: base + 1200,
      clipped_start: false, clipped_end: false},
    {station_id: 'kari', aos_utc_s: base - 300, los_utc_s: base + 300,
      clipped_start: false, clipped_end: false},
  ]
})}; renderRosPasses();`, context);
assert.match(nodes['ros-pass-state'].textContent, /FDS 시험 예측/);
assert.match(nodes['ros-pass-yonsei'].children[2].textContent, /AOS까지 00:10:00/);
assert.match(nodes['ros-pass-kari'].children[2].textContent, /LOS까지 00:05:00/);
assert.ok(nodes['ros-pass-kari'].classList.names.has('ros-pass-active'));
epoch += 7300000;
vm.runInContext('renderRosPasses()', context);
assert.match(nodes['ros-pass-state'].textContent, /판정 불가/);
assert.match(nodes['ros-pass-yonsei'].children[2].textContent, /--:--:--/);
"""


class SharedClockTests(unittest.TestCase):
    def test_every_entry_page_places_both_clocks_above_main_content(self):
        for page in PAGES:
            with self.subTest(page=page):
                source = (WEB / page / "index.html").read_text(encoding="utf-8")
                structure = PageStructure()
                structure.feed(source)
                self.assertIn("/assets/app.js", structure.scripts)
                self.assertIn("main", structure.tags)
                main_index = structure.tags.index("main")
                visible_text = " ".join(structure.text)
                self.assertIn("UTC", visible_text)
                self.assertIn("KST (UTC+9)", visible_text)
                for clock in ("utc-clock", "kst-clock"):
                    placements = [(tag, index) for name, tag, index in structure.ids
                                  if name == clock]
                    self.assertEqual(len(placements), 1, f"{page}: {clock} must be unique")
                    self.assertEqual(placements[0][0], "time")
                    self.assertLess(placements[0][1], main_index,
                                    f"{page}: {clock} should appear above the content")

    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_shared_clock_uses_utc_and_korean_time_across_midnight(self):
        result = subprocess.run(
            ["node", "-e", NODE_CLOCK_TEST, str(WEB / "common" / "app.js")],
            capture_output=True, text=True, timeout=10, check=False,
            env={**os.environ, "TZ": "America/Los_Angeles"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_ros_pass_countdown_distinguishes_prediction_and_expiry(self):
        result = subprocess.run(
            ["node", "-e", NODE_PASS_TEST, str(WEB / "common" / "app.js")],
            capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
