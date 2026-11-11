"""Verify the real Docker Compose JSON round trip for both runtime profiles."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy.environment_profile import render,verify,PROFILES


def main():
    for mode,bind,operator in (('loopback','127.0.0.1','127.0.0.1/32'),
                               ('lan','192.168.45.128','192.168.45.1/32')):
        env=dict(os.environ,PADO_CONFIG_GID=str(os.getgid()))
        # Exercise actual Compose defaults, not values inherited from CI.
        env.pop('PADO_LOCAL_BIND_IP',None);env.pop('PADO_OPERATOR_CIDR',None)
        if mode=='lan':
            env.update(PADO_LOCAL_BIND_IP=bind,PADO_OPERATOR_CIDR=operator)
        base=json.loads(subprocess.check_output(['docker','compose','-p','pado-stage9-local','-f',
            str(ROOT/'deploy/compose.local.yaml'),'config','--format','json'],env=env))
        gateway=base['services']['dgs-ops-gateway']
        if (gateway['ports'][0]['host_ip']!=bind or
                gateway['environment']['PADO_GATEWAY_OPERATOR_CIDR']!=operator):
            raise ValueError(f'{mode} Compose bind or operator default differs')
        for kind in PROFILES:
            config=ROOT/'config' if kind=='validation' else ROOT/'config/operations'
            document=render(base,kind,'ci-profile',config)
            path=ROOT/'out'/f'{kind}-{mode}-compose-ci.json';path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(document))
            actual=json.loads(subprocess.check_output(['docker','compose','-f',str(path),'config','--format','json'],env=env))
            verify(base,actual,kind,'ci-profile',config,bind,operator)
            print(kind,mode,'Compose normalization and DGS-only profile verified')


if __name__=='__main__':main()
