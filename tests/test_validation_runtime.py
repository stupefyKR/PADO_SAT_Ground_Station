import copy
import json
import os
from pathlib import Path
import secrets
import select
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from deploy.stage9.environment_profile import render, verify, PROFILES
from services.spacecraft_config import get_sensor_limits
from services.validation.scheduler import RunStore
from services.validation.runtime import activated, ContinuousContact
from tests import test_stage9_network_policy as boundary_tests

ROOT=Path(__file__).resolve().parents[1]


class EnvironmentProfileTests(unittest.TestCase):
    def test_isolation_common_images_and_boundary_mutations(self):
        fixture=boundary_tests.DgsBoundaryInvariantTests();fixture.setUp()
        base=fixture.good; base['name']='pado-stage9-local'
        # Minimal normalized fixture uses the same build sources as Compose.
        for name,s in base['services'].items():
            s['build']={'context':str(ROOT),'dockerfile':'docker/stage9/SimTm.Dockerfile' if name in ('sim-ground','sim-satellite','sim-volume-init') else 'docker/stage9/Dockerfile'}
            if name.startswith('fds-'):s['build']['dockerfile']='docker/stage9/FdsDemo.Dockerfile'
        base['services']['sim-ground']['command']=['python3','receiver','--n11-inbox','/n11-inbox/scenario-ltan12-v1']
        for kind in PROFILES:
            directory=ROOT/'config' if kind=='validation' else ROOT/'config/operations'
            result=render(base,kind,'deployment',directory)
            verify(base,result,kind,'deployment',directory,'192.168.45.128','192.168.45.1/32')
            self.assertNotIn('fds-iss-display',result['services'])
            self.assertEqual('sim-satellite' in result['services'],kind=='validation')
            self.assertEqual('fds-synthetic-demo' in result['services'],kind=='validation')
            self.assertEqual('fds-worker' in result['services'],kind=='operations')
            self.assertEqual(result['services']['portal']['image'],'pado-gs-web:local')
            self.assertEqual({n for n,s in result['services'].items() if s.get('ports')},{'dgs-ops-gateway'})
            broken=copy.deepcopy(result);broken['services']['ros-api']['networks']['entry']=None
            with self.assertRaises(ValueError):verify(base,broken,kind,'deployment',directory,'192.168.45.128','192.168.45.1/32')
            broken=copy.deepcopy(result);broken['volumes']['ros_drafts']['name']='shared-dangerous-store'
            with self.assertRaises(ValueError):verify(base,broken,kind,'deployment',directory,'192.168.45.128','192.168.45.1/32')

    def test_login_activation_nonce_and_frozen_settings(self):
        (ROOT/'out').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'out') as folder:
            root=Path(folder)
            with patch.dict(os.environ,{'PADO_VALIDATION_CONTROL':str(root/'control'),'PADO_VALIDATION_DEPLOYMENT':'first'}):
                store=RunStore(root/'runs.db',root/'control',root/'received.db')
                try:
                    self.assertFalse(activated());store.activate();self.assertTrue(activated())
                    with patch.dict(os.environ,{'PADO_VALIDATION_DEPLOYMENT':'next'}):self.assertFalse(activated())
                    run=store.schedule('planner01',int(time.time())+10,724)
                    self.assertEqual(len(run['tc']),54)
                    self.assertTrue((root/'control'/(run['run_key']+'.json')).is_file())
                    from services.validation.scheduler import frozen_limits
                    self.assertEqual(frozen_limits(root/'control',724,run['base'],10000),run['coverage']['sensor_limits'])
                    with self.assertRaises(ValueError):store.schedule('planner01',int(time.time())+20,1)
                    store.cancel(run['id'])
                    self.assertEqual(store.get(run['id'])['status'],'CANCELLED')
                finally:store.close()


