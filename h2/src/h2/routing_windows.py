"""Additional deterministic Routing search, serving remaining transects by window.

The ordinary unrestricted search remains a competing candidate. This deliberately
conservative alternative grounds the fleet during the union of temporal closures;
local prohibitions do not become global constraints on the ordinary search.
Physical graph/DEM/escape tables are shared, not recalculated for every window.
"""
from copy import copy, deepcopy
from dataclasses import replace
import time

from .anneal_repair import repair_schedule
from .checks import check_plan
from .planner import metrics
from .routing_solver import RoutingModel


EDGE_GAP_S = .2  # More than the Routing clock's 0.1 s quantisation.


def allowed_windows(graph):
    lo,hi=graph.window_start,graph.horizon
    blocked=sorted((max(lo,float(z['start_s'])-EDGE_GAP_S),min(hi,float(z['end_s'])+EDGE_GAP_S))
                   for z in (graph.scene or {}).get('temporal',[])
                   if z['end_s']>=lo and z['start_s']<=hi)
    windows=[];cursor=lo
    for a,b in blocked:
        if a>cursor:windows.append((cursor,a))
        cursor=max(cursor,b)
    if cursor<hi:windows.append((cursor,hi))
    return windows if blocked else []


def window_graph(graph, remaining, window, positions, ready, last):
    """A view of immutable physical tables, with remaining service rows only."""
    g=copy(graph);g.window_start,g.horizon=window;g.ready=dict(ready)
    indices=[i for i,tid in enumerate(graph.tids) if tid in remaining]
    g.tids=[graph.tids[i] for i in indices]
    g.tasks={tid:graph.tasks[tid] for tid in g.tids}
    g.by_uav={uid:dict(p,services=[p['services'][2*i+j] for i in indices for j in (0,1)])
              for uid,p in graph.by_uav.items()}
    g.fleet=deepcopy(graph.fleet);g.policy=deepcopy(graph.policy)
    for uid,policy in g.policy.items():
        if uid in positions:
            policy['start']=[positions[uid]]
            g.fleet[uid]['start_site']=positions[uid]
        if not last:
            # End at a permitted final site that also permits the next launch.
            # This keeps early completion valid without inventing a base ferry.
            policy['finish']=sorted(set(policy['finish']) & set(policy['refuel']))
        if ready.get(uid,0.)>=g.horizon:policy['start']=[]
    g.bundle=dict(graph.bundle,tasks=list(g.tasks.values()),fleet=list(g.fleet.values()),
                  feasibility=dict(graph.bundle['feasibility'],eligible_uav_ids_by_task={
                      tid:graph.bundle['feasibility']['eligible_uav_ids_by_task'][tid] for tid in g.tids}))
    return g


def trim_last_survey(model, route):
    result=list(route)
    surveys=[i for i,n in enumerate(result) if model.nodes[n].kind=='survey']
    if len(surveys)<=1:return []
    result.pop(surveys[-1])
    # Remove now-empty recharge sorties, preserving initial and final sites.
    while True:
        empty=next((i for i in range(len(result)-1)
                    if model.nodes[result[i]].kind!='survey' and model.nodes[result[i+1]].kind!='survey'),None)
        if empty is None:return result
        result.pop(empty if model.nodes[result[empty]].kind=='refuel' else empty+1)


def fit_routes(model, routes):
    """Reserve actual landing time, not merely the solver's coarse clock."""
    from .routing_planner import materialize
    output=[];reserve=[]
    for v,route in enumerate(routes):
        while route:
            single=[[] for _ in routes];single[v]=route
            sorties,violations=materialize(model,single)
            if not violations and all(s['end_s']<=model.g.horizon+1e-6 for s in sorties):
                output.extend(sorties);break
            route=trim_last_survey(model,route)
    return output,reserve


