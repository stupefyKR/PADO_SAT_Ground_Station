"""Explicit deployment mode; client requests cannot enable validation."""
import os
import json
from pathlib import Path


def environment():
    value=os.environ.get('PADO_ENVIRONMENT','legacy')
    if value not in ('legacy','validation','operations'):raise ValueError('invalid PADO_ENVIRONMENT')
    return value


class ContinuousContact:
    def active(self,_utc_ms):return True


def control_directory():
    return Path(os.environ.get('PADO_VALIDATION_CONTROL','/validation'))


def activated():
    try:
        path=control_directory()/'active.json'
        if path.is_symlink() or path.stat().st_size>4096:return False
        value=json.loads(path.read_text(encoding='utf-8'))
        return value.get('enabled') is True and value.get('deployment_id')==os.environ.get('PADO_VALIDATION_DEPLOYMENT','local')
    except (OSError,ValueError):return False


def contact_gate(view):
    from services.sim_sat.contact import ContactGate
    if environment()=='validation' and os.environ.get('PADO_VALIDATION_CONTACT','continuous')=='continuous':
        return ContinuousContact()
    return ContactGate(view)
