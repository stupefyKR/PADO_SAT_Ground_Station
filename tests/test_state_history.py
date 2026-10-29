"""Historical state uses measurement time, late receipt cursors and explicit gaps."""

import hashlib
import json
import secrets
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError

from services.sim_ground.archive import PacketArchive
from services.webapp.state_history import StateHistoryError, history_query, state_history
from tests import test_sim_tc_web as http_fixture
from tools.orbit_history import HISTORY_NAME, OrbitHistoryError, read_history, record_history
from tools.pado_packet_codec import decode, encode_tm


def utc(seconds):
    return datetime.fromtimestamp(seconds,timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")


def tm(name, stamp, sequence=1, **updates):
    if name=="GNSS_PVT":
        fields={"norad_id":99999,"solution_status":1,"receiver_age_ms":0,
            "x_m":6878137.01234567,"y_m":123.123456789,"z_m":10.123456789,
            "vx_mps":-0.123456789,"vy_mps":7512.123456789,"vz_mps":30.123456789}
    elif name=="AOCS_STATE":
        fields={"adcs_state":1,"control_mode":1,"boot_index":1,"sram_latchup_count":0,
            "edac_error_count":0,"q_w":1.,"q_x":0.,"q_y":0.,"q_z":0.,
            "body_rate_x_rad_s":0.,"body_rate_y_rad_s":0.,"body_rate_z_rad_s":0.}
    else:
        fields={"channel_enabled_bits":1,"latchup_bits":0,"bus_voltage_v":5.,
            "temperature_c":20.,"uptime_s":100}
    fields.update(updates)
    return encode_tm(name,sequence,stamp*1000,fields)


def orbit(start,end,*,source="TLE",epoch=None,y=0):
    return {"schema":"fds.orbit-view.v1","quality":"PRELIMINARY_NOT_FOR_OPERATIONS",
        "norad_id":99999,"source":source,"input_epoch_utc":utc(start if epoch is None else epoch),
        "start_utc":utc(start),"end_utc":utc(end),"sample_step_s":60,
        "input_sha256":"a"*64,
        "samples":[[utc(t),6878137.,y,0.,0.,0.,500000.,1.,0.,0.]
                   for t in range(start,end+1,60)]}


class StateHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.path=self.folder/"received_tm.sqlite3"
        self.archive=PacketArchive(self.path)
        self.addCleanup(self.archive.close)
        self.now=int(time.time())
        self.window={"start_utc_s":self.now-259200,"end_utc_s":self.now,"now_s":self.now}

    def append(self,name,offset,sequence=1,**fields):
        raw=tm(name,self.now+offset,sequence,**fields)
        return self.archive.append(raw,decode(raw),self.now*1000)

    def test_late_attitude_backfills_its_measurement_time_with_new_receipt_cursor(self):
        self.append("GNSS_PVT",-5)
        first=state_history(self.path,**self.window)
        self.assertEqual(len(first["positions"]),1)
        self.assertEqual(first["attitudes"],[])
        self.append("AOCS_STATE",-1800)
        later=state_history(self.path,after_id=first["next_after_id"],**self.window)
        self.assertEqual(later["positions"],[])
        self.assertEqual(later["attitudes"][0]["utc"],utc(self.now-1800))
        self.assertEqual(later["attitudes"][0]["received_utc"],utc(self.now))
        self.assertTrue(later["attitudes"][0]["valid"])
        self.assertGreater(later["revision"],first["revision"])

    def test_invalid_attitude_and_gnss_are_timestamped_gap_markers(self):
        self.append("AOCS_STATE",-15)
        self.append("AOCS_STATE",-10,sequence=2,q_w=0.)
        self.append("AOCS_STATE",-5,sequence=3)
        self.append("GNSS_PVT",-10,solution_status=0)
        result=state_history(self.path,**self.window)
        self.assertEqual([x["valid"] for x in result["attitudes"]],[True,False,True])
        self.assertIsNone(result["attitudes"][1]["quaternion_wxyz"])
        self.assertEqual(result["attitudes"][1]["utc"],utc(self.now-10))
        self.assertFalse(result["positions"][0]["valid"])

    def test_pagination_high_watermark_and_unrelated_packets_do_not_hide_late_data(self):
        self.append("GNSS_PVT",-20)
        self.append("AOCS_STATE",-20)
        self.append("EPS_PDU1",-10)
        first=state_history(self.path,limit=1,**self.window)
        self.assertTrue(first["has_more"])
        second=state_history(self.path,after_id=first["next_after_id"],limit=1,**self.window)
        self.assertFalse(second["has_more"])
        self.assertEqual(second["next_after_id"],3)
        self.append("AOCS_STATE",-120,sequence=4)
        third=state_history(self.path,after_id=3,**self.window)
        self.assertEqual(third["attitudes"][0]["utc"],utc(self.now-120))

    def test_retention_query_excludes_older_and_future_measurements(self):
        self.append("AOCS_STATE",-259201)
        self.append("AOCS_STATE",-259200,sequence=2)
        self.append("AOCS_STATE",-1,sequence=3)
        result=state_history(self.path,**self.window)
        self.assertEqual(len(result["attitudes"]),2)
        self.assertEqual(result["attitudes"][0]["utc"],utc(self.now-259200))

    def test_follow_cursor_defers_records_newer_than_the_captured_request_end(self):
        self.append("GNSS_PVT",-10)
        newer=self.append("AOCS_STATE",-1)
        self.append("EPS_PDU1",-1)
        request={**self.window,"end_utc_s":self.now-5,"follow":1}
        first=state_history(self.path,**request)
        self.assertEqual(first["revision"],3)
        self.assertEqual(first["next_after_id"],newer["id"]-1)
        self.assertTrue(first["deferred"])
        self.assertFalse(first["has_more"])
        self.assertEqual(first["attitudes"],[])
        later=state_history(self.path,after_id=first["next_after_id"],follow=1,**self.window)
        self.assertEqual([row["id"] for row in later["attitudes"]],[newer["id"]])
        self.assertFalse(later["deferred"])
        self.assertEqual(later["next_after_id"],3)

    def test_follow_no_progress_page_retries_after_window_advance(self):
        self.append("AOCS_STATE",-1)
        self.append("AOCS_STATE",-30,sequence=2)
        request={**self.window,"end_utc_s":self.now-5,"follow":1}
        first=state_history(self.path,**request)
        self.assertEqual(first["next_after_id"],0)
        self.assertTrue(first["deferred"])
        self.assertFalse(first["has_more"])
        # This older sample was received after the too-new first receipt. It
        # is also retained and becomes visible when the rolling end catches up.
        later=state_history(self.path,after_id=0,follow=1,**self.window)
        self.assertEqual([row["id"] for row in later["attitudes"]],[1,2])

    def test_fixed_historical_window_can_page_past_newer_outside_measurements(self):
        self.append("AOCS_STATE",-1)
        late=self.append("AOCS_STATE",-1800,sequence=2)
        request={**self.window,"end_utc_s":self.now-600}
        first=state_history(self.path,**request)
        self.assertEqual([row["id"] for row in first["attitudes"]],[late["id"]])
        self.assertEqual(first["next_after_id"],2)
        self.assertFalse(first["deferred"])

    def test_corrupt_raw_breaks_attitude_coverage_instead_of_becoming_valid(self):
        self.append("AOCS_STATE",-10)
        connection=sqlite3.connect(self.path)
        with connection:
            connection.execute("UPDATE packets SET raw=?",(b"bad",))
        connection.close()
        item=state_history(self.path,**self.window)["attitudes"][0]
        self.assertFalse(item["valid"])
        self.assertEqual(item["reason"],"corrupt_packet")

    def test_missing_and_replaced_archives_are_explicit(self):
        absent=state_history(None,**self.window)
        self.assertEqual(absent["state"],"WAITING")
        reset=state_history(self.path,after_id=10,**self.window)
        self.assertEqual(reset["state"],"RESET_REQUIRED")
        self.assertEqual(reset["next_after_id"],0)

    def test_query_is_strict_and_bounded(self):
        valid=history_query("after_id=0&limit=1000",now_s=self.now)
        self.assertEqual(valid["start_utc_s"],self.now-259200)
        self.assertEqual(history_query("follow=1",now_s=self.now)["follow"],1)
        self.assertEqual(valid["follow"],0)
        for query in ("after_id=0&after_id=1","limit=1001","limit=-1","path=/tmp/x","follow=2",
            f"start_utc_s={self.now-259201}&end_utc_s={self.now}",
            f"end_utc_s={self.now+61}"):
            with self.subTest(query=query),self.assertRaises(StateHistoryError):
                history_query(query,now_s=self.now)
        with self.assertRaises(StateHistoryError):
            history_query(f"start_utc_s={self.now+5}&end_utc_s={self.now+10}",now_s=self.now)

    def test_maximum_page_is_bounded_by_serialized_bytes(self):
        records=[]
        for number in range(1,1001):
            raw=tm("GNSS_PVT",self.now-1,number)
            records.append((hashlib.sha256(raw).hexdigest(),"GNSS_PVT",8225,number,
                (self.now-1)*1000,self.now*1000,raw,"{}"))
        connection=sqlite3.connect(self.path)
        with connection:
            connection.executemany("INSERT INTO packets(sha256,name,packet_id,sequence,"
                "sampled_ms,received_ms,raw,fields_json) VALUES (?,?,?,?,?,?,?,?)",records)
        connection.close()
        result=state_history(self.path,limit=1000,**self.window)
        self.assertLessEqual(len(json.dumps(result,separators=(",",":")).encode()),256*1024)
        self.assertGreater(len(result["positions"]),100)
        self.assertTrue(result["has_more"])


class OrbitHistoryTests(unittest.TestCase):
    def setUp(self):
        scratch=Path(__file__).resolve().parents[1]/"out"
        scratch.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.now=int(time.time())//60*60
        self.path=self.folder/HISTORY_NAME

    def read(self):
        return read_history(self.path,start_utc_s=self.now-259200,end_utc_s=self.now,
                            norad_id=99999,now_s=self.now)

    def test_future_refresh_does_not_erase_old_predicted_positions(self):
        record_history(orbit(self.now-3600,self.now+3600),self.folder,now_s=self.now)
        record_history(orbit(self.now-1200,self.now+7200,source="GNSS",y=1000),
                       self.folder,now_s=self.now)
        result=self.read()
        by_time={row[0]:row for row in result["samples"]}
        self.assertEqual(by_time[utc(self.now-2400)][7],"TLE")
        self.assertEqual(by_time[utc(self.now-600)][7],"GNSS")
        self.assertEqual(by_time[utc(self.now-600)][2],1000)
        self.assertEqual(result["revision"],2)

    def test_invalid_product_is_rejected_atomically(self):
        good=orbit(self.now-3600,self.now+3600)
        record_history(good,self.folder,now_s=self.now)
        bad=orbit(self.now-3600,self.now+3600)
        bad["samples"][3][7]=0
        with self.assertRaises(OrbitHistoryError):
            record_history(bad,self.folder,now_s=self.now)
        self.assertEqual(self.read()["revision"],1)

    def test_canonical_grid_is_bounded_across_unaligned_refreshes_and_reopen(self):
        for offset in (0,901,1802,2703):
            start=self.now-3600+offset
            record_history(orbit(start,start+259200),self.folder,now_s=self.now)
        result=self.read()
        self.assertLessEqual(len(result["samples"]),5000)
        self.assertEqual(result["revision"],4)
        for row in result["samples"]:
            self.assertEqual(len(row),9)
            self.assertGreaterEqual(datetime.fromisoformat(row[0].replace("Z","+00:00")),
                                    datetime.fromisoformat(row[8].replace("Z","+00:00")))

    def test_past_retention_and_missing_database_are_explicit(self):
        old=self.now-280000
        record_history(orbit(old,old+3600),self.folder,now_s=old)
        record_history(orbit(self.now-60,self.now+3600),self.folder,now_s=self.now)
        self.assertFalse(any(row[0]==utc(old) for row in self.read()["samples"]))
        self.assertEqual(read_history(None,start_utc_s=self.now-60,end_utc_s=self.now,
            norad_id=99999,now_s=self.now)["state"],"WAITING")

    def test_revision_read_is_locked_against_an_independent_publisher(self):
        payload=orbit(self.now-3600,self.now+3600)
        record_history(payload,self.folder,now_s=self.now)
        original_connect=sqlite3.connect
        observed=[]
        path=self.path

        class ObservedCursor:
            def __init__(self,cursor):
                self.cursor=cursor

            def fetchone(self):
                row=self.cursor.fetchone()
                probe=original_connect(path,timeout=0)
                try:
                    try:
                        probe.execute("BEGIN IMMEDIATE")
                        observed.append("unlocked")
                    except sqlite3.OperationalError as error:
                        observed.append(str(error))
                finally:
                    probe.rollback()
                    probe.close()
                return row

        class ObservedConnection:
            def __init__(self,connection):
                self.connection=connection

            def __enter__(self):
                self.connection.__enter__()
                return self

            def __exit__(self,*args):
                return self.connection.__exit__(*args)

            def execute(self,sql,*args):
                cursor=self.connection.execute(sql,*args)
                return ObservedCursor(cursor) if sql.startswith("SELECT revision FROM meta") else cursor

            def __getattr__(self,name):
                return getattr(self.connection,name)

        with patch("tools.orbit_history.sqlite3.connect",side_effect=lambda *args,**kwargs:
                   ObservedConnection(original_connect(*args,**kwargs))):
            record_history(payload,self.folder,now_s=self.now)
        self.assertEqual(observed,["database is locked"])
        record_history(orbit(self.now-3600,self.now+3600,source="GNSS",y=2000),
                       self.folder,now_s=self.now)
        self.assertEqual(self.read()["revision"],3)


class StateHistoryHttpTests(unittest.TestCase):
    tearDown=http_fixture.SimTcHttpTests.tearDown
    request=http_fixture.SimTcHttpTests.request

    def setUp(self):
        http_fixture.SimTcHttpTests.setUp(self)
        self.ros.tm_preview=self.folder/"test_tm_snapshot.json"
        self.auth.create_user("history01","auditor",(password:=secrets.token_urlsafe(24)))
        try:
            status,headers,body=self.request("/api/login",body={"username":"history01","password":password})
        except URLError as error:
            if isinstance(error.reason,PermissionError):
                self.tearDown()
                self.skipTest("local sandbox denies loopback connections")
            raise
        self.assertEqual(status,200)
        self.headers={"Cookie":headers["Set-Cookie"].split(";",1)[0]}

    def test_authenticated_missing_tm_and_fds_history_are_waiting_not_fabricated(self):
        for path in ("/api/ros/state-history","/api/fds/orbit-history?target=synthetic"):
            self.assertEqual(self.request(path)[0],401)
            status,_,payload=self.request(path,headers=self.headers)
            self.assertEqual(status,200)
            self.assertEqual(payload["state"],"WAITING")

    def test_proxy_returns_real_measurement_time_and_rejects_arbitrary_queries(self):
        now=int(time.time())
        archive=PacketArchive(self.folder/"received_tm.sqlite3")
        raw=tm("AOCS_STATE",now-30)
        archive.append(raw,decode(raw),now*1000)
        archive.close()
        status,_,payload=self.request("/api/ros/state-history?after_id=0&limit=1",headers=self.headers)
        self.assertEqual(status,200)
        self.assertEqual(payload["attitudes"][0]["utc"],utc(now-30))
        self.assertEqual(self.request("/api/ros/state-history?after_id=0&after_id=1",headers=self.headers)[0],400)

    def test_portal_reads_only_the_configured_fds_history(self):
        folder=self.folder/"fds"
        folder.mkdir()
        now=int(time.time())//60*60
        record_history(orbit(now-60,now+3600),folder,now_s=now)
        self.portal.synthetic_orbit_view=folder/"orbit_view.json"
        status,_,payload=self.request("/api/fds/orbit-history?target=synthetic",headers=self.headers)
        self.assertEqual(status,200)
        self.assertEqual(payload["norad_id"],99999)
        self.assertTrue(payload["samples"])
        self.assertEqual(self.request("/api/fds/orbit-history?target=synthetic&path=/tmp/x",headers=self.headers)[0],400)

    def test_rolling_follow_cursor_is_preserved_through_the_authenticated_proxy(self):
        now=int(time.time())
        archive=PacketArchive(self.folder/"received_tm.sqlite3")
        raw=tm("AOCS_STATE",now-1)
        archive.append(raw,decode(raw),now*1000)
        archive.close()
        query=f"start_utc_s={now-3600}&end_utc_s={now-5}&after_id=0&limit=1&follow=1"
        status,_,first=self.request("/api/ros/state-history?"+query,headers=self.headers)
        self.assertEqual(status,200)
        self.assertEqual(first["next_after_id"],0)
        self.assertTrue(first["deferred"])
        status,_,later=self.request(f"/api/ros/state-history?after_id={first['next_after_id']}&follow=1",
                                    headers=self.headers)
        self.assertEqual(status,200)
        self.assertEqual(len(later["attitudes"]),1)


if __name__=="__main__":
    unittest.main()
