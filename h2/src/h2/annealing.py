"""Deterministic simulated annealing over immutable H1 task visits."""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
import math
import random
import statistics
import time

import numpy as np

from .anneal_schedule import SequenceEvaluator
from .anneal_repair import repair_schedule
from .contract import fingerprint, load_bundle
from .fixed_estimator import FixedPlanEstimator


DEPTHS = {
    'quick': dict(steps=700, starts=1, finalists=2, patience=150, budget_s=15),
    'standard': dict(steps=2800, starts=2, finalists=4, patience=350, budget_s=60),
    'deep': dict(steps=10000, starts=4, finalists=7, patience=700, budget_s=180),
}


def search_config(settings, task_count):
    cfg=dict(DEPTHS[settings.search_depth or 'standard'])
    cfg['steps']=max(60,min(50000,round(cfg['steps']*settings.time_budget_s/cfg['budget_s'])))
    if task_count<=8:
        cfg['steps']=min(cfg['steps'],600);cfg['patience']=min(cfg['patience'],100)
    return cfg


def initial_state(evaluator, baseline):
    seq={u:[] for u in evaluator.uids};placed=set()
    for s in baseline['sorties']:
        for visit in zip(s['task_ids'],s.get('task_variants',['primary']*len(s['task_ids'])),s['task_reversed']):
            seq[s['uav_id']].append(visit);placed.add(visit[0])
    for tid in sorted(evaluator.e.tasks.keys()-placed):
        choices=[]
        for uid in evaluator.uids:
            if uid not in evaluator.e.eligible[tid]:continue
            options=evaluator.options[uid,tid] or ((tid,'primary',False),)
            for visit in options:
                row=evaluator.e.task(uid,*visit)
                u=evaluator.e.fleet[uid]
                bases=[s for s in sorted(evaluator.e.sites) if evaluator.e.sites[s]['role'] in ('both','start')
                       and (u['start_site'] is None or s==u['start_site'])]
                value=math.inf
                if row and row['valid']:
                    for base in bases:
                        leg=evaluator.e.leg(uid,evaluator.site_point(uid,base),row['entry'])
                        value=min(value,leg['time_s']+row['time_s'])
                choices.append((value+len(seq[uid])*10,uid,visit))
        if choices:
            _,uid,visit=min(choices);seq[uid].append(visit)
    return tuple(tuple(seq[u]) for u in evaluator.uids)


def geographic_state(evaluator,state):
    out=[]
    for uid,visits in zip(evaluator.uids,state):
        remaining=list(visits);ordered=[];u=evaluator.e.fleet[uid]
        base=u['start_site'] or next((s for s in sorted(evaluator.e.sites) if evaluator.e.sites[s]['role'] in ('both','start')),None)
        if base is None:
            out.append(tuple(visits));continue
        point=evaluator.site_point(uid,base)
        while remaining:
            choices=[]
            for i,old in enumerate(remaining):
                for visit in evaluator.options[uid,old[0]] or (old,):
                    row=evaluator.e.task(uid,*visit)
                    cost=evaluator.e.leg(uid,point,row['entry'])['time_s'] if row and row['valid'] else math.inf
                    choices.append((cost,visit,i))
            _,visit,i=min(choices);remaining.pop(i);ordered.append(visit)
            row=evaluator.e.task(uid,*visit)
            if row and row['valid']:point=row['exit']
        out.append(tuple(ordered))
    return tuple(out)


