"""Explicit TLE reconstruction is bounded and cannot replace received history."""

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from services.webapp.auth import AuthStore
from services.webapp.main import AppServer, initialize_internal_token
from tests.test_sim_tc_web import FakeHasher
from tools.orbit_history import (HISTORY_NAME, RECONSTRUCTION_NAME, OrbitHistoryError,
    read_reconstruction, record_history)


ROOT = Path(__file__).resolve().parents[1]


def utc(seconds):
    return datetime.fromtimestamp(seconds,timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")


def reconstruction(now):
    return {"schema":"pado.orbit-reconstruction.v1", "quality":"RECONSTRUCTED_NOT_MEASURED",
        "norad_id":99999,"source":"TLE","input_epoch_utc":utc(now),
        "generated_utc":utc(now),"start_utc":utc(now-600),"end_utc":utc(now),
        "sample_step_s":60,"samples":[[utc(stamp),6878137,0,0,1,0,0]
            for stamp in range(now-600,now+1,60)]}


class ReconstructionReaderTests(unittest.TestCase):
    def setUp(self):
        (ROOT/"out").mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix="orbit-reconstruction-",dir=ROOT/"out")
        self.folder=Path(self.temp.name);self.path=self.folder/RECONSTRUCTION_NAME
        self.now=1793232000;self.payload=reconstruction(self.now)

    def tearDown(self):self.temp.cleanup()

    def write(self,payload):self.path.write_text(json.dumps(payload),encoding="utf-8")

    def read(self,**overrides):
        return read_reconstruction(self.path,start_utc_s=self.now-600,end_utc_s=self.now,
                                   norad_id=99999,now_s=self.now,**overrides)

    def test_missing_and_valid_backward_reconstruction_with_requested_brackets(self):
        self.assertIsNone(self.read())
        self.write(self.payload)
        result=self.read()
        self.assertEqual(result["schema"],"pado.orbit-reconstruction.v1")
        self.assertEqual(result["quality"],"RECONSTRUCTED_NOT_MEASURED")
        self.assertEqual(result["state"],"AVAILABLE");self.assertTrue(result["non_operational"])
        self.assertLess(result["samples"][0][0],result["input_epoch_utc"])
        subset=read_reconstruction(self.path,start_utc_s=self.now-300,end_utc_s=self.now-240,
                                   norad_id=99999,now_s=self.now)
        self.assertEqual([row[0] for row in subset["samples"]],
                         [utc(stamp) for stamp in range(self.now-420,self.now-119,60)])
        self.assertEqual(self.path.read_text(encoding="utf-8"),json.dumps(self.payload))

    def test_rejects_invalid_provenance_time_geometry_density_and_json(self):
        invalid=[]
        for key,value in (("quality","PRELIMINARY_NOT_FOR_OPERATIONS"),("source","GNSS"),
                ("norad_id",25544),("sample_step_s",True),("generated_utc",utc(self.now+2)),
                ("start_utc",utc(self.now-599)),("input_epoch_utc",utc(self.now+72*3600+601))):
            item=copy.deepcopy(self.payload);item[key]=value;invalid.append(item)
        item=copy.deepcopy(self.payload);item["samples"][2][0]=item["samples"][1][0];invalid.append(item)
        item=copy.deepcopy(self.payload);item["samples"]=item["samples"][:1]+item["samples"][4:];invalid.append(item)
        for index,value in ((1,float("nan")),(1,100),(4,2),(4,True)):
            item=copy.deepcopy(self.payload);item["samples"][0][index]=value;invalid.append(item)
        item=copy.deepcopy(self.payload);item["samples"][0].append(0);invalid.append(item)
        item=copy.deepcopy(self.payload);item["extra"]=True;invalid.append(item)
        for payload in invalid:
            with self.subTest(payload=payload):
                self.write(payload)
                with self.assertRaises(OrbitHistoryError):self.read()
        self.path.write_text('{"schema":"one","schema":"two"}',encoding="utf-8")
        with self.assertRaises(OrbitHistoryError):self.read()
        self.path.write_bytes(b"x"*(2*1024*1024+1))
        with self.assertRaises(OrbitHistoryError):self.read()

    def test_full_three_day_window_and_strict_regular_named_file(self):
        full=self.payload
        full["start_utc"]=utc(self.now-72*3600)
        full["samples"]=[[utc(stamp),6878137,0,0,1,0,0]
                         for stamp in range(self.now-72*3600,self.now+1,60)]
        self.write(full)
        result=read_reconstruction(self.path,start_utc_s=self.now-72*3600,end_utc_s=self.now,
                                   norad_id=99999,now_s=self.now)
        self.assertEqual(len(result["samples"]),4321)
        with self.assertRaises(OrbitHistoryError):
            read_reconstruction(self.folder/"other.json",start_utc_s=self.now-600,
                                end_utc_s=self.now,norad_id=99999,now_s=self.now)
        if os.name == "posix":
            self.path.unlink();self.path.symlink_to(self.folder/"missing")
            with self.assertRaises(OrbitHistoryError):self.read()

    def test_forward_product_can_start_two_seconds_after_epoch_without_archiving_stale_tail(self):
        start, epoch = self.now, self.now-2
        payload={"schema":"fds.orbit-view.v1","quality":"PRELIMINARY_NOT_FOR_OPERATIONS",
            "norad_id":99999,"source":"TLE","input_epoch_utc":utc(epoch),
            "samples":[[utc(stamp),6878137,0,0,0,0,0,1,0,0]
                       for stamp in range(start,start+72*3600+1,60)]}
        record_history(payload,self.folder,now_s=self.now)
        connection=sqlite3.connect(self.folder/HISTORY_NAME)
        try:
            count, newest, oldest = connection.execute(
                "SELECT COUNT(*),MAX(utc_ms),MIN(utc_ms) FROM samples").fetchone()
            self.assertGreater(count,4000)
            self.assertEqual(oldest,start*1000)
            self.assertLess(newest,(epoch+72*3600)*1000,
                            "recorded history retains the strict 72h input freshness bound")
        finally:connection.close()
        for mutation in ("overlong","stale_first","before_epoch"):
            bad=copy.deepcopy(payload)
            if mutation=="overlong":bad["samples"].append([utc(start+72*3600+60),6878137,0,0,0,0,0,1,0,0])
            elif mutation=="stale_first":bad["input_epoch_utc"]=utc(start-72*3600)
            else:bad["input_epoch_utc"]=utc(start+1)
            with self.subTest(mutation=mutation):
                with self.assertRaises(OrbitHistoryError):record_history(bad,self.folder,now_s=self.now)