def improve_in_windows(graph, bundle, settings, scene, baseline, emit):
    began=time.perf_counter();windows=allowed_windows(graph)
    report=dict(attempted=False,adopted=False,windows=[dict(start_s=a,end_s=b) for a,b in windows],
                policy='whole transects; ground during union of temporal closures; intermediate ends must allow refuelling and final landing',
                baseline_feasible=baseline['checks']['passed'],baseline_metrics=baseline['metrics'],stages=[])
    if len(windows)<2:
        report['reason']='no_split_operating_window';return baseline,report
    report['attempted']=True
    # A bounded deterministic share of the search budget for each window.
    options=replace(settings,time_budget_s=max(2.,min(30.,settings.time_budget_s/len(windows))))
    remaining=set(graph.tids);positions={};ready={};combined=[];actions=[]
    for number,window in enumerate(windows):
        if not remaining:break
        emit(dict(stage='routing_windows',window=number+1,windows=len(windows),remaining=len(remaining)))
        g=window_graph(graph,remaining,window,positions,ready,number==len(windows)-1)
        model=RoutingModel(g,options,diagnostic=True)
        routes,search=model.solve()
        best=None;trials=[]
        for route in routes[:3]:
            sorties,reserve=fit_routes(model,route)
            if not sorties:continue
            sorties,conflicts,repairs=repair_schedule(sorties,list(g.fleet.values()),options,scene,g.horizon,
                                                     max_conflict_rounds=8)
            assigned={tid for s in sorties for tid in s['task_ids']}
            partial=dict(sorties=sorties,unassigned=[dict(task_id=tid,reason='next_window')
                                                   for tid in g.tids if tid not in assigned])
            check=check_plan(g.bundle,partial,options,scene)
            failures=[v for v in check['violations'] if v['code']!='unassigned_task']+reserve
            if any(s['start_s']<window[0]-1e-6 or s['end_s']>window[1]+1e-6 for s in sorties):
                failures.append(dict(code='window_overrun'))
            trials.append(dict(assigned=len(assigned),violations=failures[:10]))
            if failures:continue
            score=(-len(assigned),metrics(sorties)[settings.objective+'_s'],metrics(sorties)['total_flight_s'])
            if best is None or score<best[0]:best=(score,sorties,repairs,assigned)
        stage=dict(window=number+1,start_s=window[0],end_s=window[1],remaining_before=len(remaining),
                   search=search,trials=trials,completed=0)
        report['stages'].append(stage)
        if best is None:continue
        _,sorties,repairs,assigned=best
        # Rename stage-local sortie IDs, including their diagnostic references.
        renames={}
        for s in sorties:
            uid=s['uav_id'];old=s['id'];index=sum(x['uav_id']==uid for x in combined)+1
            s['id']=f'{uid}_S{index}';s['index']=index;renames[old]=s['id'];combined.append(s)
            positions[uid]=s['landing_site_id'];ready[uid]=s['end_s']+g.fleet[uid]['service_time_s']
        for action in repairs:
            action=dict(action)
            if action.get('sortie') in renames:action['sortie']=renames[action['sortie']]
            actions.append(action)
        remaining-=assigned;stage['completed']=len(assigned);stage['remaining_after']=len(remaining)
    trial=dict(baseline,sorties=combined,metrics=metrics(combined),
               unassigned=[dict(task_id=tid,reason='no_feasible_slot_in_allowed_windows') for tid in graph.tids if tid in remaining],
               deconfliction=dict(remaining=[],actions=actions))
    check=check_plan(bundle,trial,settings,scene)
    if any(s['start_s']<graph.window_start-1e-6 or s['end_s']>graph.horizon+1e-6 for s in combined):
        check['violations'].append(dict(code='daylight_window'))
    check['passed']=not check['violations'];trial['checks']=check
    trial['status']='FEASIBLE' if check['passed'] else 'UNRESOLVED'
    report.update(candidate_feasible=check['passed'],candidate_metrics=trial['metrics'],
                  remaining=len(remaining),violations=check['violations'][:30],elapsed_s=time.perf_counter()-began)
    secondary='total_flight_s' if settings.objective=='makespan' else 'makespan_s'
    score=lambda x:(x['metrics'][settings.objective+'_s'],x['metrics'][secondary])
    if check['passed'] and (not baseline['checks']['passed'] or score(trial)<score(baseline)):
        report.update(adopted=True,reason='feasible_window_plan_improves_or_rescues_baseline')
        return trial,report
    report['reason']='window_candidate_failed_validation' if not check['passed'] else 'baseline_is_better_or_equal'
    return baseline,report
