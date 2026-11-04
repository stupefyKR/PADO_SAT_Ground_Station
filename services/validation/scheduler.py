"""Durable validation sequencing through the existing ROS->DGS TC bridge."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time

from services.spacecraft_config import get_sensor_limits
from .corpus import COUNT, TRIGGER, tc_matrix
from .recorder import run_key, report


def atomic_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.'+secrets.token_hex(6)+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as target:
            json.dump(value,target,ensure_ascii=False,allow_nan=False)
            target.flush();os.fsync(target.fileno())
        os.chmod(temporary,0o644)
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def frozen_limits(control: Path, seed,base,count):
    path=control/(run_key(seed,base)+'.json')
    if path.is_symlink() or not path.is_file() or path.stat().st_size>128*1024:
        raise ValueError('validation run settings unavailable')
    value=json.loads(path.read_text(encoding='utf-8'))
    if (value.get('seed'),value.get('base_utc_ms'),value.get('count'))!=(seed,base,count):
        raise ValueError('validation run settings mismatch')
    limits=value['limits']
    if not isinstance(limits,dict) or set(limits)!={'sensors'}:raise ValueError('invalid frozen ranges')
    digest=hashlib.sha256(json.dumps(limits,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if value.get('limits_sha256',digest)!=digest:raise ValueError('frozen ranges checksum mismatch')
    return limits


class RunStore:
    def __init__(self,path:Path,control:Path,received:Path):
        if path.is_symlink() or path.parent.is_symlink() or control.is_symlink():
            raise ValueError('regular validation store and control directory required')
        self.path,self.control,self.received=path,control,received
        path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,check_same_thread=False,timeout=5)
        os.chmod(path,0o600)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS validation_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_key TEXT UNIQUE,seed INTEGER,base INTEGER,target INTEGER,status TEXT,
          actor TEXT,created INTEGER,coverage TEXT);
        CREATE TABLE IF NOT EXISTS validation_tc(run INTEGER,step INTEGER,name TEXT,variant TEXT,
          parameters TEXT,status TEXT,raw_hex TEXT,sent_ms INTEGER,ack TEXT,error TEXT,
          PRIMARY KEY(run,step));
        UPDATE validation_tc SET status='UNKNOWN',error='process restarted during transmission' WHERE status='SENDING';
        UPDATE validation_runs SET status='UNKNOWN' WHERE status='RUNNING';
        ''')
        self.db.commit();self.lock=threading.RLock()

    def close(self):
        with self.lock:self.db.close()

    def activate(self):
        atomic_json(self.control/'active.json',{'schema':'pado.validation-activation.v1','enabled':True,
            'deployment_id':os.environ.get('PADO_VALIDATION_DEPLOYMENT','local'),'activated_ms':int(time.time()*1000)})

    def schedule(self,actor:str,target:int,seed:int,*,now_s=None):
        now=int(time.time()) if now_s is None else now_s
        if type(target) is not int or not now+3<=target<=now+86400:
            raise ValueError('validation target must be 3 seconds to 24 hours in the future')
        if type(seed) is not int or not 0<=seed<=0xffffffff:raise ValueError('invalid seed')
        # All samples predate the scheduled transmission and retain their own
        # measurement UTC even though their wire arrival order is shuffled.
        base=(target-COUNT-5)*1000
        matrix=tc_matrix(target*1000)
        limits=get_sensor_limits()
        matrix['sensor_limits']=limits
        matrix['sensor_limits_sha256']=hashlib.sha256(json.dumps(limits,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        with self.lock,self.db:
            if self.db.execute("SELECT 1 FROM validation_runs WHERE status IN ('SCHEDULED','RUNNING')").fetchone():
                raise ValueError('a validation sequence is already scheduled or running')
            if self.db.execute('SELECT COUNT(*) FROM validation_runs').fetchone()[0]>=20:
                raise ValueError('20-run retention limit reached; export results before cleanup')
            previous=self.db.execute("SELECT run_key FROM validation_runs WHERE status IN ('TRANSMITTED','FAILED','UNKNOWN') ORDER BY id DESC LIMIT 1").fetchone()
            if previous and report(self.received,previous[0])['state'] not in ('PASS','FAIL'):
                raise ValueError('previous validation is incomplete; stop it before scheduling another run')
            key=run_key(seed,base)
            cursor=self.db.execute('INSERT INTO validation_runs(run_key,seed,base,target,status,actor,created,coverage) VALUES(?,?,?,?,?,?,?,?)',
                (key,seed,base,target,'SCHEDULED',actor,now,json.dumps(matrix)))
            ident=cursor.lastrowid
            cases=matrix['cases']+[{'name':TRIGGER,'variant':'TM corpus trigger',
                'parameters':{'seed':seed,'count':COUNT,'base_utc_ms':base}}]
            self.db.executemany('INSERT INTO validation_tc VALUES(?,?,?,?,?,?,?,?,?,?)',[
                (ident,index+1,row['name'],row['variant'],json.dumps(row['parameters']),'SCHEDULED',None,None,None,None)
                for index,row in enumerate(cases)])
            atomic_json(self.control/(key+'.json'),{'seed':seed,'base_utc_ms':base,'count':COUNT,
                'limits':limits,'limits_sha256':matrix['sensor_limits_sha256']})
        return self.get(ident)

    def get(self,ident):
        with self.lock:
            row=self.db.execute('SELECT * FROM validation_runs WHERE id=?',(ident,)).fetchone()
            if row is None:raise ValueError('unknown validation run')
            columns=[item[0] for item in self.db.execute('SELECT * FROM validation_runs LIMIT 0').description]
            result=dict(zip(columns,row))
            result['coverage']=json.loads(result['coverage'])
            steps=self.db.execute('SELECT step,name,variant,parameters,status,raw_hex,sent_ms,ack,error FROM validation_tc WHERE run=? ORDER BY step',(ident,)).fetchall()
            result['tc']=[dict(zip(('step','name','variant','parameters','status','raw_hex','sent_ms','ack','error'),step)) for step in steps]
            for step in result['tc']:
                step['parameters']=json.loads(step['parameters'])
                step['ack']=json.loads(step['ack']) if step['ack'] else None
        result['tm']=report(self.received,result['run_key'])
        from services.sim_ground.tc_journal import read
        wire={p['sha256']:p for p in read(self.received.with_name('transmitted_tc.sqlite3'),
            [hashlib.sha256(bytes.fromhex(s['raw_hex'])).hexdigest() for s in result['tc'] if s['raw_hex']])['items']}
        for step in result['tc']:
            observed=wire.get(hashlib.sha256(bytes.fromhex(step['raw_hex'])).hexdigest()) if step['raw_hex'] else None
            step['wire_status']=observed['status'] if observed else 'NOT_OBSERVED'
            step['wire_sent_ms']=observed['wire_ms'] if observed and observed['status']=='SENT' else None
        result['tc_summary']={'expected':len(result['tc']),'sent':sum(s['wire_sent_ms'] is not None for s in result['tc']),
            'acked':sum(s['status']=='ACKED' for s in result['tc']),
            'state':'PASS' if all(s['status']=='ACKED' and s['wire_sent_ms'] is not None for s in result['tc']) else
                'FAIL' if any(s['status'] in ('REJECTED','UNKNOWN') for s in result['tc']) else 'WAITING'}
        return result

    def list(self):
        with self.lock:
            rows=self.db.execute('SELECT id,run_key,seed,target,status,actor,created FROM validation_runs ORDER BY id DESC LIMIT 20').fetchall()
        return {'items':[dict(zip(('id','run_key','seed','target','status','actor','created'),row)) for row in rows]}

    def cancel(self,ident):
        with self.lock,self.db:
            row=self.db.execute('SELECT run_key,status FROM validation_runs WHERE id=?',(ident,)).fetchone()
            if row is None or row[1]=='CANCELLED':raise ValueError('unknown or already cancelled validation run')
            self.db.execute("UPDATE validation_runs SET status='CANCELLED' WHERE id=?",(ident,))
            self.db.execute("UPDATE validation_tc SET status='CANCELLED' WHERE run=? AND status='SCHEDULED'",(ident,))
            atomic_json(self.control/'abort.json',{'run_key':row[0]})

    def step(self,socket_path):
        """Called by the single existing transmitter, never a parallel uplink."""
        from services.webapp.sim_tc_dispatch import send_sim_tc
        now=int(time.time())
        with self.lock,self.db:
            run=self.db.execute("SELECT id,target FROM validation_runs WHERE status IN ('SCHEDULED','RUNNING') AND target<=? ORDER BY id LIMIT 1",(now,)).fetchone()
            if run is None:return False
            ident,target=run
            if now>target+1800:
                self.db.execute("UPDATE validation_runs SET status='EXPIRED' WHERE id=?",(ident,));return True
            if self.db.execute("SELECT 1 FROM validation_tc WHERE run=? AND status='SENDING'",(ident,)).fetchone():
                self.db.execute("UPDATE validation_runs SET status='FAILED' WHERE id=?",(ident,));return True
            row=self.db.execute("SELECT step,name,parameters FROM validation_tc WHERE run=? AND status='SCHEDULED' ORDER BY step LIMIT 1",(ident,)).fetchone()
            if row is None:
                self.db.execute("UPDATE validation_runs SET status='TRANSMITTED' WHERE id=?",(ident,));return True
            step,name,parameters=row
            self.db.execute("UPDATE validation_runs SET status='RUNNING' WHERE id=?",(ident,))
            self.db.execute("UPDATE validation_tc SET status='SENDING' WHERE run=? AND step=?",(ident,step))
        def capture(packet,phase):
            if phase=='ENCODED':
                atomic_json(self.control/'tc_intent.json',{'sha256':hashlib.sha256(packet).hexdigest()})
            with self.lock,self.db:
                if phase in ('ENCODED','SENT'):
                    self.db.execute('UPDATE validation_tc SET raw_hex=?,sent_ms=? WHERE run=? AND step=?',
                        (packet.hex(),int(time.time()*1000) if phase=='SENT' else None,ident,step))
        try:
            ack=send_sim_tc(socket_path,{'command_name':name,'parameters':json.loads(parameters),
                'command_sequence':(ident*128+step)%65535 or 1},validation=True,on_packet=capture)
            status='ACKED' if ack['result']==0 else 'REJECTED'
            with self.lock,self.db:
                self.db.execute('UPDATE validation_tc SET status=?,ack=? WHERE run=? AND step=?',(status,json.dumps(ack),ident,step))
        except (OSError,ValueError,KeyError) as error:
            with self.lock,self.db:
                self.db.execute("UPDATE validation_tc SET status='UNKNOWN',error=? WHERE run=? AND step=?",(str(error)[:300],ident,step))
                self.db.execute("UPDATE validation_runs SET status='FAILED' WHERE id=?",(ident,))
        return True
