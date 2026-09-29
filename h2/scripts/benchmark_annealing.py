"""End-to-end, fixed-seed H1 -> annealed H2 -> independent H3 benchmark."""
import argparse
from collections import Counter
import json
from pathlib import Path
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
    results=[]
    for index in range(2 if args.repeat else 1):
        began=time.perf_counter()
        candidate=build_live(args.input,root/'data',args.objective,args.budget,20260918,
                             str(args.out/f'progress-{index}.jsonl'),args.depth)
        built=time.perf_counter();validation=validate_result(args.input,candidate);done=time.perf_counter()
        search=candidate.get('provenance',{}).get('annealing',{})
        report=dict(input_dir=str(args.input),input_sha256=input_fingerprint(args.input)[0],
            objective=args.objective,depth=args.depth,seed=20260918,pipeline_s=built-began,h3_gate_s=done-built,
            total_s=done-began,h3_status=validation['status'],metrics=validation['metrics'],
            violations=dict(Counter(v['code'] for v in validation['violations'])),annealing=search)
        results.append(report)
        (args.out/f'result-{index}.json').write_text(json.dumps(candidate,allow_nan=False))
        (args.out/f'validation-{index}.json').write_text(json.dumps(validation,allow_nan=False))
        print(json.dumps(report,ensure_ascii=False),flush=True)
    if args.repeat:
        assert results[0]['annealing']['solution_sha256']==results[1]['annealing']['solution_sha256']
    (args.out/'benchmark.json').write_text(json.dumps(results,indent=2,ensure_ascii=False,allow_nan=False)+'\n')


if __name__=='__main__':main()