class ReconstructionApiTests(unittest.TestCase):
    def setUp(self):
        (ROOT/"out").mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix="reconstruction-http-",dir=ROOT/"out")
        self.folder=Path(self.temp.name);self.now=int(time.time())
        token=self.folder/"token";initialize_internal_token(token)
        self.auth=AuthStore(self.folder/"users.db",FakeHasher())
        self.password=secrets.token_urlsafe(24);self.auth.create_user("planner01","planner",self.password)
        self.portal=AppServer(("127.0.0.1",0),mode="portal",transport="http",
            internal_token_file=token,auth=self.auth,synthetic_orbit_view=self.folder/"orbit_view.json")
        self.thread=threading.Thread(target=self.portal.serve_forever,daemon=True);self.thread.start()
        # The historical prediction remains the authoritative existing source.
        historic={"schema":"fds.orbit-view.v1","quality":"PRELIMINARY_NOT_FOR_OPERATIONS",
            "norad_id":99999,"source":"TLE","input_epoch_utc":utc(self.now-600),
            "samples":[[utc(stamp),6878137,0,0,0,0,0,1,0,0]
                       for stamp in range(self.now-600,self.now+1,60)]}
        record_history(historic,self.folder,now_s=self.now)

    def tearDown(self):
        self.portal.shutdown();self.portal.server_close();self.thread.join(timeout=2)
        self.auth.close();self.temp.cleanup()

    def request(self,path,body=None,headers=None):
        request=Request(f"http://127.0.0.1:{self.portal.server_port}{path}",
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type":"application/json",**(headers or {})})
        try:response=urlopen(request,timeout=5)
        except HTTPError as error:response=error
        except URLError as error:
            if isinstance(error.reason,PermissionError):self.skipTest("sandbox denies loopback")
            raise
        with response:return response.status,response.headers,json.load(response)

    def test_auth_optional_reconstruction_and_invalid_auxiliary_preserves_history(self):
        query=f"/api/fds/orbit-history?target=synthetic&start_utc_s={self.now-600}&end_utc_s={self.now}"
        self.assertEqual(self.request(query)[0],401)
        status,headers,_=self.request("/api/login",{"username":"planner01","password":self.password})
        self.assertEqual(status,200)
        cookie={"Cookie":headers["Set-Cookie"].split(";",1)[0]}
        status,_,before=self.request(query,headers=cookie)
        self.assertEqual(status,200);self.assertEqual(before["state"],"AVAILABLE")
        self.assertEqual(before["reconstruction_state"],"WAITING");self.assertIsNone(before["reconstruction"])
        path=self.folder/RECONSTRUCTION_NAME
        path.write_text(json.dumps(reconstruction(self.now)),encoding="utf-8")
        status,_,available=self.request(query,headers=cookie)
        self.assertEqual(status,200);self.assertEqual(available["reconstruction_state"],"AVAILABLE")
        self.assertEqual(available["samples"],before["samples"])
        self.assertEqual(available["reconstruction"]["quality"],"RECONSTRUCTED_NOT_MEASURED")
        path.write_text('{"schema":"broken"}',encoding="utf-8")
        status,_,invalid=self.request(query,headers=cookie)
        self.assertEqual(status,200);self.assertEqual(invalid["reconstruction_state"],"INVALID")
        self.assertIsNone(invalid["reconstruction"])
        self.assertEqual(invalid["samples"],before["samples"])
        self.assertEqual(invalid["state"],"AVAILABLE")
        # A reconstruction remains usable while the historical archive is empty.
        (self.folder/HISTORY_NAME).unlink()
        path.write_text(json.dumps(reconstruction(self.now)),encoding="utf-8")
        status,_,only=self.request(query,headers=cookie)
        self.assertEqual(status,200);self.assertEqual(only["state"],"WAITING")
        self.assertEqual(only["reconstruction_state"],"AVAILABLE")


if __name__ == "__main__":unittest.main()
