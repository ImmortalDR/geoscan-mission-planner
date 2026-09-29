"""End-to-end, fixed-seed H1 -> Routing H2 -> independent H3 benchmark."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import time

from gmp.api.planner_adapter import build_live
from gmp.safety.h3_gate import input_fingerprint, validate_result


def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--objective',choices=['makespan','total_flight'],default='makespan')
    parser.add_argument('--depth',choices=['quick','standard','deep'],default='standard')
    parser.add_argument('--budget',type=float,default=60.)
    parser.add_argument('--repeat',action='store_true')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    files=sorted([*root.joinpath('h2/src').rglob('*.py'),*root.joinpath('src/gmp').rglob('*.py'),
                  *root.joinpath('h1/h1_coverage/src').rglob('*.py')])
    source_files={str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    source_digest=hashlib.sha256(json.dumps(source_files,sort_keys=True).encode()).hexdigest()
    source_input=args.input
    source_hash=input_fingerprint(source_input)[0]
    # Selecting a metric in the UI also requests it in mission.json. Mirror
    # that operation in an isolated copy; never edit a scenario template.
    mission=json.loads((source_input/'mission.json').read_text())
    objective_added=args.objective not in mission.get('objectives',[])
    if objective_added:
        args.input=args.out/'input'
        shutil.copytree(source_input,args.input,dirs_exist_ok=True)
        mission.setdefault('objectives',[]).append(args.objective)
        (args.input/'mission.json').write_text(json.dumps(mission,indent=2)+'\n')
    results=[]
    for index in range(2 if args.repeat else 1):
        began=time.perf_counter()
        candidate=build_live(args.input,root/'data',args.objective,args.budget,20260918,
                             str(args.out/f'progress-{index}.jsonl'),args.depth)
        built=time.perf_counter();validation=validate_result(args.input,candidate);done=time.perf_counter()
        search=candidate.get('provenance',{}).get('routing',{})
        report=dict(input_dir=str(args.input),input_sha256=input_fingerprint(args.input)[0],
            source_code_sha256=source_digest,
            source_input_dir=str(source_input),source_input_sha256=source_hash,objective_added=objective_added,
            objective=args.objective,depth=args.depth,seed=20260918,pipeline_s=built-began,h3_gate_s=done-built,
            total_s=done-began,h3_status=validation['status'],metrics=validation['metrics'],
            violations=dict(Counter(v['code'] for v in validation['violations'])),routing=search)
        results.append(report)
        (args.out/f'result-{index}.json').write_text(json.dumps(candidate,allow_nan=False))
        (args.out/f'validation-{index}.json').write_text(json.dumps(validation,allow_nan=False))
        print(json.dumps(report,ensure_ascii=False),flush=True)
    if args.repeat:
        assert results[0]['routing']['solution_sha256']==results[1]['routing']['solution_sha256']
    (args.out/'benchmark.json').write_text(json.dumps(results,indent=2,ensure_ascii=False,allow_nan=False)+'\n')


if __name__=='__main__':main()
