"""Shared monitoring bounds edits do not alter physical constants or TM times."""

import json
import os
import secrets
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from services.spacecraft_config import load_settings
from services.sim_ground.receiver import TmReceiver, load_limits
from services.webapp.auth import AuthStore
from services.webapp.main import AppServer, initialize_internal_token, test_n10_feedback
from services.webapp.settings import SettingsError, SettingsStore
from services.webapp.tm_preview import load_snapshot, validate_snapshot
from tests.test_sim_tc_web import FakeHasher


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config/spacecraft.example.json"
KEY = "EPS_P60.battery_voltage_v"


class BoundsFiles(unittest.TestCase):
    def setUp(self):
        (ROOT / "out").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="sensor-settings-", dir=ROOT / "out")
        self.folder = Path(self.temp.name)
        self.path = self.folder / "spacecraft.json"
        self.document = load_settings(EXAMPLE)
        self.path.write_text(json.dumps(self.document), encoding="utf-8")
        self.store = SettingsStore(self.path)
        self.snapshot = self.folder / "test_tm_snapshot.json"
        rule = self.document["sensor_limits"][KEY]
        self.sample = {"type":"TEST_ONLY_TM_SNAPSHOT", "non_operational":True,
            "received_utc":"2026-10-29T04:00:01Z", "attitude":None,
            "sensors":[{**rule, "samples":[{"utc":"2026-10-29T04:00:00Z", "value":14.0}]}]}
        self.snapshot.write_text(json.dumps(self.sample), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_atomic_cas_preserves_power_and_rejects_invalid_fields(self):
        original = self.path.read_bytes()
        version = self.store.view()["sha256"]
        for changes in ({KEY:{"lower":15,"upper":14}},
                        {KEY:{"lower":float("nan"),"upper":16}},
                        {KEY:{"lower":True,"upper":16}},
                        {"power": {"lower":0,"upper":1}},
                        {KEY:{"lower":11,"upper":16,"unit":"A"}}):
            with self.assertRaisesRegex(SettingsError,"invalid_settings"):
                self.store.save_limits(version, changes)
            self.assertEqual(self.path.read_bytes(), original)
        saved = self.store.save_limits(version,{KEY:{"lower":13,"upper":13.5}})
        self.assertNotEqual(saved["sha256"], version)
        self.assertEqual(load_settings(self.path)["power"],self.document["power"])
        with self.assertRaisesRegex(SettingsError,"settings_conflict"):
            self.store.save_limits(version,{KEY:{"lower":10,"upper":15}})
        current = self.path.read_bytes()
        with patch("services.webapp.settings.os.replace",side_effect=OSError("disk error")):
            with self.assertRaisesRegex(SettingsError,"settings_unavailable"):
                self.store.save_limits(saved["sha256"],{KEY:{"lower":10,"upper":15}})
        self.assertEqual(self.path.read_bytes(),current)
        self.assertFalse(list(self.folder.glob(".spacecraft-*.tmp")))
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777,0o664)
            self.assertEqual((self.folder/".spacecraft-settings.lock").stat().st_mode & 0o777,0o664)

    def test_existing_snapshot_and_n10_use_new_bounds_without_new_tm(self):
        before = self.snapshot.read_bytes()
        self.store.save_limits(self.store.view()["sha256"],{KEY:{"lower":13,"upper":13.5}})
        overlaid = load_snapshot(self.snapshot,limits=self.store.limits())
        self.assertEqual(overlaid["sensors"][0]["upper"],13.5)
        self.assertEqual(overlaid["received_utc"],self.sample["received_utc"])
        self.assertEqual(overlaid["sensors"][0]["samples"],self.sample["sensors"][0]["samples"])
        self.assertEqual(self.snapshot.read_bytes(),before)
        # Imported files retain their own explicit ranges.
        self.assertEqual(validate_snapshot(before)["sensors"][0]["upper"],
                         self.document["sensor_limits"][KEY]["upper"])
        class Drafts:
            def n10_test_ack_items(self):return []
        import datetime
        now = int(datetime.datetime(2026,10,29,4,0,2,tzinfo=datetime.timezone.utc).timestamp())
        feedback = test_n10_feedback(Drafts(),self.snapshot,now,limits=self.store.limits())
        self.assertEqual(feedback["bus_soh"]["sensors"][0]["limit_state"],"OUT_OF_RANGE")

    def test_receiver_reloads_ranges_keeps_history_and_last_valid_file(self):
        inbox = self.folder/"n11";inbox.mkdir()
        receiver = TmReceiver(self.snapshot,inbox,load_limits(self.path),limits_path=self.path)
        try:
            history = receiver.sensor_history["EPS_P60"]
            history.append((123,b"existing sample"))
            self.store.save_limits(self.store.view()["sha256"],{KEY:{"lower":12,"upper":15}})
            receiver._reload_limits()
            self.assertEqual(receiver.limits["sensors"][KEY]["upper"],15)
            self.assertIs(receiver.sensor_history["EPS_P60"],history)
            self.assertEqual(list(history),[(123,b"existing sample")])
            self.path.write_text('{"schema":"pado.spacecraft-settings.v1"}',encoding="utf-8")
            with patch("services.sim_ground.receiver.print") as logged:
                receiver._reload_limits(); receiver._reload_limits()
            self.assertEqual(logged.call_count,1)
            self.assertEqual(receiver.limits["sensors"][KEY]["upper"],15)
            legacy = self.folder/"legacy.json"
            legacy.write_text(json.dumps({"sensors":{KEY:self.document["sensor_limits"][KEY]}}),encoding="utf-8")
            self.assertEqual(len(load_limits(legacy)["sensors"]),1)
        finally:
            receiver.archive.close()


