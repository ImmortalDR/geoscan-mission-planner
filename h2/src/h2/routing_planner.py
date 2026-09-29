"""Transect Routing -> detailed motion -> existing repair/checks -> H3 adapter."""
from copy import deepcopy
from dataclasses import replace
import math
import time

import numpy as np

from .contract import fingerprint
from .routing_graph import RoutingGraph
from .routing_graph import angle
from .routing_solver import RoutingModel
from .routing_diagnostics import explain_task
from .anneal_repair import repair_schedule
from .checks import check_plan
from .planner import metrics


def materialize(model, routes, shortcuts=None):
    g=model.g;output=[];reserve_violations=[]
    for v,route in enumerate(routes):
        if not route:continue
        uid=model.uids[v];u=g.fleet[uid];p=g.by_uav[uid]
        ready=max(g.window_start,getattr(g,'ready',{}).get(uid,0.));start_node=None;start_id=None;visits=[]
        for node_id in route:
            node=model.nodes[node_id]
            if node.kind=='start_base':start_node=node;start_id=node_id;continue
            if node.kind=='survey':visits.append((node_id,node));continue
            if node.kind not in ('refuel','finish_base'):continue
            if start_node is None:raise ValueError('missing physical takeoff base')
            clock=ready;depart=clock;points=[];task_times=[];task_ids=[];reversed_=[]
            survey_length=transit_length=0.;required=0.
            current=start_node.physical
            current_node_id=start_id
            def point(vertex,agl,phase):
                xy=g.vertices[vertex]['xy']
                points.append(dict(x=xy[0],y=xy[1],z_m=float(p['elevation'](np.asarray(xy)))+agl,
                                   agl_m=agl,t_s=clock,phase=phase,task_id=None))
            def append(trajectory,phase,tid=None):
                nonlocal clock
                for x,y,z,t,h in trajectory:
                    points.append(dict(x=float(x),y=float(y),z_m=float(z),agl_m=float(h),
                                       t_s=clock+float(t),phase=phase,task_id=tid))
                clock+=float(trajectory[-1,3])
            def transfer(destination,destination_id):
                nonlocal current,current_node_id,transit_length,required,clock
                edges=g.path_edges(uid,current,destination)
                if shortcuts is not None:
                    usable=u['operational_endurance_min']*60*(1-u['energy_reserve_fraction'])
                    improved=shortcuts.shorten(model,v,current_node_id,destination_id,edges,usable-(clock-depart))
                    if improved is not None:
                        required=max(required,clock-depart+improved['required_s'])
                        for trajectory in improved['trajectories']:
                            transit_length+=float(np.linalg.norm(np.diff(trajectory[:,:2],axis=0),axis=1).sum())
                            append(trajectory,'transit')
                        current=destination;current_node_id=destination_id
                        return
                departure_turn=float(model.departure_turn[v][current_node_id,destination_id])
                arrival_turn=float(model.arrival_turn[v][current_node_id,destination_id])
                required=max(required,clock-depart+float(p['q'][current,destination]))
                previous_heading=None
                for index,(a,b) in enumerate(edges):
                    trajectory=p['records'][a,b].copy()
                    first,last=p['edge_headings'][a,b]
                    extra=departure_turn if index==0 else p['turn_seconds']*angle(previous_heading,first)
                    if index==len(edges)-1:extra+=arrival_turn
                    duration=float(trajectory[-1,3])
                    if duration>0:trajectory[:,3]*=(duration+extra)/duration
                    elif extra>0:trajectory[-1,3]=extra
                    transit_length+=float(np.linalg.norm(np.diff(trajectory[:,:2],axis=0),axis=1).sum())
                    append(trajectory,'transit')
                    previous_heading=last
                if not edges and departure_turn+arrival_turn>0:
                    clock+=departure_turn+arrival_turn
                    point(current,g.vertices[current].get('agl_m',u['cruise_agl_m']),'transit')
                current=destination
                current_node_id=destination_id
            point(current,0.,'takeoff');clock+=p['takeoff'];point(current,u['cruise_agl_m'],'takeoff')
            for visit_id,visit in visits:
                service=p['services'][visit.service];tid=service['task_id']
                transfer(service['entry'],visit_id);begin=clock
                required=max(required,clock-depart+service['required_s'])
                append(service['points'],'survey',tid);current=service['exit']
                task_ids.append(tid);reversed_.append(service['reverse'])
                task_times.append(dict(task_id=tid,start_s=begin,end_s=clock))
                survey_length+=g.tasks[tid]['survey_length_m']
            transfer(node.physical,node_id)
            point(current,u['cruise_agl_m'],'landing');clock+=p['landing'];point(current,0.,'landing')
            usable=u['operational_endurance_min']*60*(1-u['energy_reserve_fraction'])
            required=max(required,clock-depart)
            index=sum(s['uav_id']==uid for s in output)+1;sid=f'{uid}_S{index}'
            if required>usable+1e-5:reserve_violations.append(dict(code='routing_return_reserve',sortie_id=sid,required_s=required,usable_s=usable))
            output.append(dict(id=sid,index=index,uav_id=uid,start_site_id=start_node.site,landing_site_id=node.site,
                task_ids=task_ids,task_reversed=reversed_,task_variants=['primary']*len(task_ids),
                start_s=depart,end_s=clock,flight_time_s=clock-depart,distance_m=survey_length+transit_length,
                transit_length_m=transit_length,survey_length_m=survey_length,resource_margin_s=usable-(clock-depart),
                required_return_resource_s=required,waypoints=points,task_times=task_times))
            ready=clock+u['service_time_s'];start_node=node;start_id=node_id;visits=[]
    return output,reserve_violations


