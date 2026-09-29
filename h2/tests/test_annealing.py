from copy import deepcopy
from itertools import product
import random

import pytest
from h2.scenarios import make_bundle
from h2.planner import plan_bundle, Settings
from h2.fixed_estimator import FixedPlanEstimator
from h2.anneal_schedule import SequenceEvaluator
from h2.annealing import initial_state, neighbor
from h2.anneal_repair import repair_schedule, shift_departure


def scattered():
    b=make_bundle(8,1,endurance_min=180)
    for i,t in enumerate(b['tasks']):
        for coords in (t['geom_coords'],t['transects'][0]['coords']):
            for p in coords:p[0]+=5000 if i%2 else 0
        t['entry']=t['geom_coords'][0][:];t['exit']=t['geom_coords'][-1][:]
    return b


@pytest.mark.parametrize('objective',['makespan','total_flight'])
def test_geographic_order_improves_and_is_reproducible(objective):
    b=scattered();original=deepcopy(b)
    options=Settings(search_depth='quick',time_budget_s=15,seed=20260918,objective=objective)
    first=plan_bundle(b,options);second=plan_bundle(b,options)
    assert first['status']=='FEASIBLE',first['checks']
    assert first['sorties']==second['sorties']
    assert first['annealing']['solution_sha256']==second['annealing']['solution_sha256']
    assert first['annealing']['iterations']==second['annealing']['iterations']
    assert first['metrics'][objective+'_s']<first['annealing']['legacy_objective_s']*.7
    assert first['annealing']['accepted_uphill']>0
    assert first['annealing']['iterations']<700
    assert b==original


def test_neighbors_preserve_coverage_eligibility_and_variants():
    b=make_bundle(7,3);b['feasibility']['eligible_uav_ids_by_task']['t0']=['u0']
    e=SequenceEvaluator(FixedPlanEstimator(b))
    p=plan_bundle(b,Settings(max_iterations=0,cpsat=False))
    state=initial_state(e,p);rng=random.Random(20260918)
    for move in ('variant','relocate','swap','reorder','two_opt')*20:
        state,_=neighbor(e,state,rng,move)
        assert sorted(v[0] for seq in state for v in seq)==sorted(e.e.tasks)
        assert all(uid in e.e.eligible[v[0]] for uid,seq in zip(e.uids,state) for v in seq)
        assert all(v in e.options[uid,v[0]] for uid,seq in zip(e.uids,state) for v in seq)


@pytest.mark.parametrize('objective',['makespan','total_flight'])
def test_base_returns_match_exhaustive_partitions(objective):
    b=make_bundle(5,1,endurance_min=8);settings=Settings(objective=objective)
    e=FixedPlanEstimator(b,settings=settings);seq=SequenceEvaluator(e)
    visits=tuple((t['id'],'primary',False) for t in b['tasks'])
    best=seq.evaluate((visits,));assert best['missing']==0
    brute=[]
    for cuts in product([False,True],repeat=4):
        chunks=[[]]
        for i,v in enumerate(visits):
            chunks[-1].append(v)
            if i<4 and cuts[i]:chunks.append([])
        plan={'sorties':[dict(uav_id='u0',start_site_id='base',landing_site_id='base',
              task_ids=[v[0] for v in chunk]) for chunk in chunks]}
        report=e.evaluate(plan)
        if report['status']=='ESTIMATED_FEASIBLE':brute.append(report[objective+'_s'])
    assert best['objective']==pytest.approx(min(brute))
    assert e.evaluate(seq.fixed_plan(best))['status']=='ESTIMATED_FEASIBLE'


def test_single_task_stops_without_annealing_loop():
    p=plan_bundle(make_bundle(1,1),Settings(search_depth='deep',time_budget_s=180))
    assert p['status']=='FEASIBLE'
    assert p['annealing']['iterations']==0
    assert p['annealing']['stop_reasons']==['trivial_exhaustive']


def crossing_routes():
    b=make_bundle(1,2)
    routes=[]
    for i in range(2):
        pts=[(-500,0),(500,0)] if i==0 else [(0,-500),(0,500)]
        routes.append(dict(id=f'u{i}_S1',index=1,uav_id=f'u{i}',start_s=0.,end_s=100.,task_times=[],
            waypoints=[dict(x=x,y=y,z_m=60.,agl_m=60.,t_s=j*100.,phase='transit') for j,(x,y) in enumerate(pts)]))
    return b,routes


def test_conflict_uses_small_shift_and_preserves_input():
    b,routes=crossing_routes();original=deepcopy(routes)
    repaired,conflicts,actions=repair_schedule(routes,b['fleet'],Settings(),horizon=200)
    assert not conflicts
    assert actions and max(s['end_s'] for s in repaired)<110
    assert routes==original


