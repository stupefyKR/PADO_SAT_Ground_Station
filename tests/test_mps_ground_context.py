"""The MPS map shows nearby orbit tracks while keeping the chosen contact distinct."""

import os
from pathlib import Path
import shutil
import subprocess
import unittest


LIVE = Path(__file__).resolve().parents[1] / "web/mps/live.js"

NODE_TEST = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const base = Math.floor(Date.now() / 60000) * 60000;
const periodMs = 600000;
const stamp = (ms) => new Date(ms).toISOString();
const pass = (aos, los) => ({
  pass_id: 'yonsei-001', station_id: 'yonsei', band: 'UHF',
  aos_utc_s: aos / 1000, los_utc_s: los / 1000,
});
function orbit(start, end, aos, los, longitude = (index) => -100 + index) {
  const rows = [];
  for (let time = start, index = 0; time <= end; time += 60000, index++) {
    rows.push([stamp(time), 6878137, 0, 0, longitude(index), 30, 500000,
      1, 0, 0]);
  }
  return {
    schema: 'fds.orbit-view.v1', source: 'TLE', norad_id: 99999,
    orbital_period_s: periodMs / 1000,
    input_epoch_utc: stamp(base - 60000),
    start_utc: stamp(start), end_utc: stamp(end), samples: rows,
    stations: [{id: 'yonsei', band: 'UHF', latitude_deg: 37.5,
      longitude_deg: 126.9, altitude_m: 0,
      passes: [{aos_utc: stamp(aos), los_utc: stamp(los)}]}],
  };
}

class CanvasContext {
  constructor() {
    this.strokes = []; this.path = []; this.lineWidth = 1; this.texts = []; this.arcs = [];
    this.strokeStyle = '#000';
  }
  beginPath() { this.path = []; }
  moveTo(x, y) { this.path.push(['M', x, y]); }
  lineTo(x, y) { this.path.push(['L', x, y]); }
  stroke() { this.strokes.push({path: this.path.slice(),
    width: this.lineWidth, style: this.strokeStyle}); }
  clearRect() {}
  fillRect() {}
  drawImage() {}
  arc(x, y, radius) {this.arcs.push({x, y, radius});}
  fill() {}
  fillText(text, x, y) {this.texts.push({text, x, y});}
  setLineDash() {}
  save() {}
  restore() {}
}
const groundContext = new CanvasContext();
const polarContext = new CanvasContext();
const nodes = {
  'pass-view-state': {textContent: ''},
  'pass-view-note': {textContent: ''},
  'mps-ground-view': {width: 720, height: 340,
    getContext: () => groundContext},
  'mps-polar-view': {width: 420, height: 340,
    getContext: () => polarContext},
  'mps-earth-image': {complete: false, naturalWidth: 0},
};
const context = {document: {getElementById: (id) => nodes[id]}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);

const aos = base + 4 * periodMs + 30000;
const los = aos + 120000;
const choice = pass(aos, los);
const fullOrbit = orbit(base, base + 9 * periodMs, aos, los);
const track = context.mpsGroundContext(fullOrbit, choice);
assert.equal(Date.parse(track.rows[0][0]), aos - 2 * periodMs,
  'ground track begins exactly two orbital periods before AOS');
assert.equal(Date.parse(track.rows.at(-1)[0]), los + 2 * periodMs,
  'ground track ends exactly two orbital periods after LOS');
assert.equal(track.clippedBefore, false);
assert.equal(track.clippedAfter, false);
assert.ok(track.rows.length > context.mpsPassSamples(fullOrbit, choice).rows.length,
  'ground context contains much more than the selected contact');

const clippedOrbit = orbit(base + 3 * periodMs, base + 5 * periodMs,
  aos, los);
const clipped = context.mpsGroundContext(clippedOrbit, choice);
assert.equal(Date.parse(clipped.rows[0][0]), Date.parse(clippedOrbit.start_utc),
  'missing earlier FDS samples are clipped to available coverage');
assert.equal(Date.parse(clipped.rows.at(-1)[0]), Date.parse(clippedOrbit.end_utc),
  'missing later FDS samples are clipped to available coverage');
assert.equal(clipped.clippedBefore, true);
assert.equal(clipped.clippedAfter, true);