def neighbor(evaluator,state,rng,move=None):
    move=move or rng.choice(('variant','relocate','swap','reorder','two_opt'))
    occupied=[i for i,s in enumerate(state) if s]
    if not occupied:return state,move
    seq=[list(s) for s in state];a=rng.choice(occupied);i=rng.randrange(len(seq[a]));visit=seq[a][i]
    uid=evaluator.uids[a]
    if move=='variant':
        choices=[v for v in evaluator.options[uid,visit[0]] if v!=visit]
        if choices:seq[a][i]=rng.choice(choices)
    elif move=='relocate':
        targets=[j for j,u in enumerate(evaluator.uids) if j!=a and u in evaluator.e.eligible[visit[0]]]
        if targets:
            b=rng.choice(targets);seq[a].pop(i)
            opts=evaluator.options[evaluator.uids[b],visit[0]]
            if opts and visit not in opts:visit=rng.choice(opts)
            seq[b].insert(rng.randrange(len(seq[b])+1),visit)
    elif move=='swap':
        targets=[j for j in occupied if j!=a]
        if targets:
            b=rng.choice(targets);j=rng.randrange(len(seq[b]));other=seq[b][j];ub=evaluator.uids[b]
            if uid in evaluator.e.eligible[other[0]] and ub in evaluator.e.eligible[visit[0]]:
                va=evaluator.options[uid,other[0]];vb=evaluator.options[ub,visit[0]]
                seq[a][i]=other if not va or other in va else rng.choice(va)
                seq[b][j]=visit if not vb or visit in vb else rng.choice(vb)
    elif len(seq[a])>1:
        j=rng.randrange(len(seq[a]))
        if move=='reorder':seq[a].insert(j,seq[a].pop(i))
        else:
            lo,hi=sorted((i,j))
            # Reverse both the order and each complete task traversal.
            seq[a][lo:hi+1]=[(v[0],v[1],not v[2]) for v in reversed(seq[a][lo:hi+1])]
    return tuple(tuple(s) for s in seq),move