class BoundsHttp(unittest.TestCase):
    def setUp(self):
        BoundsFiles.setUp(self)
        self.token = self.folder/"token";initialize_internal_token(self.token)
        self.auth = AuthStore(self.folder/"auth.db",FakeHasher())
        self.password = secrets.token_urlsafe(24)
        for name,role in (("planner01","planner"),("auditor01","auditor")):
            self.auth.create_user(name,role,self.password)
        self.ros = AppServer(("127.0.0.1",0),mode="ros",transport="http",
            internal_token_file=self.token,draft_db=self.folder/"draft.db",
            settings_file=self.path,tm_preview=self.snapshot)
        self.portal = AppServer(("127.0.0.1",0),mode="portal",transport="http",
            internal_token_file=self.token,auth=self.auth,
            ros_url=f"http://127.0.0.1:{self.ros.server_port}")
        self.threads=[]
        for server in (self.ros,self.portal):
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start();self.threads.append(thread)

    def tearDown(self):
        for server in (self.portal,self.ros):server.shutdown();server.server_close()
        for thread in self.threads:thread.join(timeout=2)
        self.ros.drafts.close();self.auth.close();BoundsFiles.tearDown(self)

    def request(self,path,body=None,headers=None,backend=False):
        target=self.ros if backend else self.portal
        request=Request(f"http://127.0.0.1:{target.server_port}{path}",
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type":"application/json",**(headers or {})})
        try: response=urlopen(request,timeout=5)
        except HTTPError as error:response=error
        except URLError as error:
            if isinstance(error.reason,PermissionError):self.skipTest("sandbox denies loopback")
            raise
        with response:return response.status,response.headers,json.load(response)

    def test_http_auth_csrf_role_cas_and_immediate_snapshot(self):
        self.assertEqual(self.request("/api/ros/settings")[0],401)
        self.assertEqual(self.request("/settings",backend=True)[0],403)
        status,headers,login=self.request("/api/login",{"username":"planner01","password":self.password})
        self.assertEqual(status,200)
        cookie={"Cookie":headers["Set-Cookie"].split(";",1)[0]}
        authorized={**cookie,"X-CSRF-Token":login["csrf"]}
        status,_,current=self.request("/api/ros/settings",headers=cookie)
        self.assertEqual(status,200);self.assertEqual(len(current["sensors"]),20)
        change={"expected_sha256":current["sha256"],"limits":{KEY:{"lower":12,"upper":13}}}
        self.assertEqual(self.request("/api/ros/settings",change,cookie)[0],403)
        self.assertEqual(self.request("/api/ros/settings",{**change,"power":{}},authorized)[0],400)
        self.assertEqual(self.request("/api/ros/settings",change,authorized)[0],200)
        self.assertEqual(self.request("/api/ros/settings",change,authorized)[0],409)
        status,_,snapshot=self.request("/api/ros/test-tm-snapshot",headers=cookie)
        self.assertEqual(status,200);self.assertEqual(snapshot["sensors"][0]["upper"],13)
        self.assertEqual(snapshot["sensors"][0]["samples"][0]["utc"],"2026-10-29T04:00:00Z")
        _,headers,login=self.request("/api/login",{"username":"auditor01","password":self.password})
        auditor={"Cookie":headers["Set-Cookie"].split(";",1)[0],"X-CSRF-Token":login["csrf"]}
        self.assertEqual(self.request("/api/ros/settings",headers=auditor)[0],200)
        self.assertEqual(self.request("/api/ros/settings",change,auditor)[0],403)


if __name__ == "__main__":unittest.main()
