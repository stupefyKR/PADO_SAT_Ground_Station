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
    env=dict(os.environ,PADO_CONFIG_GID=str(os.getgid()))
    base=json.loads(subprocess.check_output(['docker','compose','-p','pado-stage9-local','-f',
        str(ROOT/'deploy/compose.local.yaml'),'config','--format','json'],env=env))
    for kind in PROFILES:
        config=ROOT/'config' if kind=='validation' else ROOT/'config/operations'
        document=render(base,kind,'ci-profile',config)
        path=ROOT/'out'/f'{kind}-compose-ci.json';path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(document))
        actual=json.loads(subprocess.check_output(['docker','compose','-f',str(path),'config','--format','json'],env=env))
        verify(base,actual,kind,'ci-profile',config,'192.168.45.128','192.168.45.1/32')
        print(kind,'Compose normalization and DGS-only profile verified')


if __name__=='__main__':main()
