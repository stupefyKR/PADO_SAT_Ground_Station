import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from services.webapp.auth import AuthStore
from services.webapp.main import AppServer, initialize_internal_token
from tests import test_stage9_http as http_tests
from urllib.error import URLError

ROOT=Path(__file__).resolve().parents[1]


class ValidationHttpTests(unittest.TestCase):
    def request(self,*args,**kwargs):
        try:return http_tests.LocalHttpWebTests.request(*args,**kwargs)
        except URLError as error:
            if isinstance(error.reason,PermissionError):self.skipTest('local sandbox denies loopback; exercised on Linux')
            raise

    def setUp(self):
        (ROOT/'out').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'out')
        self.root=Path(self.temp.name)
        self.servers=[];self.stores=[];self.threads=[]
        self.password=secrets.token_urlsafe(24)

    def tearDown(self):
        for s in self.servers:s.shutdown();s.server_close()
        for t in self.threads:t.join(2)
        for s in self.servers:
            if s.validation_runs:s.validation_runs.close()
            if s.drafts:s.drafts.close()
        for s in self.stores:s.close()
        self.temp.cleanup()

    def deployment(self,kind):
        root=self.root/kind;root.mkdir()
        token=root/'token';initialize_internal_token(token)
        auth=AuthStore(root/'auth.db',http_tests.FakeHasher());self.stores.append(auth)
        auth.create_user('planner01','planner',self.password)
        auth.create_user('audit01','auditor',self.password)
        with patch.dict(os.environ,{'PADO_ENVIRONMENT':kind,'PADO_VALIDATION_CONTROL':str(root/'control')}):
            backend=AppServer(('127.0.0.1',0),mode='ros',transport='http',internal_token_file=token,
                draft_db=root/'drafts.db',tm_preview=root/'test_tm_snapshot.json')
            portal=AppServer(('127.0.0.1',0),mode='portal',transport='http',internal_token_file=token,
                auth=auth,ros_url=f'http://127.0.0.1:{backend.server_port}')
        for server in (backend,portal):
            self.servers.append(server)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.threads.append(thread)
        return root,backend,f'http://127.0.0.1:{portal.server_port}'

    def test_login_activation_schedule_auth_and_operations_isolation(self):
        root,backend,base=self.deployment('validation')
        op_root,_,ops=self.deployment('operations')
        self.assertEqual(self.request(base+'/api/ros/validation/runs')[0],401)
        self.assertEqual(self.request(base+'/api/login',data={'username':'planner01','password':secrets.token_urlsafe(24)})[0],401)
        self.assertFalse((root/'control/active.json').exists())
        status,headers,session=self.request(base+'/api/login',data={'username':'planner01','password':self.password})
        self.assertEqual(status,200)
        self.assertTrue((root/'control/active.json').exists())
        cookie={'Cookie':headers['Set-Cookie'].split(';')[0]}
        self.assertTrue(cookie['Cookie'].startswith('pado_validation_session='))
        self.assertEqual(self.request(ops+'/api/session',headers=cookie)[0],401)
        self.assertEqual(self.request(ops+'/api/ros/validation/runs',data={})[0],404)
        self.assertFalse((op_root/'control').exists())
        request={'target_utc_s':int(time.time())+30,'seed':724}
        self.assertEqual(self.request(base+'/api/ros/validation/runs',data=request,headers=cookie)[0],403)
        status,_,run=self.request(base+'/api/ros/validation/runs',data=request,headers={**cookie,'X-CSRF-Token':session['csrf']})
        self.assertEqual(status,200,run)
        self.assertEqual(len(run['tc']),54)
        self.assertEqual(self.request(base+f"/api/ros/validation/runs/{run['id']}",headers=cookie)[0],200)
        # Knowing the backend port does not bypass internal identity checks.
        self.assertEqual(self.request(f'http://127.0.0.1:{backend.server_port}/validation/runs')[0],403)
        _,h,auditor=self.request(base+'/api/login',data={'username':'audit01','password':self.password})
        self.assertEqual(self.request(base+'/api/ros/validation/runs',data=request,
            headers={'Cookie':h['Set-Cookie'].split(';')[0],'X-CSRF-Token':auditor['csrf']})[0],403)


if __name__=='__main__':unittest.main()
