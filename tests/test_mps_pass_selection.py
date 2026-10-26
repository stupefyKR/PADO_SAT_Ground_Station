"""The MPS browser must retain the physical contact, not its rolling ordinal ID."""

import os
from pathlib import Path
import shutil
import subprocess
import unittest


APP = Path(__file__).resolve().parents[1] / "web/common/app.js"

NODE_TEST = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {document: {body: {dataset: {page: 'selection-test'}}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const same = context.mpsSamePhysicalPass;
const old = {station_id: 'yonsei', band: 'UHF', pass_id: 'yonsei-003',
  aos_utc_s: 10000, los_utc_s: 10600, clipped_start: false, clipped_end: false};
assert.equal(same(old, {...old, pass_id: 'yonsei-002', aos_utc_s: 10004,
  los_utc_s: 10605}), true, 'renumbered future pass keeps selection');
assert.equal(same(old, {...old, aos_utc_s: 15500, los_utc_s: 16100}), false,
  'reused ordinal ID must not select an orbit later');
assert.equal(same(old, {...old, station_id: 'kari', band: 'S_BAND',
  pass_id: 'kari-003'}), false, 'different station must not inherit selection');
const kari = {...old, station_id: 'kari', band: 'S_BAND', pass_id: 'kari-003'};
assert.equal(same(kari, {...kari, pass_id: 'kari-002', aos_utc_s: 10004,
  los_utc_s: 10605}), true, 'Daejeon pass selection also follows contact time');
assert.equal(same(old, {...old, pass_id: 'yonsei-004', aos_utc_s: 10620,
  los_utc_s: 11220}), false, 'nearby but disjoint contact is distinct');
assert.equal(same(old, {...old, clipped_start: true}), false,
  'rolling clipped boundary cannot identify a future pass');
"""

NODE_NAV_TEST = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.disabled = false;
    this.hidden = false;
    this._text = '';
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this._text = ''; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  click() {
    if (this.disabled) return;
    this.onclick?.();
    this.listeners.click?.();
  }
  scrollIntoView() {}
}

const panel = new Element();
const timeline = new Element();
const tag = new Element();
const toolbar = new Element();
const foot = new Element();
const notice = new Element();
const inspector = new Element();
const draftName = new Element();
const count = new Element();
count.previousElementSibling = new Element();
const position = new Element();
const previous = new Element('button');
const next = new Element('button');
panel.querySelector = (selector) => ({
  '.timeline': timeline,
  '.panel-head .tag': tag,
  '.toolbar': toolbar,
  '.panel-foot': foot,
})[selector];
panel.querySelectorAll = (selector) => {
  assert.equal(selector, 'tbody tr');
  return timeline.children[0]?.children[1]?.children ?? [];
};
const document = {
  body: {dataset: {page: 'selection-test'}},
  createElement: (tag) => new Element(tag),
  getElementById: (id) => ({
    'mps-passes': count,
    'mps-pass-position': position,
    'mps-pass-previous': previous,
    'mps-pass-next': next,
  })[id] ?? null,
  querySelector: (selector) => ({
    '.passes-panel': panel,
    '.notice span': notice,
    '.inspector-panel dl': inspector,
    '.draft-meta strong': draftName,
  })[selector],
};
const context = {document};
vm.createContext(context);
vm.runInContext(`
  let mpsSelectedPass = null;
  function mpsDrawPass() {}
  function mpsRenderSequenceEditor(pass) { mpsSelectedPass = pass; }
`, context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);

const selectedId = () => vm.runInContext('mpsSelectedPass?.pass_id', context);
const rows = () => panel.querySelectorAll('tbody tr');
const now = Math.floor(Date.now() / 1000);
const pass = (id, station, start) => ({
  pass_id: id, station_id: station, band: station === 'yonsei' ? 'UHF' : 'S_BAND',
  aos_utc_s: start, los_utc_s: start + 600,
  max_elevation_deg: 40, minimum_elevation_deg: 10,
  clipped_start: false, clipped_end: false,
});
const first = pass('yonsei-001', 'yonsei', now + 1000);
const second = pass('kari-001', 'kari', now + 3000);
const third = pass('yonsei-002', 'yonsei', now + 5000);
const schedule = (passes) => ({
  schedule_state: 'validated', schedule_sha256: 'a'.repeat(64), passes,
});
const render = (data) => context.renderMpsPasses(data, {role: 'planner'}, []);

// N13 ordering is chronological even if the payload arrives out of order.
render(schedule([third, first, second]));
assert.equal(selectedId(), first.pass_id, 'nearest future PASS is initially selected');
assert.equal(previous.disabled, true);
assert.equal(next.disabled, false);
assert.match(position.textContent, /^1 \/ 3/);
assert.deepEqual(rows().map((row) => row.children[4].textContent),
  [first.pass_id, second.pass_id, third.pass_id]);
assert.deepEqual(rows().map((row) => row.attributes['aria-selected']),
  ['true', 'false', 'false']);

next.click();
assert.equal(selectedId(), second.pass_id, 'next control selects the following PASS');
assert.equal(draftName.textContent, second.pass_id, 'inspector follows navigation');
assert.match(position.textContent, /^2 \/ 3/);
assert.equal(previous.disabled, false);
assert.equal(next.disabled, false);
next.click();
assert.equal(selectedId(), third.pass_id);
assert.equal(next.disabled, true, 'last PASS disables next navigation');
next.click();
assert.equal(selectedId(), third.pass_id, 'end boundary cannot move selection');
previous.click();
assert.equal(selectedId(), second.pass_id, 'previous control moves backward');

// A rolling N13 schedule renumbers IDs; keep the physical contact selected.
const refreshedSecond = {...second, pass_id: 'kari-009',
  aos_utc_s: second.aos_utc_s + 4, los_utc_s: second.los_utc_s + 5};
render(schedule([first, third, refreshedSecond]));
assert.equal(selectedId(), refreshedSecond.pass_id);
assert.match(position.textContent, /^2 \/ 3/);
assert.deepEqual(rows().map((row) => row.attributes['aria-selected']),
  ['false', 'true', 'false']);

// Direct row selection and navigation share the same selected index.
rows()[0].click();
assert.equal(selectedId(), first.pass_id);
assert.equal(previous.disabled, true);
next.click();
assert.equal(selectedId(), refreshedSecond.pass_id);

render(schedule([]));
assert.equal(selectedId(), undefined, 'empty schedule clears stale selection');
assert.equal(previous.disabled, true);
assert.equal(next.disabled, true);
assert.equal(previous.onclick, null);
assert.equal(next.onclick, null);
assert.equal(position.textContent, '선택 가능한 PASS 없음');

render({schedule_state: 'absent', schedule_sha256: null, passes: []});
assert.equal(tag.textContent, 'FDS N13 산출물 대기',
  'successful API response with no N13 is shown as publication waiting');
context.mpsPassesUnavailable();
assert.equal(tag.textContent, 'N13 API 조회 오류',
  'API failure has a distinct status from an unpublished N13');
"""

NODE_BOOTSTRAP_TEST = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(process.argv[1], 'utf8');
const schedule = {schedule_state: 'validated', schedule_sha256: 'a'.repeat(64), passes: []};
async function makePage(catalogReady, passesReady) {
  const timers = [];
  const rendered = [];
  const errors = [];
  let unavailable = 0;
  const node = {addEventListener() {}};
  const draftTag = {textContent: '', className: ''};
  const context = {
    document: {body: {dataset: {page: 'selection-test'}}, visibilityState: 'visible',
      getElementById: () => node,
      querySelector: (selector) => selector === '.draft-panel .panel-head .tag' ? draftTag : null},
    setInterval: (callback, delay) => timers.push({callback, delay}),
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  context.api = async (path) => {
    if (path === '/api/session') return {username: 'operator', role: 'planner', csrf: 'x'};
    if (path === '/api/mps/catalog') {
      if (!catalogReady) throw new Error('catalog unavailable');
      return {version: 'test', commands: [], draft_commands: []};
    }
    if (path === '/api/mps/passes') {
      if (!passesReady) throw new Error('schedule unavailable');
      return schedule;
    }
    if (path === '/api/mps/test-sequences') return {items: []};
    if (path === '/api/fds/orbit') throw new Error('orbit unavailable');
    if (path === '/api/mps/test-ros-feedback') return null;
    throw new Error(`unexpected API: ${path}`);
  };
  context.value = () => {};
  context.showError = (message) => errors.push(message);
  context.renderMpsDraftCatalog = () => {};
  context.mpsSetOrbit = () => {};
  context.mpsRenderSequenceEditor = () => {};
  context.mpsSetN10Feedback = () => {};
  context.renderMpsPasses = (data, session, drafts) => {
    draftTag.textContent = 'rendered';
    rendered.push({data, role: session.role, drafts});
  };
  context.mpsPassesUnavailable = () => { unavailable += 1; };
  await context.workspace('mps');
  return {timers, rendered, errors, draftTag, get unavailable() { return unavailable; },
    setCatalogReady(value) { catalogReady = value; },
    setPassesReady(value) { passesReady = value; }};
}

(async () => {
  const catalogOut = await makePage(false, true);
  assert.equal(catalogOut.unavailable, 0);
  assert.equal(catalogOut.rendered.length, 1,
    'a catalog failure must not hide a valid N13 PASS schedule');
  assert.equal(catalogOut.rendered[0].role, 'viewer',
    'sequence editing stays locked without the command catalog');
  assert.equal(catalogOut.draftTag.textContent, '명령 DB 조회 오류');
  assert.ok(catalogOut.errors.some((message) => message.includes('명령 DB')));
  catalogOut.setCatalogReady(true);
  await catalogOut.timers.find(({delay}) => delay === 60000).callback();
  assert.equal(catalogOut.rendered.at(-1).role, 'planner',
    'catalog recovery restores the authorized sequence editor');
  assert.equal(catalogOut.draftTag.textContent, 'rendered');

  const scheduleOut = await makePage(true, false);
  assert.equal(scheduleOut.unavailable, 1,
    'N13 API failure is distinct from an absent but successful response');
  assert.equal(scheduleOut.rendered.length, 0);
  scheduleOut.setPassesReady(true);
  await scheduleOut.timers.find(({delay}) => delay === 60000).callback();
  assert.equal(scheduleOut.rendered.length, 1,
    'the page retries N13 after an initial API failure');
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""


class MpsPassSelectionTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_selection_follows_contact_geometry_not_ordinal_id(self):
        result = subprocess.run(
            ["node", "-e", NODE_TEST, str(APP)],
            capture_output=True, text=True, timeout=10, check=False,
            env=os.environ.copy(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_pass_navigation_and_refresh_preserve_selection(self):
        result = subprocess.run(
            ["node", "-e", NODE_NAV_TEST, str(APP)],
            capture_output=True, text=True, timeout=10, check=False,
            env=os.environ.copy(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_schedule_bootstrap_survives_catalog_failure_and_retries_n13(self):
        result = subprocess.run(
            ["node", "-e", NODE_BOOTSTRAP_TEST, str(APP)],
            capture_output=True, text=True, timeout=10, check=False,
            env=os.environ.copy(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