def test_repair_budget_exhaustion_keeps_conflicts_visible():
    b,routes=crossing_routes();original=deepcopy(routes)
    repaired,conflicts,actions=repair_schedule(routes,b['fleet'],Settings(),horizon=200,max_conflict_rounds=0)
    assert repaired==original and conflicts and not actions
    repaired,conflicts,actions=repair_schedule(routes,b['fleet'],Settings(),horizon=200,max_conflict_rounds=1)
    assert not conflicts and len(actions)==1


def test_delay_propagates_only_needed_service_gap():
    b,routes=crossing_routes();later=deepcopy(routes[0]);later.update(id='u0_S2',index=2,start_s=140.,end_s=240.)
    for p in later['waypoints']:p['t_s']+=140
    routes.append(later)
    shifted=shift_departure(routes,'u0_S1',15.,{u['id']:u for u in b['fleet']})
    assert shifted[-1]['start_s']==145.
    assert shifted[1]==routes[1]


def test_unassigned_tasks_never_disappear_from_status():
    b=make_bundle(2,1,endurance_min=.1)
    p=plan_bundle(b,Settings(search_depth='quick',time_budget_s=1))
    assert p['status']!='FEASIBLE'
    assert len(p['unassigned'])==2
    assert p['annealing']['final_unassigned']==2


def test_daylight_start_is_included_before_h3():
    b=make_bundle(2,1)
    scene=dict(schema_version='h2.scene.v1',crs=b['crs'],operating_window={'start_s':600.,'end_s':1800.})
    p=plan_bundle(b,Settings(search_depth='quick',time_budget_s=1),scene)
    assert p['status']=='FEASIBLE',p['checks']
    assert min(s['start_s'] for s in p['sorties'])>=600
    assert max(s['end_s'] for s in p['sorties'])<=1800


def test_temporal_delay_uses_crossing_time_and_is_rechecked():
    from shapely.geometry import box,mapping
    b=make_bundle(1,1)
    points=[dict(x=i*100.,y=0.,z_m=60.,agl_m=60.,t_s=i*10.,phase='transit') for i in range(11)]
    route=dict(id='u0_S1',index=1,uav_id='u0',start_s=0.,end_s=100.,task_times=[],waypoints=points)
    scene={'temporal':[dict(geometry=mapping(box(801,-10,899,10)),start_s=0.,end_s=90.)]}
    routes,conflicts,actions=repair_schedule([route],b['fleet'],Settings(),scene,horizon=200.)
    assert not conflicts and actions
    assert 0<routes[0]['start_s']<30
    assert actions[0]['action']=='shift_time_window'
    # Full H2 segment semantics must also pass: no overlapping segment may cross.
    polygon=box(801,-10,899,10)
    from shapely.geometry import LineString
    assert not any(a['t_s']<=90 and LineString([(a['x'],a['y']),(c['x'],c['y'])]).intersects(polygon)
                   for a,c in zip(routes[0]['waypoints'],routes[0]['waypoints'][1:]))


def test_total_flight_keeps_checked_incumbent_when_grid_is_more_expensive():
    p=plan_bundle(make_bundle(2,1),Settings(search_depth='quick',time_budget_s=3,objective='total_flight'))
    assert p['status']=='FEASIBLE'
    assert p['annealing']['incumbent_reserve_passed']
    assert p['metrics']['total_flight_s']<=p['annealing']['legacy_objective_s']+1e-6


@pytest.mark.parametrize('count,expected', [(1,2),(2,4)])
def test_annealing_enumerates_h1_variants_without_changing_geometry(count,expected):
    from test_route_variants import variant_bundle
    b=variant_bundle(count);before=deepcopy(b)
    e=SequenceEvaluator(FixedPlanEstimator(b))
    assert len(e.options['u0','t0'])==expected
    p=plan_bundle(b,Settings(search_depth='quick',time_budget_s=1))
    assert p['status']=='FEASIBLE',p['checks']
    assert b==before and p['tasks']==b['tasks']
    assert p['annealing']['stop_reasons']==['trivial_exhaustive']


def test_shared_profile_never_expands_individual_uav_eligibility():
    b=make_bundle(1,2)
    b['fleet'][1]={**b['fleet'][0],'id':'u1'}
    b['feasibility']['eligible_uav_ids_by_task']['t0']=['u1']
    e=SequenceEvaluator(FixedPlanEstimator(b))
    assert e.options['u0','t0']==()
    assert len(e.options['u1','t0'])==2
    assert not e.fragments('u0',(('t0','primary',False),),'base')
    p=plan_bundle(b,Settings(search_depth='quick',time_budget_s=1))
    assert p['status']=='FEASIBLE'
    assert {s['uav_id'] for s in p['sorties']}=={'u1'}
