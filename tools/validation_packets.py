"""Export reproducible TC/TM validation vectors or inspect a DGS run journal."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from services.validation.corpus import COUNT, generate, tc_matrix
from services.validation.recorder import report
from services.spacecraft_config import get_sensor_limits


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    export=sub.add_parser('generate')
    export.add_argument('--output',type=Path,required=True)
    export.add_argument('--seed',type=int,default=724)
    export.add_argument('--base-utc',default=None,help='ISO UTC measurement start; default now minus 10005 seconds')
    verify=sub.add_parser('report')
    verify.add_argument('--journal',type=Path,required=True)
    verify.add_argument('--run-key',required=True)
    args=parser.parse_args()
    if args.command=='report':
        result=report(args.journal,args.run_key)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        raise SystemExit(0 if result['state']=='PASS' else 1)
    base=(int(datetime.fromisoformat(args.base_utc.replace('Z','+00:00')).timestamp()) if args.base_utc else int(time.time())-COUNT-5)*1000
    args.output.mkdir(parents=True,exist_ok=False)
    limits=get_sensor_limits()
    cases=generate(args.seed,base,limits=limits)
    (args.output/'settings.json').write_text(json.dumps(limits,ensure_ascii=False,indent=2),encoding='utf-8')
    with (args.output/'tm_packets.jsonl').open('x',encoding='utf-8',newline='\n') as output:
        for case in cases:output.write(json.dumps({**case.expected(),'raw_hex':case.packet.hex()},ensure_ascii=False)+'\n')
    (args.output/'tc_matrix.json').write_text(json.dumps(tc_matrix(base),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'directory':str(args.output.resolve()),'count':len(cases),'seed':args.seed,'base_utc_ms':base}))


if __name__=='__main__':main()