def polish_transits(model, routes, baseline, bundle, settings, scene, emit):
    """Keep the checked baseline unless shortened, repaired motion is better."""
    from .routing_shortcuts import TransitShortcuts
    started=time.perf_counter()
    if not baseline['checks']['passed']:
        return baseline,dict(adopted=False,reason='baseline_not_feasible')
    shortcuts=TransitShortcuts(model.g)
    sorties,reserve=materialize(model,routes,shortcuts)
    report=dict(shortcuts.stats,adopted=False,before_metrics=baseline['metrics'])
    if not shortcuts.stats['accepted'] or reserve:
        report.update(reason='no_resource_feasible_improvement',violations=reserve,
                      elapsed_s=time.perf_counter()-started)
        return baseline,report
    emit(dict(stage='full_validation',operation='transit_shortcuts',
              shortcuts=shortcuts.stats['accepted'],saved_flight_s=shortcuts.stats['saved_flight_s']))
    repair_limit={'quick':4,'standard':8,'deep':16}[settings.search_depth or 'standard']
    sorties,conflicts,actions=repair_schedule(sorties,bundle['fleet'],settings,scene,model.g.horizon,
                                             max_conflict_rounds=repair_limit)
    trial=dict(baseline,sorties=sorties,metrics=metrics(sorties),
               deconfliction=dict(remaining=conflicts,actions=actions))
    check=check_plan(bundle,trial,settings,scene)
    if any(s['start_s']<model.g.window_start-1e-6 or s['end_s']>model.g.horizon+1e-6 for s in sorties):
        check['violations'].append(dict(code='daylight_window'))
    check['passed']=not check['violations']
    trial['checks']=check
    def score(plan):
        m=plan['metrics']
        secondary='total_flight_s' if settings.objective=='makespan' else 'makespan_s'
        return (m[settings.objective+'_s'],m[secondary])
    report.update(after_metrics=trial['metrics'],violations=check['violations'][:30],
                  repairs=len(actions),elapsed_s=time.perf_counter()-started)
    if check['passed'] and score(trial)<score(baseline):
        report.update(adopted=True,reason='checked_objective_improvement')
        return trial,report
    report['reason']='validation_failed' if not check['passed'] else 'no_improvement_after_ground_delays'
    return baseline,report


