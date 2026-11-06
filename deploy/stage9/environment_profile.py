"""Derive isolated deployments from the existing, policy-checked Compose stack.

Input is Docker Compose's normalized JSON, so no additional YAML dependency is
needed. The launcher keeps the original policy check and checks the derived
security projection again before publishing any port.
"""
import argparse
import copy
import json
from pathlib import Path
import sys

from deploy.stage9.check_compose_policy import check, check_actual_networks


PROFILES = {
    'validation': ('pado-stage9-local', 8080, 'br-pado-local'),
    'operations': ('pado-operations', 8082, 'br-pado-ops'),
}


def render(base, kind, deployment_id, config_dir):
    if kind not in PROFILES:raise ValueError('unknown environment')
    result=copy.deepcopy(base)
    project,port,bridge=PROFILES[kind]
    result['name']=project
    services=result['services']
    # No ISS/fabricated operational source is started by either profile.
    removed={'fds-iss-display'}
    if kind=='operations':removed|={'fds-synthetic-demo','sim-satellite'}
    for name in removed:services.pop(name,None)
    if kind=='operations':
        worker=copy.deepcopy(base['services']['fds-synthetic-demo'])
        worker['command']=['python3','tools/run_fds_pipeline.py','--config','/config/fds.properties',
            '--orekit-data','/opt/orekit-data','--tle-inbox','/n11-inbox/tle','--gnss-inbox','/n11-inbox/operations',
            '--tle-current-root','/app/out/tle-current','--gnss-current-root','/app/out/gnss-current',
            '--output-root','/app/out/runs','--outbox-dir','/app/out/outbox','--n08-outbox-dir','/app/out/n08-outbox',
            '--view-dir','/app/out/fds-view','--mps-schedule-dir','/app/out/mps-schedules',
            '--mvn','/usr/local/bin/mvn-offline','--maven-repo','/app/m2','--watch']
        worker['volumes']=[m for m in worker.get('volumes',[]) if m['target']!='/app/out/fds-truth']
        worker['volumes'].append({'type':'bind','source':str(config_dir),'target':'/config',
            'read_only':True,'bind':{'create_host_path':False}})
        services['fds-worker']=worker
        ground=services['sim-ground']
        command=ground['command']
        command[command.index('--n11-inbox')+1]='/n11-inbox/operations'
        for mount in ground.get('volumes',[]):
            if mount['target']=='/fds-view':mount['source']='fds_view'
    for name,service in services.items():
        dockerfile=service.get('build',{}).get('dockerfile','')
        service['image']=('pado-gs-fds:local' if 'FdsDemo' in dockerfile else
                          'pado-gs-link:local' if 'SimTm' in dockerfile else 'pado-gs-web:local')
        if name in ('portal','ros-api','mps-api','sim-ground','sim-satellite'):
            service.setdefault('environment',{})['PADO_ENVIRONMENT']=kind
        if kind=='validation' and name in ('ros-api','sim-ground','sim-satellite'):
            service['environment'].update(PADO_VALIDATION_CONTROL='/validation',
                PADO_VALIDATION_DEPLOYMENT=deployment_id,PADO_VALIDATION_CONTACT='continuous')
            service.setdefault('volumes',[]).append({'type':'volume','source':'validation_control',
                'target':'/validation','read_only':name!='ros-api'})
        for mount in service.get('volumes',[]):
            if mount['type']=='bind' and mount['target']=='/config':mount['source']=str(config_dir)
        for dependency in removed:service.get('depends_on',{}).pop(dependency,None)
    if kind=='validation':
        init=services['sim-volume-init']
        init['volumes'].append({'type':'volume','source':'validation_control','target':'/validation'})
        init['command'].append('/validation')
        services['ros-api'].setdefault('depends_on',{})['sim-volume-init']={'condition':'service_completed_successfully','required':True}
    else:
        command=services['portal']['command']
        for flag in ('--synthetic-orbit-view','--iss-orbit-view'):
            if flag in command:
                index=command.index(flag);del command[index:index+2]
        command.extend(['--operational-orbit-view','/fds-view/orbit_view.json'])
    # Keep empty FDS/input volumes for an eventual real source. They are never
    # shared across projects and are not populated by simulation in operations.
    volumes={m['source'] for s in services.values() for m in s.get('volumes',[]) if m['type']=='volume'}
    result['volumes']={name:{'name':f'{project}_{name}'} for name in volumes}
    for name,network in result['networks'].items():
        network['name']=f'{project}_{name}'
    result['networks']['entry']['driver_opts']['com.docker.network.bridge.name']=bridge
    gateway=services['dgs-ops-gateway']
    gateway['command']=['python3','/app/deploy/stage9/dgs_ops_gateway.py','--listen-port',str(port),'--upstream-port','8080']
    gateway['ports'][0].update(published=str(port),target=port)
    return result


def security_projection(document):
    """Compare all configuration after harmless Compose normalization."""
    def normalize(value,key=''):
        if isinstance(value,dict):
            return {k:normalize(v,k) for k,v in value.items() if not (k=='volume' and v=={}) and not (k in ('read_only','create_host_path') and v is False)}
        if isinstance(value,list):
            values=[normalize(v) for v in value]
            return sorted(values,key=lambda v:json.dumps(v,sort_keys=True)) if key in ('volumes','cap_drop','cap_add','security_opt','group_add') else values
        return value
    return normalize(document)


def verify(base,actual,kind,deployment_id,config_dir,bind,operator):
    check(base,bind,8080,'br-pado-local',operator,'pado-stage9-local')
    expected=render(base,kind,deployment_id,config_dir)
    if security_projection(actual)!=security_projection(expected):
        raise ValueError('environment config differs from the isolated approved profile')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['render','verify','networks'])
    parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--environment',choices=PROFILES,required=True)
    parser.add_argument('--deployment-id',required=True)
    parser.add_argument('--config-dir',type=Path,required=True)
    parser.add_argument('--bind-ip',required=True)
    parser.add_argument('--operator-cidr',required=True)
    parser.add_argument('--require-gateway',action='store_true')
    args=parser.parse_args()
    base=json.loads(args.base.read_text())
    check(base,args.bind_ip,8080,'br-pado-local',args.operator_cidr,'pado-stage9-local')
    if args.action=='render':json.dump(render(base,args.environment,args.deployment_id,args.config_dir),sys.stdout)
    elif args.action=='verify':verify(base,json.load(sys.stdin),args.environment,args.deployment_id,args.config_dir,args.bind_ip,args.operator_cidr)
    else:
        project,_,bridge=PROFILES[args.environment]
        check_actual_networks(json.load(sys.stdin),project,bridge,args.require_gateway,
                              satellite=args.environment=='validation')


if __name__=='__main__':main()
