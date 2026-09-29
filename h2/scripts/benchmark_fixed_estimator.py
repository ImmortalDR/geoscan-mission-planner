"""Reproducible S17 preparation and fixed-plan benchmarks; no optimization."""
import argparse
from collections import Counter
import json
from pathlib import Path
import random
import time

import numpy as np

from gmp.api.h2_adapter import build_scene_sidecar
from gmp.api.planner_adapter import build_h1
from gmp.safety.h3_gate import input_fingerprint
from h2.estimate_grid import motion
from h2.fixed_estimator import FixedPlanEstimator
from h2.scheduler import Settings
from h2.task_routes import route_variants


def fixed_plan(bundle, rng=None):
    """Externally specify all choices; even infeasible plans must be evaluated."""
    tasks=list(bundle['tasks'])
    if rng:rng.shuffle(tasks)
    groups={u['id']:[] for u in bundle['fleet']}
    counts=Counter()
    for task in tasks:
        eligible=bundle['feasibility']['eligible_uav_ids_by_task'][task['id']]
        candidates=[u for u in bundle['fleet'] if u['id'] in eligible]
        if rng:
            u=rng.choice(candidates)
        elif task['payload_class']=='rgb':
            u=next((u for u in candidates if u['model']=='geoscan_201'),candidates[0])
        else:
            u=candidates[counts[task['payload_class']]%len(candidates)]
            counts[task['payload_class']]+=1
        groups[u['id']].append(task)
    sorties=[]
    for u in bundle['fleet']:
        tasks=groups[u['id']]
        while tasks:
            n=rng.randint(1,4) if rng else 1
            chunk,tasks=tasks[:n],tasks[n:]
            variants=[rng.choice(route_variants(t))['id'] if rng else 'primary' for t in chunk]
            sorties.append(dict(uav_id=u['id'],start_site_id=u['start_site'],landing_site_id=u['landing_site'],
                task_ids=[t['id'] for t in chunk],task_variants=variants,
                task_reversed=[bool(rng.getrandbits(1)) if rng else False for _ in chunk]))
    return dict(schema_version='h2.fixed_plan.v1',sorties=sorties)


def stats(values):
    return dict(count=len(values),mean_s=float(np.mean(values)),p95_s=float(np.quantile(values,.95)),max_s=max(values))


def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=root/'src/gmp/scenario_templates/S17_MSU_geo401_RGB_and_Lidar_alotofZones/input')
    parser.add_argument('--dataset',type=Path,default=root/'data')
    parser.add_argument('--bundle',type=Path,help='Skip H1 and use this bundle')
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--plans',type=int,default=100)
    parser.add_argument('--sample-transfers',type=int,default=1000)
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter()
    if args.bundle:
        bundle=json.loads(args.bundle.read_text());h1_s=None
    else:
        bundle=build_h1(args.input,args.dataset,'makespan')['h1_bundle'];h1_s=time.perf_counter()-started
    side_started=time.perf_counter();side=build_scene_sidecar(args.input,bundle);side_s=time.perf_counter()-side_started
    payloads=json.loads((args.input/'payload_catalog.json').read_text())['payload_profiles']
    settings=Settings(sample_step_m=20,survey_speed_factor=min(float(p['survey_speed_factor']) for p in payloads))
    e=FixedPlanEstimator(bundle,side,settings)
    plan=fixed_plan(bundle);estimate=e.evaluate(plan)
    first_total_s=time.perf_counter()-started
    print(json.dumps(dict(stage='first_plan',h1_s=h1_s,sidecar_s=side_s,preparation_s=e.preparation_s,
                         evaluation_s=estimate['evaluation_s'],total_s=first_total_s)),flush=True)
    times=[e.evaluate(plan)['evaluation_s'] for _ in range(args.plans)]
    rng=random.Random(42);different=[];statuses=Counter()
    for _ in range(args.plans):
        result=e.evaluate(fixed_plan(bundle,rng));different.append(result['evaluation_s']);statuses[result['status']]+=1
    # Compare conservative estimates to full terrain motion along these SAME paths.
    # This is not a comparison against a shorter straight route, nor an H3 verdict.
    sampled=[];under=[];sample_started=time.perf_counter()
    for _ in range(args.sample_transfers):
        uid=rng.choice(list(e.fleet));tab=e.by_uav[uid];start,end=rng.sample(tab['coordinates'],2)
        leg=e.leg(uid,start,end)
        if leg['coords'] is None:continue
        length=float(np.linalg.norm(np.diff(leg['coords'],axis=0),axis=1).sum())
        trajectory=motion(leg['coords'],leg['heights'],length/tab['uav']['ground_speed_ms'],settings,tab['grid'].elevation)
        actual=float(trajectory[-1,3]);sampled.append((leg['time_s'],actual))
        if actual>leg['time_s']+1e-6:under.append(dict(uav_id=uid,start=start,end=end,estimated_s=leg['time_s'],actual_s=actual))
    report=dict(input_dir=str(args.input),input_sha256=input_fingerprint(args.input)[0],scene_id=bundle['scene_id'],
        tasks=len(bundle['tasks']),transects=sum(len(t['transects']) for t in bundle['tasks']),
        h1_s=h1_s,scene_sidecar_s=side_s,preparation=e.report(),first_evaluation_s=estimate['evaluation_s'],
        input_to_first_estimate_s=first_total_s,repeat_same_plan=stats(times),different_fixed_plans=stats(different),
        different_plan_statuses=dict(statuses),example_plan=dict(status=estimate['status'],makespan_s=estimate['makespan_s'],
            sorties=len(plan['sorties']),violations=dict(Counter(v['code'] for v in estimate['violations']))),
        full_transit_comparison=dict(sample_count=len(sampled),wall_s=time.perf_counter()-sample_started,
            underestimates=under,min_estimated_to_motion_ratio=min(a/b for a,b in sampled if b>0),
            median_estimated_to_motion_ratio=float(np.median([a/b for a,b in sampled if b>0])),
            scope='Full H2 terrain motion on the same chosen grid polylines; excludes H3 safety validation'),
        notes=['No plan optimization or inter-aircraft deconfliction in benchmark.',
               'Example assignments deliberately fixed; rejection is not proof that the scenario is infeasible.',
               'first total includes H1 only when --bundle is omitted; imports excluded.'])
    for name,value in [('benchmark',report),('fixed-plan',plan),('estimate',estimate),('bundle',bundle),('scene',side)]:
        (args.out/f'{name}.json').write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