def optimize(evaluator,state,settings,progress=None):
    cfg=search_config(settings,len(evaluator.e.tasks));rng=random.Random(settings.seed)
    frontier={};statistics_moves=Counter();accepted=uphill=iterations=0;logs=[];stops=[]
    def remember(s,score):
        key=(score['missing'],score['objective'])
        actual=tuple(label.path for label in score['labels'])
        if any(actual==tuple(label.path for label in value[1]['labels']) for value in frontier.values()):
            return False
        previous=min((v[0] for v in frontier.values()),default=(math.inf,math.inf))
        frontier[s]=(key,score)
        if len(frontier)>cfg['finalists']:
            worst=max(frontier,key=lambda k:(frontier[k][0],k));del frontier[worst]
        return key[0]<previous[0] or (key[0]==previous[0] and key[1]<previous[1]-.01)
    initial=evaluator.evaluate(state);remember(state,initial)
    geographic=geographic_state(evaluator,state);remember(geographic,evaluator.evaluate(geographic))
    if len(evaluator.e.tasks)<=1:
        for uid in evaluator.uids:
            for tid in sorted(evaluator.e.tasks):
                for visit in evaluator.options[uid,tid]:
                    candidate=tuple((visit,) if u==uid else () for u in evaluator.uids)
                    remember(candidate,evaluator.evaluate(candidate))
        stops.append('trivial_exhaustive')
    else:
        for start in range(cfg['starts']):
            current=state if start==0 else min(frontier,key=lambda k:(frontier[k][0],k))
            if start>1:
                for _ in range(5*start):current,_=neighbor(evaluator,current,rng)
            score=evaluator.evaluate(current);deltas=[]
            for _ in range(24):
                candidate,_=neighbor(evaluator,current,rng);other=evaluator.evaluate(candidate)
                if other['missing']==score['missing'] and other['objective']>score['objective']:
                    deltas.append(other['objective']-score['objective'])
            temperature=(statistics.median(deltas)/-math.log(.65) if deltas else max(1.,score['objective']*.02))
            limit=max(1,cfg['steps']//cfg['starts']);stale=0;local_best=(score['missing'],score['objective'])
            for step in range(limit):
                candidate,move=neighbor(evaluator,current,rng);statistics_moves[move]+=1;iterations+=1
                other=evaluator.evaluate(candidate)
                fraction=step/max(1,limit-1);temp=max(1e-9,temperature*.002**fraction)
                delta=other['energy']-score['energy']
                if delta<=0 or rng.random()<math.exp(-min(745.,delta/temp)):
                    accepted+=1;uphill+=int(delta>0);current,score=candidate,other
                changed=remember(candidate,other)
                key=(score['missing'],score['objective'])
                if key[0]<local_best[0] or (key[0]==local_best[0] and key[1]<local_best[1]-.01):local_best=key;stale=0
                else:stale+=1
                if changed or step%200==0:
                    best=min(v[0] for v in frontier.values())
                    entry=dict(stage='annealing',start=start,iteration=iterations,temperature=temp,
                               objective_value=best[1],unassigned_count=best[0])
                    logs.append(entry)
                    if progress:progress(entry)
                # Work-count stopping remains identical on a slow or busy machine.
                if stale>=cfg['patience'] and step>=min(200,limit//2):
                    stops.append('no_improvement');break
            else:stops.append('step_limit')
    finalists=sorted(((s,v[1]) for s,v in frontier.items()),key=lambda p:(p[1]['missing'],p[1]['objective'],p[0]))
    report=dict(seed=settings.seed,depth=settings.search_depth,config=cfg,iterations=iterations,
                accepted=accepted,accepted_uphill=uphill,moves=dict(statistics_moves),stop_reasons=stops,improvement_tolerance_s=.01,
                initial_estimate_s=initial['objective'],initial_unassigned=initial['missing'],
                best_estimate_s=finalists[0][1]['objective'],best_unassigned=finalists[0][1]['missing'],
                sequence_evaluations=evaluator.calls)
    return finalists,report,logs


def plan_annealed(source,settings,scene=None,progress=None):
    from .planner import plan_bundle,metrics,infeasibility_bounds
    from .scheduler import Scheduler
    from .checks import check_plan
    began=time.perf_counter();bundle=load_bundle(source);timings={};history=[]
    def emit(stage,**values):
        event=dict(stage=stage,elapsed_s=time.perf_counter()-began,**values)
        history.append(event)
        if progress:progress(deepcopy(event))
    emit('precomputation')
    t=time.perf_counter();estimator=FixedPlanEstimator(bundle,scene,settings);timings['precomputation_s']=time.perf_counter()-t
    emit('initial_plan')
    t=time.perf_counter()
    baseline=plan_bundle(bundle,replace(settings,search_depth=None,max_iterations=0,cpsat=False),scene)
    timings['initial_plan_s']=time.perf_counter()-t
    evaluator=SequenceEvaluator(estimator)
    state=initial_state(evaluator,baseline)
    t=time.perf_counter();finalists,report,logs=optimize(evaluator,state,settings,lambda event:emit(event['stage'],**{k:v for k,v in event.items() if k!='stage'}))
    timings['annealing_s']=time.perf_counter()-t
    emit('full_validation',candidate_count=len(finalists))
    candidates=[];checks=[];t=time.perf_counter()
    for rank,(state,score) in enumerate(finalists):
        fixed=evaluator.fixed_plan(score)
        plan=estimator.materialize(fixed,check=False)
        routes,conflicts,actions=repair_schedule(plan['sorties'],bundle['fleet'],settings,scene,estimator.horizon)
        plan['sorties']=routes;plan['metrics']=metrics(routes)
        plan['checks']=check_plan(bundle,plan,settings,scene)
        final_fixed={'sorties':[dict(uav_id=s['uav_id'],start_site_id=s['start_site_id'],landing_site_id=s['landing_site_id'],
            start_s=s['start_s'],task_ids=s['task_ids'],task_variants=s['task_variants'],task_reversed=s['task_reversed']) for s in routes]}
        extra=[v for v in estimator.evaluate(final_fixed)['violations'] if v['code']!='missing_task']
        hard=[v for v in plan['checks']['violations'] if v['code']!='unassigned_task']+extra
        if plan['estimate_overruns']:
            extra.append({'code':'estimate_overrun'});hard.append(extra[-1])
        plan['checks']['violations'].extend(extra)
        plan['checks']['passed']=not plan['checks']['violations']
        plan['deconfliction']=dict(remaining=conflicts,ladder_actions=actions,ladder_used=[1] if actions else [])
        plan['status']='FEASIBLE' if not hard and not plan['unassigned'] else 'UNRESOLVED'
        plan['search_candidate_rank']=rank
        value=plan['metrics'][settings.objective+'_s']
        checks.append(dict(rank=rank,unassigned=len(plan['unassigned']),objective_s=value,
                           passed=plan['status']=='FEASIBLE',violations=hard,repair_actions=actions))
        candidates.append(((bool(hard),len(plan['unassigned']),len(hard),value),plan))
    # Retain a complete incumbent when it also passes the new return-reserve
    # model. Conservative transit estimates must not force a worse verified plan.
    hard=[v for v in baseline['checks']['violations'] if v['code']!='unassigned_task']
    if any(s['start_s']<estimator.window_start_s or (estimator.horizon is not None and s['end_s']>estimator.horizon) for s in baseline['sorties']):
        hard.append({'code':'daylight_window'})
    if any(bundle['mission']['wind']['speed_ms']>estimator.fleet[s['uav_id']]['max_wind_ms'] for s in baseline['sorties']):
        hard.append({'code':'wind_limit'})
    incumbent_reserve=None
    if not baseline['unassigned'] and not hard:
        incumbent_reserve=True
        for sortie in baseline['sorties']:
            uid=sortie['uav_id'];u=estimator.fleet[uid]
            points=np.array([[p['x'],p['y'],p.get('z_m',p['agl_m']),p['t_s']-sortie['start_s'],p['agl_m']]
                             for p in sortie['waypoints'] if p['phase'] not in ('landing','ground','service')])
            requirement=estimator.by_uav[uid]['grid'].return_requirements(points) if len(points) else 0.
            if requirement>u['operational_endurance_min']*60*(1-u['energy_reserve_fraction']):
                incumbent_reserve=False;break
    any_complete=any(p['status']=='FEASIBLE' for _,p in candidates)
    if (not any_complete and (baseline['unassigned'] or incumbent_reserve)) or (not baseline['unassigned'] and incumbent_reserve and not hard):
        candidates.append(((bool(hard),len(baseline['unassigned']),len(hard),baseline['metrics'][settings.objective+'_s']),baseline))
    selected_key,best=min(candidates,key=lambda x:x[0])
    if best is baseline:
        best['checks']['violations']=hard+[v for v in best['checks']['violations'] if v['code']=='unassigned_task']
        best['checks']['passed']=not best['checks']['violations']
    best['status']='FEASIBLE' if not selected_key[0] and not best['unassigned'] else 'UNRESOLVED'
    timings['final_checks_s']=time.perf_counter()-t
    report.update(timings=timings,candidates=checks,legacy_objective_s=baseline['metrics'][settings.objective+'_s'],
                  legacy_unassigned=len(baseline['unassigned']),incumbent_reserve_passed=incumbent_reserve,
                  selected_source=('legacy_incumbent' if not baseline['unassigned'] else 'legacy_fallback') if best is baseline else 'annealing',
                  final_objective_s=best['metrics'][settings.objective+'_s'],final_unassigned=len(best['unassigned']))
    best['annealing']=report;best['solver_log']=history;best['settings']=asdict(settings)
    best['metrics'].update(runtime_s=time.perf_counter()-began,iterations=report['iterations'],
                           unassigned_count=len(best['unassigned']),conflict_count=len(best['deconfliction']['remaining']))
    best['infeasibility_proofs']=infeasibility_bounds(Scheduler(bundle,settings,scene))
    if best['status']!='FEASIBLE' and (best['infeasibility_proofs'] or any(not ids for ids in estimator.eligible.values())):
        best['status']='INFEASIBLE'
    report['solution_sha256']=fingerprint({'sorties':best['sorties'],'unassigned':best['unassigned'],'objective':settings.objective})
    emit('complete',objective_value=report['final_objective_s'],unassigned_count=len(best['unassigned']))
    return best
