"""MPS displays FDS samples and discards stale asynchronous PASS responses."""

from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "web/mps/doppler.js"

NODE_TEST = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Context {
  constructor() { this.texts = []; this.strokes = []; this.arcs = []; this.path = []; }
  clearRect() { this.texts = []; this.strokes = []; this.arcs = []; }
  fillRect() {}
  beginPath() { this.path = []; }
  moveTo(x, y) { this.path.push(['M', x, y]); }
  lineTo(x, y) { this.path.push(['L', x, y]); }
  stroke() { this.strokes.push({path: this.path.slice(), width: this.lineWidth, color: this.strokeStyle}); }
  setLineDash() {}
  fillText(text, x, y) { this.texts.push({text, x, y}); }
  arc(x, y, radius) { this.arcs.push({x, y, radius}); }
  fill() {}
}
const chart = new Context();
const nodes = Object.fromEntries(['state','chart','download','meta','note','hover','retry']
  .map(name => [`mps-doppler-${name}`, {textContent: '', disabled: true, hidden: false}]));
Object.assign(nodes['mps-doppler-chart'], {width: 1000, height: 320,
  getContext: () => chart, getBoundingClientRect: () => ({left: 10, width: 500})});
const requests = [], downloads = [], urls = [], revoked = [], csvRequests = [];
let csvBody = 'utc,doppler_shift_hz\n2026-11-11T00:00:00Z,1000\n';
let csvError = null;
let csvProfileHash = 'f'.repeat(64);
let now = Date.now();
const scope = {
  Date: class extends Date { static now() { return now; } },
  document: {getElementById: id => nodes[id] ?? null,
    createElement: tag => { assert.equal(tag, 'a'); return {click() {downloads.push({href:this.href, name:this.download});}};}},
  api: url => new Promise((resolve, reject) => requests.push({url, resolve, reject})),
  fetch: async (url, options) => {
    csvRequests.push({url, options});
    return csvError ? {ok:false, json:async()=>({error:csvError})} :
      {ok:true, headers:{get: name => name === 'Content-Type' ?
        'text/csv; charset=utf-8' : name === 'X-Pado-Profile-SHA256' ? csvProfileHash : null},
        blob:async()=>new Blob([csvBody])};
  },
  URL: {createObjectURL(blob) { urls.push(blob); return 'blob:fds-result'; },
    revokeObjectURL(url) { revoked.push(url); }},
  setTimeout: callback => callback(),
};
vm.createContext(scope);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), scope);
const stamp = value => new Date(value).toISOString();
const aos = Date.parse('2026-11-11T23:59:40Z'), los = aos + 60000;
const pass = (station, ordinal=1) => ({pass_id:`${station}-${String(ordinal).padStart(3, '0')}`,
  station_id:station, band:station==='yonsei'?'UHF':'S_BAND',
  aos_utc_s:aos/1000,los_utc_s:los/1000});
const yonsei = pass('yonsei'), kari = pass('kari');
const schedule = digest => ({schedule_state:'validated',schedule_sha256:digest.repeat(64)});
const profile = (selected, digest='a') => ({schema:'fds.doppler-profile.v1',
  schedule_sha256:digest.repeat(64),profile_sha256:'f'.repeat(64),pass_id:selected.pass_id,
  station_id:selected.station_id,band:selected.band,norad_id:99999,source:'TLE',
  input_epoch_utc:stamp(aos-1000),aos_utc:stamp(aos),los_utc:stamp(los),
  carrier_hz:selected.station_id==='yonsei'?435e6:2.2e9,frequency_example:true,sample_step_s:10,
  samples:[[stamp(aos),-1000,3000,435003000],
    [stamp(aos+30000),0,0,435000000],[stamp(los),1000,-3000,434997000]]});
const flush = async()=>{await Promise.resolve();await Promise.resolve();};

