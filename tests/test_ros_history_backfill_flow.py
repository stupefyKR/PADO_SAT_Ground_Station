"""Late real PDU ingress updates the same historical ROS pose through HTTP and UI math."""

import json
import math
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from services.sim_ground.receiver import TmReceiver, load_limits
from services.webapp.auth import AuthStore
from services.webapp.main import AppServer, initialize_internal_token
from tests.test_sim_tc_web import FakeHasher
from tools.orbit_history import record_history
from tools.pado_packet_codec import encode_tm


ROOT = Path(__file__).resolve().parents[1]
NODE_FLOW = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const input=JSON.parse(fs.readFileSync(0,'utf8')), scope={};vm.createContext(scope);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),scope);
const cache=new scope.rosStateHistory.Cache(), at=input.at*1000;
cache.setOrbit(input.orbit);
cache.mergeTm(input.before);
const before=cache.resolve(at);
assert.equal(before.attitudeStatus,'UNKNOWN');
assert.ok(before.position_itrf_m,'GNSS location is known before attitude downlink');
assert.equal(scope.rosAttitudeMath.computeState(before).orientationKnown,false);
cache.mergeTm(input.after);
const after=cache.resolve(at);
assert.equal(after.attitudeStatus,'MEASURED');
assert.equal(after.utc,before.utc,'replaying the same past moment does not jump to reception time');
assert.equal(after.attitude.received_utc,input.after.attitudes[0].received_utc);
const scene=scope.rosAttitudeMath.computeState(after);
assert.equal(scene.orientationKnown,true);
assert.ok(Math.abs(scene.earthAlignment)<1e-5,'an arbitrary non-nadir quaternion stays non-nadir');
const gap=cache.resolve(at+60000);
assert.equal(gap.attitudeStatus,'UNKNOWN','a held display never becomes a measured attitude');
assert.ok(gap.positionSource.startsWith('FDS_'),'FDS supplies location independently of attitude coverage');
assert.equal(scope.rosAttitudeMath.computeState(gap).positionKnown,true);
assert.equal(scope.rosAttitudeMath.computeState(gap).orientationKnown,false);
assert.equal(gap.displayAttitudeStatus,'LAST_KNOWN');
assert.equal(scope.rosAttitudeMath.computeState(gap).orientationHeld,true);
assert.equal(scope.rosAttitudeMath.computeState(gap).displayAttitudeUtc,after.attitude.utc);
"""


class HistoryBackfillFlowTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "out").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="history-backfill-", dir=ROOT / "out")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        token = self.folder / "internal-token"
        initialize_internal_token(token)
        self.auth = AuthStore(self.folder / "users.db", FakeHasher())
        self.addCleanup(self.auth.close)
        self.run_password = secrets.token_urlsafe(24)
        self.auth.create_user("planner01", "planner", self.run_password)
        self.snapshot = self.folder / "test_tm_snapshot.json"
        self.ros = AppServer(("127.0.0.1", 0), mode="ros", transport="http",
            internal_token_file=token, draft_db=self.folder / "drafts.db", tm_preview=self.snapshot)
        self.addCleanup(self.ros.drafts.close)
        self.portal = AppServer(("127.0.0.1", 0), mode="portal", transport="http",
            internal_token_file=token, auth=self.auth,
            ros_url=f"http://127.0.0.1:{self.ros.server_port}")
        self.threads = []
        for server in (self.ros, self.portal):
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            self.threads.append(worker)
        self.addCleanup(self.stop_servers)
        inbox = self.folder / "n11"
        inbox.mkdir()
        self.receiver = TmReceiver(self.snapshot, inbox,
            load_limits(ROOT / "config/spacecraft.example.json"))
        self.addCleanup(self.receiver.archive.close)

        class Contact:
            def active(self, _instant):
                return True

        self.receiver.contact_gate = Contact()

    def stop_servers(self):
        for server in (self.portal, self.ros):
            server.shutdown()
            server.server_close()
        for worker in self.threads:
            worker.join(timeout=3)

    def request(self, path, body=None, headers=None):
        raw = json.dumps(body).encode() if body is not None else None
        request = Request(f"http://127.0.0.1:{self.portal.server_port}{path}", data=raw,
            headers={**({"Content-Type":"application/json"} if raw else {}), **(headers or {})})
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        except URLError as error:
            if isinstance(error.reason, PermissionError):
                self.skipTest("local sandbox denies loopback connections")
            raise
        with response:
            return response.status, dict(response.headers), json.loads(response.read())

    def test_cold_history_reconstruction_keeps_earth_between_tm_times(self):
        now = int(time.time()) // 60 * 60
        selected = now - 3600
        stamp = lambda second: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(second))
        fds = self.folder / "fds-reconstruction"
        fds.mkdir()
        samples = []
        for second in range(selected - 60, now + 1, 60):
            angle = (second-selected)*2*math.pi/5700
            samples.append([stamp(second),6878137*math.cos(angle),6878137*math.sin(angle),0.,1.,0.,0.])
        document = {"schema":"pado.orbit-reconstruction.v1", "quality":"RECONSTRUCTED_NOT_MEASURED",
            "norad_id":99999,"source":"TLE","input_epoch_utc":stamp(now-2),
            "generated_utc":stamp(now),"start_utc":samples[0][0],"end_utc":samples[-1][0],
            "sample_step_s":60,"samples":samples}
        (fds / "orbit_reconstruction.json").write_text(json.dumps(document), encoding="utf-8")
        self.portal.synthetic_orbit_view = fds / "orbit_view.json"
        status, headers, _ = self.request("/api/login", {"username":"planner01","password":self.run_password})
        self.assertEqual(status,200)
        status, _, orbit = self.request("/api/fds/orbit-history?target=synthetic&"+
            f"start_utc_s={selected-60}&end_utc_s={now}",
            headers={"Cookie":headers["Set-Cookie"].split(";",1)[0]})
        self.assertEqual(status,200)
        self.assertEqual(orbit["state"],"WAITING")
        self.assertEqual(orbit["reconstruction_state"],"AVAILABLE")
        if shutil.which("node"):
            script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const input=JSON.parse(fs.readFileSync(0,'utf8')), scope={};vm.createContext(scope);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),scope);
const cache=new scope.rosStateHistory.Cache();cache.setOrbit(input.orbit);
for(const offset of [1,15,29,59,75,119]){
  const state=cache.resolve((input.selected+offset)*1000);
  assert.equal(state.positionSource,'FDS_RECONSTRUCTED_TLE');
  assert.equal(state.attitudeStatus,'UNKNOWN');
  const scene=scope.rosAttitudeMath.computeState(state);
  assert.ok(scene.positionKnown && scene.earthBody && scene.sunBody);
  assert.equal(scene.orientationKnown,false);
}
'''
            process = subprocess.run([shutil.which("node"),"-e",script,
                str(ROOT / "web/ops/state_history.js"),str(ROOT / "web/ops/attitude_math.js")],
                input=json.dumps({"selected":selected,"orbit":orbit}),text=True,
                capture_output=True,timeout=20)
            self.assertEqual(process.returncode,0,process.stdout+process.stderr)

    def test_two_day_late_attitude_fills_past_gap_without_changing_measurement_time(self):
        now = int(time.time())
        selected = now - 2 * 86400
        query = f"start_utc_s={selected-60}&end_utc_s={selected+60}&limit=1000"
        self.assertEqual(self.request("/api/ros/state-history?" + query)[0], 401)
        status, headers, _ = self.request("/api/login", {
            "username":"planner01", "password":self.run_password})
        self.assertEqual(status, 200)
        auth = {"Cookie":headers["Set-Cookie"].split(";",1)[0]}
        fds = self.folder / "fds"
        fds.mkdir()
        stamp = lambda second: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(second))
        rows = []
        for second in range(selected-60, now+121, 60):
            angle = (second-selected)*2*math.pi/5700
            rows.append([stamp(second),6878137*math.cos(angle),6878137*math.sin(angle),0.,
                         0.,0.,500000.,1.,0.,0.])
        product = {"schema":"fds.orbit-view.v1","quality":"PRELIMINARY_NOT_FOR_OPERATIONS",
            "source":"TLE","norad_id":99999,"input_epoch_utc":stamp(selected-60),
            "start_utc":rows[0][0],"end_utc":rows[-1][0],"sample_step_s":60,"samples":rows}
        record_history(product, fds, now_s=selected)
        self.portal.synthetic_orbit_view = fds / "orbit_view.json"
        status, _, orbit = self.request("/api/fds/orbit-history?"+
            f"target=synthetic&start_utc_s={selected-60}&end_utc_s={selected+60}", headers=auth)
        self.assertEqual(status, 200)
        self.assertGreater(len(orbit["samples"]), 1)
        position = encode_tm("GNSS_PVT", 1, selected*1000, {
            "norad_id":99999, "solution_status":1, "receiver_age_ms":0,
            "x_m":6878137., "y_m":0., "z_m":0., "vx_mps":0., "vy_mps":7600., "vz_mps":0.})
        self.receiver.ingest(position, received_ms=now*1000, historical=True)
        status, _, before = self.request("/api/ros/state-history?"+query, headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(len(before["positions"]), 1)
        self.assertEqual(before["attitudes"], [])
        half = math.sqrt(.5)
        attitude = encode_tm("AOCS_STATE", 2, selected*1000, {
            "adcs_state":1,"control_mode":1,"boot_index":1,"sram_latchup_count":0,"edac_error_count":0,
            "q_w":half,"q_x":0.,"q_y":0.,"q_z":half,
            "body_rate_x_rad_s":0.,"body_rate_y_rad_s":0.,"body_rate_z_rad_s":0.})
        self.receiver.ingest(attitude, received_ms=int(time.time()*1000), historical=True)
        status, _, after = self.request("/api/ros/state-history?"+query+
            f"&after_id={before['next_after_id']}", headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(after["positions"], [])
        self.assertEqual(len(after["attitudes"]), 1)
        record = after["attitudes"][0]
        self.assertGreater(record["id"], before["revision"])
        self.assertEqual(record["utc"], before["positions"][0]["utc"])
        self.assertNotEqual(record["utc"], record["received_utc"])
        self.assertEqual(record["frame"], "SIM_BODY_TO_ITRF")
        self.assertEqual(len(list((self.folder / "n11").glob("*.manifest.json"))), 0,
                         "old GNSS backfill must not be presented as a current FDS fix")
        if shutil.which("node"):
            process = subprocess.run([shutil.which("node"), "-e", NODE_FLOW,
                str(ROOT / "web/ops/state_history.js"), str(ROOT / "web/ops/attitude_math.js")],
                input=json.dumps({"at":selected,"before":before,"after":after,"orbit":orbit}),
                text=True, capture_output=True, timeout=20)
            self.assertEqual(process.returncode, 0, process.stdout+process.stderr)


if __name__ == "__main__":
    unittest.main()
