"""DGS observation journal. Expected outcomes never decide packet acceptance."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading

from services.sim_ground.archive import json_fields
from tools.pado_packet_codec import decode, PacketError
from tools.preview_draft_tm import build_snapshot, publish_snapshot, MAX_SIM_HISTORY_PDUS
from .corpus import generate


def run_key(seed, base):
    return f"{seed:08x}-{base:016x}"


def parse_tm(packet, *, catalog=None,received_ms=None):
    value = decode(packet, **({"catalog":catalog} if catalog else {}))
    if value["kind"] != "TM":
        raise PacketError("TM packet required on downlink")
    if received_ms is not None and (value['utc_ms']%1000 or not 0<=received_ms//1000*1000-value['utc_ms']<=72*3600000):
        raise PacketError('measurement UTC outside historical receive window')
    from services.sim_ground.measurement import validate_measurement
    validate_measurement(value)
    return value


class Recorder:
    def __init__(self, path: Path, *, limits: dict):
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("regular validation journal required")
        self.path, self.limits = path, limits
        path.parent.mkdir(parents=True,exist_ok=True)
        self.db = sqlite3.connect(path,check_same_thread=False,timeout=5)
        os.chmod(path,0o600)
        self.db.execute("PRAGMA synchronous=FULL")
        page_size=self.db.execute('PRAGMA page_size').fetchone()[0]
        self.db.execute(f'PRAGMA max_page_count={128*1024*1024//page_size}')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS runs(key TEXT PRIMARY KEY,seed INTEGER,base INTEGER,count INTEGER);
          CREATE TABLE IF NOT EXISTS expected(run TEXT,sha TEXT,case_id INTEGER,category TEXT,
            name TEXT,mutation TEXT,fields TEXT,spikes TEXT,PRIMARY KEY(run,sha));
          CREATE TABLE IF NOT EXISTS received(run TEXT,sha TEXT,raw BLOB,name TEXT,fields TEXT,
            error TEXT,received_ms INTEGER,sampled_ms INTEGER,PRIMARY KEY(run,sha));
          CREATE TABLE IF NOT EXISTS sensor_events(run TEXT,sha TEXT,sensor TEXT,kind TEXT,
            sampled_ms INTEGER,value REAL,lower_value REAL,upper_value REAL,PRIMARY KEY(run,sha,sensor,kind));
          CREATE INDEX IF NOT EXISTS received_measurement ON received(run,name,sampled_ms);
          CREATE TABLE IF NOT EXISTS unexpected(run TEXT,sha TEXT,raw BLOB,error TEXT,received_ms INTEGER,
            PRIMARY KEY(run,sha));
        ''')
        self.db.commit()
        self.lock = threading.RLock()
        self.active = None
        self.active_count = 0
        self.active_total = 0

    def close(self):
        with self.lock:self.db.close()

    def begin(self, seed, base, count, *, limits=None):
        if limits is not None:self.limits=limits
        key = run_key(seed,base)
        cases = generate(seed,base,count,limits=self.limits)
        with self.lock,self.db:
            if self.db.execute("SELECT 1 FROM runs WHERE key=?",(key,)).fetchone():
                if self.db.execute("SELECT COUNT(*) FROM received WHERE run=?",(key,)).fetchone()[0]:
                    raise ValueError("validation run already received; schedule a new run")
            elif self.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]>=20:
                raise ValueError("validation journal run limit reached; export and archive results")
            self.db.execute("INSERT OR IGNORE INTO runs VALUES(?,?,?,?)",(key,seed,base,count))
            self.db.executemany("INSERT OR IGNORE INTO expected VALUES(?,?,?,?,?,?,?,?)",[
                (key,expected["sha256"],case.index,case.category,case.name,case.mutation,
                 json.dumps(case.expected_fields,sort_keys=True,separators=(",",":")),
                 json.dumps(case.spike_keys)) for case in cases for expected in [case.expected()]])
            self.active = key
            self.active_count = 0
            self.active_total = count
        return key

    def recognizes(self, packet):
        if not self.active:return False
        sha=hashlib.sha256(packet).hexdigest()
        with self.lock:
            return bool(self.db.execute("SELECT 1 FROM expected WHERE run=? AND sha=?",(self.active,sha)).fetchone())

    def receive(self, packet, received_ms):
        if not self.active or not isinstance(packet,bytes) or len(packet)>512:
            raise ValueError("bounded packet and active validation run required")
        # Decode before consulting the expected result. The parser sees only
        # the bytes that arrived on the existing satellite/DGS TCP stream.
        parsed,error=None,None
        try:parsed=parse_tm(packet,received_ms=received_ms)
        except (PacketError,ValueError) as exc:error=str(exc)
        sha=hashlib.sha256(packet).hexdigest()
        with self.lock,self.db:
            if not self.db.execute("SELECT 1 FROM expected WHERE run=? AND sha=?",(self.active,sha)).fetchone():
                return None
            changed=self.db.execute("INSERT OR IGNORE INTO received VALUES(?,?,?,?,?,?,?,?)",(
                self.active,sha,packet,parsed["name"] if parsed else None,
                json.dumps(json_fields(parsed["fields"]),sort_keys=True,separators=(",",":")) if parsed else None,
                error,received_ms,parsed["utc_ms"] if parsed else None)).rowcount
            self.active_count += changed
            if parsed:
                for key,rule in self.limits["sensors"].items():
                    family,field=key.split(".")
                    if family!=parsed["name"]:continue
                    value=parsed["fields"].get(field)
                    if type(value) not in (int,float):continue
                    if value<rule["lower"] or value>rule["upper"]:
                        self.db.execute("INSERT OR IGNORE INTO sensor_events VALUES(?,?,?,?,?,?,?,?)",(
                            self.active,sha,key,"OUT_OF_RANGE",parsed["utc_ms"],value,rule["lower"],rule["upper"]))
        return parsed

    def unexpected(self,packet,received_ms,error):
        if not self.active:return
        with self.lock,self.db:
            if self.db.execute('SELECT COUNT(*) FROM unexpected WHERE run=?',(self.active,)).fetchone()[0]>=1000:
                raise ValueError('unexpected validation packet limit reached')
            self.db.execute('INSERT OR IGNORE INTO unexpected VALUES(?,?,?,?,?)',
                (self.active,hashlib.sha256(packet).hexdigest(),packet,str(error)[:300],received_ms))

    def project(self, snapshot: Path, received_ms: int):
        with self.lock:
            rows=self.db.execute("SELECT raw FROM received WHERE run=? AND error IS NULL "
                "AND name IN (%s) ORDER BY sampled_ms" % ','.join('?' for _ in self.limits['sensors']),
                (self.active,*[key.split('.')[0] for key in self.limits['sensors']])).fetchall()
            attitudes=self.db.execute("SELECT raw FROM received WHERE run=? AND name='AOCS_STATE' "
                "AND error IS NULL ORDER BY sampled_ms DESC LIMIT 1",(self.active,)).fetchall()
        packets=list(dict.fromkeys(bytes(row[0]) for row in rows+attitudes))
        if packets:
            preview=build_snapshot(packets,self.limits,received_ms=received_ms//1000*1000,max_packets=MAX_SIM_HISTORY_PDUS)
            if preview.get('attitude'):preview['attitude']['frame']='SIM_BODY_TO_ITRF'
            publish_snapshot(preview,snapshot)


def _read(path):
    if path is None or not path.exists():return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size>128*1024*1024:
        raise ValueError("invalid validation journal")
    connection=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
    connection.row_factory=sqlite3.Row
    return connection


def report(path: Path | None,key: str):
    db=_read(path)
    if db is None:return {"state":"WAITING","received":0}
    try:
        run=db.execute("SELECT * FROM runs WHERE key=?",(key,)).fetchone()
        if run is None:return {"state":"WAITING","received":0}
        rows=db.execute("SELECT e.*,r.sha AS actual_sha,r.name AS actual_name,r.fields AS actual_fields,"
            "r.error FROM expected e LEFT JOIN received r ON e.run=r.run AND e.sha=r.sha WHERE e.run=?",(key,)).fetchall()
        received=sum(row['actual_sha'] is not None for row in rows)
        false_accept=[r['case_id'] for r in rows if r['actual_sha'] and r['category']=='malformed' and r['error'] is None]
        false_reject=[r['case_id'] for r in rows if r['actual_sha'] and r['category']!='malformed' and r['error'] is not None]
        wrong_fields=[r['case_id'] for r in rows if r['actual_sha'] and r['category']!='malformed' and
            r['error'] is None and (r['fields']!=r['actual_fields'] or r['name']!=r['actual_name'])]
        missing_events=[]
        for row in rows:
            if row['actual_sha'] and row['category']=='spike':
                for sensor in json.loads(row['spikes']):
                    if not db.execute("SELECT 1 FROM sensor_events WHERE run=? AND sha=? AND sensor=?",
                        (key,row['sha'],sensor)).fetchone():missing_events.append(row['case_id'])
        unexpected=db.execute('SELECT COUNT(*) FROM unexpected WHERE run=?',(key,)).fetchone()[0]
        errors=len(false_accept)+len(false_reject)+len(wrong_fields)+len(missing_events)+unexpected
        return {"state":"PASS" if received==run['count'] and not errors else "FAIL" if received==run['count'] else "RECEIVING",
            "run_key":key,"expected":run['count'],"received":received,"missing":run['count']-received,
            "valid_parsed":sum(r['actual_sha'] is not None and r['error'] is None for r in rows),
            "malformed_rejected":sum(r['actual_sha'] is not None and r['error'] is not None for r in rows),
            "false_accept":false_accept[:50],"false_reject":false_reject[:50],
            "wrong_fields":wrong_fields[:50],"missing_spike_events":missing_events[:50],
            'error_counts':{'false_accept':len(false_accept),'false_reject':len(false_reject),
                'wrong_fields':len(wrong_fields),'missing_spike_events':len(missing_events)},
            "sensor_events":db.execute("SELECT COUNT(*) FROM sensor_events WHERE run=?",(key,)).fetchone()[0],
            'unexpected_packets':unexpected,
            "tm_types":sorted({r['name'] for r in rows}),
            "mutations":sorted({r['mutation'] for r in rows if r['mutation']})}
    finally:db.close()


def packets(path: Path | None,key: str,*,before=2147483647,limit=50):
    if type(before) is not int or type(limit) is not int or not 1<=limit<=100:raise ValueError('invalid query')
    db=_read(path)
    if db is None:return {'items':[]}
    try:
        rows=db.execute("SELECT e.case_id,e.category,e.mutation,e.name,r.raw,r.fields,r.error,r.received_ms "
            "FROM received r JOIN expected e ON e.run=r.run AND e.sha=r.sha WHERE r.run=? AND e.case_id<? "
            "ORDER BY e.case_id DESC LIMIT ?",(key,before,limit)).fetchall()
        return {'items':[{'case_id':r['case_id'],'category':r['category'],'mutation':r['mutation'],'name':r['name'],
            'raw_hex':bytes(r['raw']).hex(),'parsed':json.loads(r['fields']) if r['fields'] else None,
            'error':r['error'],'received_ms':r['received_ms']} for r in rows]}
    finally:db.close()


def events(path: Path | None,key: str,limit=100):
    db=_read(path)
    if db is None:return {'items':[]}
    try:
        rows=db.execute("SELECT * FROM sensor_events WHERE run=? ORDER BY sampled_ms DESC LIMIT ?",(key,min(100,int(limit)))).fetchall()
        return {'items':[dict(row) for row in rows]}
    finally:db.close()