(async()=>{
  const first = scope.mpsLoadDoppler(yonsei,schedule('a'));
  assert.equal(nodes['mps-doppler-download'].disabled,true);
  assert.equal(requests.length,1);
  await scope.mpsLoadDoppler(yonsei,schedule('a'));
  assert.equal(requests.length,1,'same in-flight selection does not issue another request');
  const second = scope.mpsLoadDoppler(kari,schedule('a'));
  requests[1].resolve(profile(kari)); await second;
  assert.match(nodes['mps-doppler-state'].textContent,/항우연/,'S-band PASS is supported without a TC editor');
  assert.equal(nodes['mps-doppler-download'].disabled,false);
  requests[0].resolve(profile(yonsei)); await first;
  assert.match(nodes['mps-doppler-state'].textContent,/항우연/,'late previous-station response is ignored');
  await scope.mpsLoadDoppler(kari,schedule('a'));
  assert.equal(requests.length,2,'same validated selection reuses its FDS profile');
  for(const text of ['kHz','UTC','AOS','LOS','23:59:40','2026-11-11','2026-11-12'])
    assert.ok(chart.texts.some(item=>item.text===text),text);
  assert.ok(chart.strokes.some(line=>line.width===2.5 && line.path.length===3),
    'all FDS samples are used directly for the plotted profile');
  assert.ok(chart.strokes.some(line=>line.color==='#91b8c7'),'zero shift line');
  assert.ok(chart.texts.every(item=>item.x>=0&&item.x<1000&&item.y>=0&&item.y<=320));
  nodes['mps-doppler-chart'].onpointermove({clientX:10+500*(84+(1000-112)/2)/1000});
  assert.match(nodes['mps-doppler-hover'].textContent,/2026-11-12 00:00:10\.000 UTC/);
  assert.match(nodes['mps-doppler-hover'].textContent,/0\.000 Hz/);
  assert.match(nodes['mps-doppler-meta'].textContent,/예시 주파수/);
  await nodes['mps-doppler-download'].onclick();
  assert.equal(csvRequests[0].url,requests[1].url+'&format=csv');
  assert.equal(csvRequests[0].options.credentials,'same-origin');
  assert.equal(await urls[0].text(),csvBody,'download uses exact FDS CSV bytes');
  assert.deepEqual(downloads,[{href:'blob:fds-result',name:'doppler-kari-001-aaaaaaaaaaaa.csv'}]);
  assert.deepEqual(revoked,['blob:fds-result']);

  now += 60001;
  const periodic = scope.mpsRefreshDoppler();
  assert.equal(requests.length,3,'same N13 profile is revalidated after sixty seconds');
  const changedFrequency = profile(kari);
  changedFrequency.carrier_hz += 1e6;
  changedFrequency.profile_sha256 = 'e'.repeat(64);
  changedFrequency.samples.forEach(row=>row[3]+=1e6);
  requests[2].resolve(changedFrequency); await periodic;
  assert.match(nodes['mps-doppler-meta'].textContent,/2201\.000000 MHz/,
    'a carrier-only change refreshes even when N13 hash is unchanged');
  await scope.mpsRefreshDoppler();
  assert.equal(requests.length,3,'immediate repeated refresh is cached');
  await nodes['mps-doppler-download'].onclick();
  assert.equal(downloads.length,1,'a CSV with a different profile hash is never downloaded');
  assert.equal(nodes['mps-doppler-download'].disabled,true);
  assert.match(nodes['mps-doppler-note'].textContent,/프로파일이 변경/);
  assert.ok(!chart.strokes.length,'mismatched CSV clears the outdated graph');
  const profileRetry = nodes['mps-doppler-retry'].onclick();
  requests[3].resolve(changedFrequency); await profileRetry;
  assert.equal(nodes['mps-doppler-download'].disabled,false);
  csvProfileHash = 'e'.repeat(64);

  const refreshed = scope.mpsLoadDoppler(kari,schedule('b'));
  assert.equal(nodes['mps-doppler-download'].disabled,true,'changed N13 digest disables old CSV immediately');
  assert.ok(!chart.strokes.length,'changed N13 clears the old profile');
  requests[4].reject(new Error('schedule_changed')); await refreshed;
  assert.equal(nodes['mps-doppler-download'].disabled,true);
  assert.match(nodes['mps-doppler-note'].textContent,/일정이 갱신/);
  assert.equal(nodes['mps-doppler-retry'].hidden,false);
  const retry = nodes['mps-doppler-retry'].onclick();
  assert.equal(requests.length,6,'failed request can be retried');
  requests[5].resolve(profile(kari,'b')); await retry;
  assert.equal(nodes['mps-doppler-download'].disabled,false);
  csvError='schedule_changed';
  await nodes['mps-doppler-download'].onclick();
  assert.equal(nodes['mps-doppler-download'].disabled,true,'stale download cannot remain enabled');
  assert.equal(downloads.length,1,'error response is never downloaded as a CSV');
  assert.match(nodes['mps-doppler-note'].textContent,/일정이 갱신/);

  const pending = scope.mpsLoadDoppler(yonsei,schedule('c'));
  scope.mpsClearDoppler();
  requests.at(-1).resolve(profile(yonsei,'c')); await pending;
  assert.equal(nodes['mps-doppler-state'].textContent,'PASS를 선택하세요');
  assert.equal(nodes['mps-doppler-download'].disabled,true,'empty selection invalidates pending results');
  assert.equal(nodes['mps-doppler-chart'].onpointermove,null);

  const invalid = profile(yonsei);
  invalid.samples[1][0]=invalid.samples[0][0];
  assert.throws(()=>scope.mpsValidateDoppler(invalid,yonsei,'a'.repeat(64)),/invalid_doppler/);
  assert.throws(()=>scope.mpsValidateDoppler(profile(kari),yonsei,'a'.repeat(64)),/invalid_doppler/);
  const clipped=profile(yonsei);clipped.samples.shift();
  assert.throws(()=>scope.mpsValidateDoppler(clipped,yonsei,'a'.repeat(64)),/invalid_doppler/);
  delete nodes['mps-doppler-chart'];
  await scope.mpsLoadDoppler(yonsei,schedule('a'));
  scope.mpsClearDoppler();
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


class MpsDopplerUiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_profile_plot_download_and_stale_selection_guards(self):
        result = subprocess.run([shutil.which("node"), "-e", NODE_TEST, str(SOURCE)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_profile_assets_and_hooks_are_present(self):
        markup = (ROOT / "web/mps/index.html").read_text(encoding="utf-8")
        live = (ROOT / "web/mps/live.js").read_text(encoding="utf-8")
        self.assertLess(markup.index("/assets/mps-doppler.js"), markup.index("/assets/mps-live.js"))
        self.assertIn('id="mps-doppler-chart"', markup)
        self.assertIn('id="mps-doppler-download" type="button" disabled', markup)
        self.assertIn('mpsLoadDoppler(pass, schedule)', live)
        self.assertIn('mpsClearDoppler()', live)
        self.assertIn('mpsRefreshDoppler()', live)


if __name__ == "__main__":
    unittest.main()