def plan_routing(bundle, settings, scene=None, progress=None):
    began=time.perf_counter();events=[]
    def emit(event):
        event=dict(event,elapsed_s=time.perf_counter()-began);events.append(event)
        if progress:progress(event)
    # Parent task windows apply to the start of each complete transect. The
    # external H1 export preserves the parent ID for this explicit mapping.
    windows=dict(settings.task_windows)
    for t in bundle['tasks']:
        if t['parent_task_id'] in windows:windows[t['id']]=windows[t['parent_task_id']]
    settings=replace(settings,task_windows={t['id']:windows[t['id']] for t in bundle['tasks'] if t['id'] in windows})
    emit(dict(stage='graph_precomputation'))
    g=RoutingGraph(bundle,settings,scene,emit)
    graph_view=dict(schema='h2.routing_graph.v1',geojson=g.geojson(),summary=g.report())
    emit(dict(stage='graph_ready',routing_graph=graph_view))
    stage=time.perf_counter();model=RoutingModel(g,settings,emit)
    model_s=time.perf_counter()-stage
    candidates,search=model.solve()
    copy_expansions=[]
    if not candidates and search['reload_copy_limit_reached']:
        import gc
        copy_expansions.append(search)
        del model
        gc.collect()
        emit(dict(stage='routing_expand_reloads',message='Increasing exhausted reload-copy bound'))
        stage=time.perf_counter();model=RoutingModel(g,settings,emit,copy_multiplier=2)
        model_s+=time.perf_counter()-stage
        candidates,search=model.solve()
    strict_search=None
    if not candidates:
        import gc
        strict_search=search
        del model
        gc.collect()
        emit(dict(stage='routing_diagnostic',message='Complete search failed; explicit partial diagnostic'))
        stage=time.perf_counter();model=RoutingModel(g,settings,emit,copy_multiplier=2 if copy_expansions else 1,diagnostic=True)
        model_s+=time.perf_counter()-stage
        candidates,search=model.solve()
    final_started=time.perf_counter();finalists=[];diagnostics=[];finalist_routes={}
    for number,routes in enumerate(candidates):
        sorties,reserve=materialize(model,routes)
        assigned={tid for sortie in sorties for tid in sortie['task_ids']}
        omitted=len(bundle['tasks'])-len(assigned)
        raw_value=metrics(sorties)[settings.objective+'_s']
        # Repair only shifts departures forward; it cannot improve either
        # objective or add missing tasks. Once a physically checked candidate
        # dominates this lower bound, rechecking the inferior finalist cannot
        # change the result (also for explicit partial diagnostic plans).
        beaten=any(len(prior['unassigned'])<=omitted
                   and not any(v['code']!='unassigned_task' for v in prior['checks']['violations'])
                   and (len(prior['unassigned'])<omitted or prior['metrics'][settings.objective+'_s']<=raw_value)
                   for prior in finalists)
        if beaten:
            diagnostics.append(dict(candidate=number+1,skipped='dominated_before_ground_delays',lower_bound_s=raw_value))
            continue
        emit(dict(stage='full_validation',candidate=number+1,candidates=len(candidates)))
        # Do not spend the entire worker budget repeatedly repairing one poor
        # candidate. Remaining conflicts stay explicit; try the next finalist.
        repair_limit={'quick':4,'standard':8,'deep':16}[settings.search_depth or 'standard']
        sorties,conflicts,actions=repair_schedule(sorties,bundle['fleet'],settings,scene,g.horizon,
                                                max_conflict_rounds=repair_limit)
        result=dict(schema_version='h2.plan.v1',scene_id=bundle['scene_id'],input_sha256=fingerprint(bundle),
            input_schema_version=bundle['schema_version'],crs=bundle['crs'],time_origin=g.origin.isoformat(),
            tasks=deepcopy(bundle['tasks']),sorties=sorties,objective=settings.objective,
            unassigned=[explain_task(model,t['id']) for t in bundle['tasks']
                        if t['id'] not in {tid for sortie in sorties for tid in sortie['task_ids']}],
            metrics=metrics(sorties),solver_log=events,deconfliction=dict(remaining=conflicts,actions=actions),
            requires_h3_validation=True,certificate=None,
            assumptions=['Whole immutable transects; detailed physical fence/KNN graph with directed profile costs',
                         'Time-linear endurance; 60 s extra return margin; unpadded physical transit times',
                         'Constructive continuous escape via next graph endpoint; real emergency landing sites',
                         'Heading-aware shortest paths; temporal transit constraints checked and repaired before H3',
                         'Intermediate refuelling obeys explicit per-UAV permissions; H3 remains authoritative'])
        check=check_plan(bundle,result,settings,scene)
        check['violations'].extend(reserve)
        if any(s['start_s']<g.window_start-1e-6 or s['end_s']>g.horizon+1e-6 for s in sorties):check['violations'].append(dict(code='daylight_window'))
        check['passed']=not check['violations']
        result['checks']=check;result['status']='FEASIBLE' if check['passed'] else 'UNRESOLVED'
        diagnostics.append(dict(candidate=number+1,passed=check['passed'],metric_s=result['metrics'][settings.objective+'_s'],
                                violations=check['violations'][:30],repairs=len(actions)))
        finalists.append(result)
        finalist_routes[id(result)]=routes
    shortcut_report=dict(adopted=False,reason='no_finalist')
    if finalists:
        result=min(finalists,key=lambda x:(len(x['unassigned']),sum(v['code']!='unassigned_task' for v in x['checks']['violations']),x['metrics'][settings.objective+'_s']))
        result,shortcut_report=polish_transits(model,finalist_routes[id(result)],result,bundle,settings,scene,emit)
    else:
        result=dict(schema_version='h2.plan.v1',scene_id=bundle['scene_id'],input_sha256=fingerprint(bundle),
            input_schema_version=bundle['schema_version'],crs=bundle['crs'],time_origin=g.origin.isoformat(),
            tasks=deepcopy(bundle['tasks']),sorties=[],objective=settings.objective,
            unassigned=[explain_task(model,t['id']) for t in bundle['tasks']],
            metrics=metrics([]),solver_log=events,deconfliction=dict(remaining=[]),requires_h3_validation=True,
            certificate=None,assumptions=['Search failure is not a proof of physical infeasibility'],status='UNRESOLVED',
            checks=dict(passed=False,violations=[dict(code='no_complete_routing_solution_found',routing_status=search['status'])]))
    from .routing_windows import improve_in_windows
    try:
        result,window_report=improve_in_windows(g,bundle,settings,scene,result,emit)
    except (RuntimeError,ValueError,OverflowError) as exc:
        # An auxiliary Routing model must never discard an already checked plan.
        window_report=dict(attempted=True,adopted=False,reason='window_search_error',error=str(exc),
                           baseline_feasible=result['checks']['passed'])
    result['routing_graph']=graph_view
    result['routing']=dict(graph=g.report(),model_s=model_s,**search,final_validation_s=time.perf_counter()-final_started,
        transit_shortcuts=shortcut_report,
        window_search=window_report,
        finalist_checks=diagnostics,total_s=time.perf_counter()-began,objective=settings.objective,
        strict_search=strict_search,copy_expansions=copy_expansions,
        solution_sha256=fingerprint(dict(sorties=result['sorties'],unassigned=result['unassigned'],metrics=result['metrics'])))
    emit(dict(stage='complete',objective_value=result['metrics'][settings.objective+'_s'],unassigned_count=len(result['unassigned'])))
    return result
