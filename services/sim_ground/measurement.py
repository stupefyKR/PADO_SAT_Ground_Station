"""Measurement checks shared by normal downlinks and integration validation."""
import math
from tools.pado_packet_codec import PacketError, gnss_to_fds_properties


def validate_measurement(decoded,expected_norad=99999,*,error_type=PacketError):
    if decoded['name']=='GNSS_PVT':
        if decoded['fields']['norad_id']!=expected_norad:raise error_type('GNSS satellite identity mismatch')
        gnss_to_fds_properties(decoded)
    elif decoded['name']=='AOCS_STATE':
        if abs(math.hypot(*(decoded['fields'][f'q_{a}'] for a in 'wxyz'))-1)>0.02:
            raise error_type('non-unit synthetic attitude quaternion')