// Repeated antimeridian crossings must remain separate paths on the map.
const crossing = orbit(base, base + 9 * periodMs, aos, los,
  (index) => ((170 + index * 12 + 180) % 360) - 180);
context.mpsSetOrbit(crossing);
context.mpsDrawPass(choice);
assert.match(nodes['pass-view-state'].textContent, /FDS/);
const polarPass = polarContext.strokes.findLast((stroke) => stroke.width === 3);
assert.equal(polarPass.path.filter(([operation]) => operation === 'L').length,
  context.mpsPassSamples(crossing, choice).rows.length - 1,
  'polar plot remains limited to the selected contact');
for (const label of ['0°', '30°', '60°', 'AOS', 'LOS'])
  assert.ok(polarContext.texts.some(entry => entry.text === label), `polar annotation ${label}`);
assert.ok(polarContext.texts.some(entry => entry.text.includes(
  stamp(aos).slice(0, 19).replace('T', ' ') + ' UTC')), 'AOS includes exact UTC');
assert.ok(polarContext.texts.some(entry => entry.text.includes(
  stamp(los).slice(0, 19).replace('T', ' ') + ' UTC')), 'LOS includes exact UTC');

// Peak events come from FDS, including times between the 60-second samples.
const peakAt = aos + 45000;
fullOrbit.stations[0].passes[0].peak_utc = stamp(peakAt);
fullOrbit.stations[0].passes[0].max_elevation_deg = 62.3;
const peakChoice = {...choice, peak_utc_s: aos / 1000 + 40, max_elevation_deg: 61};
assert.equal(context.mpsPolarEvents(peakChoice, fullOrbit.stations[0].passes[0]).peak.at, peakAt);
assert.equal(context.mpsPolarEvents(peakChoice, {}).peak.elevation, 61, 'N13 peak fallback');
assert.equal(context.mpsPolarEvents(choice, {peak_utc:stamp(los+1000),max_elevation_deg:85}).peak,
  null, 'never invent an out-of-contact peak');
context.mpsSetOrbit(fullOrbit); context.mpsDrawPass(peakChoice);
assert.ok(polarContext.texts.some(entry => entry.text === 'MAX'));
assert.ok(polarContext.texts.some(entry => entry.text.includes('62.3°') &&
  entry.text.includes(stamp(peakAt).slice(0, 19).replace('T', ' ') + ' UTC')));
const peakMark = polarContext.arcs.findLast(entry => entry.radius === 5);
assert.ok(Math.abs(Math.hypot(peakMark.x-210,peakMark.y-143)-112*(1-62.3/90)) < 1e-8,
  'peak mark uses FDS maximum elevation rather than the largest sampled elevation');
const peakPath = polarContext.strokes.findLast(stroke => stroke.width === 3);
assert.equal(peakPath.path.filter(([operation]) => operation === 'L').length,
  context.mpsPassSamples(fullOrbit, peakChoice).rows.length,
  'insert the peak vertex so its marker lies on the contact path');
assert.ok(polarContext.texts.every(entry => entry.x >= 0 && entry.x <= 420 && entry.y >= 0 && entry.y < 340));
const tracks = groundContext.strokes.filter((stroke) =>
  stroke.path.some(([operation]) => operation === 'L') && stroke.width > 1);
assert.ok(tracks.length >= 2, 'context and selected contact both have visible tracks');
const widths = [...new Set(tracks.map(({width}) => width))].sort((a, b) => a - b);
assert.ok(widths.length >= 2 && widths.at(-1) > widths[0],
  'selected contact is drawn more strongly than nearby orbit tracks');
assert.ok(tracks.findIndex(({width}) => width === widths[0]) <
  tracks.findIndex(({width}) => width === widths.at(-1)),
  'context tracks are drawn before the selected contact');
for (const stroke of tracks) {
  let previous = null;
  for (const [operation, x, y] of stroke.path) {
    if (operation === 'L' && previous) {
      assert.ok(Math.abs(x - previous[0]) <= 360,
        'ground track may not draw a line across the map at the antimeridian');
    }
    previous = [x, y];
  }
}
"""


class MpsGroundContextTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_neighbor_orbits_clip_and_do_not_bridge_dateline(self):
        result = subprocess.run(
            ["node", "-e", NODE_TEST, str(LIVE)], capture_output=True,
            text=True, timeout=10, check=False, env=os.environ.copy(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
