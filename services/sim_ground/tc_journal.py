"""Exact uplink bytes at the DGS socket boundary, independently of ROS intent."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from tools.pado_packet_codec import decode
from services.sim_ground.archive import json_fields


def record(path:Path,packet:bytes,status:str,*,catalog=None):
    if status not in ('PREPARED','SENT','UNKNOWN'):raise ValueError('invalid TC wire status')
    if path.is_symlink() or path.parent.is_symlink():raise ValueError('regular TC journal required')
    parsed=decode(packet,**({'catalog':catalog} if catalog else {}))
    if parsed['kind']!='TC':raise ValueError('TC required')
    db=sqlite3.connect(path,timeout=5)
    try:
        os.chmod(path,0o600)
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS uplink(sha TEXT PRIMARY KEY,raw BLOB,name TEXT,fields TEXT,status TEXT,wire_ms INTEGER)')
        if db.execute('SELECT COUNT(*) FROM uplink').fetchone()[0]>=50000:raise ValueError('TC journal retention limit reached')
        with db:
            db.execute('INSERT INTO uplink VALUES(?,?,?,?,?,?) ON CONFLICT(sha) DO UPDATE SET status=excluded.status,wire_ms=excluded.wire_ms',
                (hashlib.sha256(packet).hexdigest(),packet,parsed['name'],json.dumps(json_fields(parsed['fields'])),status,int(time.time()*1000)))
    finally:db.close()


def read(path:Path,digests=None):
    if not path.exists():return {'items':[]}
    if path.is_symlink() or path.stat().st_size>128*1024*1024:raise ValueError('invalid TC journal')
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
    try:
        if digests is None:rows=db.execute('SELECT * FROM uplink ORDER BY wire_ms DESC LIMIT 100').fetchall()
        else:
            if len(digests)>128:raise ValueError('bounded TC lookup required')
            rows=db.execute('SELECT * FROM uplink WHERE sha IN (%s)' % ','.join('?' for _ in digests),digests).fetchall() if digests else []
        return {'items':[{'sha256':r[0],'raw_hex':bytes(r[1]).hex(),'name':r[2],'parsed':json.loads(r[3]),'status':r[4],'wire_ms':r[5]} for r in rows]}
    finally:db.close()