@unittest.skipIf(os.name=='nt','Real Unix TC bridge and duplex ingress exercised on Linux')
class ValidationWireTests(unittest.TestCase):
    def test_scheduled_tc_raw_and_ten_thousand_tm_through_dgs(self):
        from services.sim_ground.receiver import TmReceiver
        from services.sim_sat.emulator import SatelliteSession,TmSpool,read_test_tc
        from tools.pado_packet_codec import decode
        from services.validation.corpus import catalog
        (ROOT/'out').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'out') as folder, patch.dict(os.environ,{'PADO_ENVIRONMENT':'validation'}):
            root=Path(folder);control=root/'control';control.mkdir()
            snapshot=root/'test_tm_snapshot.json'
            receiver=TmReceiver(snapshot,root/'n11',get_sensor_limits())
            receiver.enable_validation(control);receiver.contact_gate=ContinuousContact()
            store=RunStore(root/'runs.db',control,root/'validation.sqlite3')
            sat,ground=socket.socketpair();sat.settimeout(5);ground.settimeout(5)
            stop=threading.Event();ready=threading.Event();errors=[]
            def receive():
                try:receiver.process_connection(ground)
                except Exception as e:
                    if not stop.is_set():errors.append(e)
            def satellite():
                spool=TmSpool(root/'spool.sqlite3')
                session=SatelliteSession(spool,validation=True,validation_control=control)
                try:
                    while not stop.is_set():
                        session.flush_requested(sat,ContinuousContact(),clock_ms=lambda:int(time.time()*1000))
                        if select.select([sat],[],[],.002)[0]:
                            packet=read_test_tc(sat)
                            if packet is None:break
                            session.handle_uplink(packet,sat,ContinuousContact(),now_ms=int(time.time()*1000))
                except Exception as e:
                    if not stop.is_set():errors.append(e)
                finally:spool.close()
            threads=[threading.Thread(target=receive,daemon=True),threading.Thread(target=satellite,daemon=True),
                threading.Thread(target=receiver.serve_tc_socket,args=(root/'bridge.sock',),kwargs={'stop':stop,'ready':ready},daemon=True)]
            for t in threads:t.start()
            try:
                self.assertTrue(ready.wait(3))
                run=store.schedule('planner01',int(time.time())+3,724)
                deadline=time.monotonic()+180
                while time.monotonic()<deadline:
                    store.step(root/'bridge.sock')
                    if store.db.execute('SELECT status FROM validation_runs').fetchone()[0] in ('TRANSMITTED','FAILED'):break
                    time.sleep(.01)
                last=store.get(run['id'])
                self.assertEqual(errors,[])
                self.assertEqual(last['tc_summary']['state'],'PASS',last['tc'])
                for row in last['tc']:
                    self.assertIsNotNone(row['sent_ms'])
                    self.assertIsNotNone(row['wire_sent_ms'])
                    packet=decode(bytes.fromhex(row['raw_hex']),catalog=catalog())
                    self.assertEqual(packet['name'],row['name'])
                    self.assertEqual(packet['fields'],row['parameters'])
                while time.monotonic()<deadline:
                    last=store.get(run['id'])
                    if last['tm']['state'] in ('PASS','FAIL') and snapshot.exists():break
                    if errors:break
                    time.sleep(.3)
                self.assertEqual(errors,[])
                self.assertEqual(last['tm']['state'],'PASS',last['tm'])
                self.assertEqual(last['tm']['received'],10000)
                self.assertEqual(last['tm']['valid_parsed'],7500)
                self.assertEqual(last['tm']['malformed_rejected'],2500)
                self.assertTrue(snapshot.exists())
                self.assertGreaterEqual(last['tm']['sensor_events'],500)
                output=ROOT/'out/validation-wire-result.json'
                output.write_text(json.dumps({'tc':last['tc_summary'],'tm':last['tm']},indent=2))
            finally:
                stop.set();sat.close();ground.close()
                for t in threads:t.join(6)
                receiver.validation.close();receiver.archive.close();store.close()


if __name__=='__main__':unittest.main()
