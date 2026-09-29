from copy import deepcopy
from types import SimpleNamespace

import pytest
from shapely.geometry import box, mapping

from h2.scenarios import make_bundle
from h2.scheduler import Settings
from h2.planner import plan_bundle
from h2.routing_windows import allowed_windows, window_graph
from h2.routing_graph import RoutingGraph
from h1_coverage.transect_bundle import export_transect_bundle


def options(objective='makespan'):
    return Settings(algorithm='routing',search_depth='quick',time_budget_s=4,objective=objective,seed=123)


def scene(close=200,open_=600,end=2000):
    return dict(schema_version='h2.scene.v1',crs=32637,
                operating_window=dict(start_s=0.,end_s=float(end)),
                temporal=[dict(id='pause',geometry=mapping(box(499000,5999000,502000,6002000)),
                               start_s=float(close),end_s=float(open_),min_alt_m=0.,max_alt_m=2000.)])


def test_window_union_clips_merges_and_keeps_boundary_clearance():
    g=SimpleNamespace(window_start=100,horizon=1000,scene=dict(temporal=[
        dict(start_s=250,end_s=400),dict(start_s=200,end_s=300),
        dict(start_s=-50,end_s=120),dict(start_s=800,end_s=1100)]))
    assert allowed_windows(g)==[(120.2,199.8),(400.2,799.8)]
    g.scene={}
    assert allowed_windows(g)==[]


def test_sequential_windows_complete_once_return_before_closure_and_are_deterministic():
    b=make_bundle(6,1);s=scene();before=deepcopy(b)
    a=plan_bundle(b,options(),s);again=plan_bundle(b,options(),s)
    assert a['checks']['passed'],a['checks']
    report=a['routing']['window_search']
    assert report['adopted'],report
    assert report['candidate_metrics']['makespan_s']<report['baseline_metrics']['makespan_s']
    first,last=a['sorties'][0],a['sorties'][-1]
    assert first['end_s']<200 and last['start_s']>600
    assert last['start_s']>=first['end_s']+b['fleet'][0]['service_time_s']
    tasks=[tid for sortie in a['sorties'] for tid in sortie['task_ids']]
    assert len(tasks)==len(set(tasks))==6
    assert a['routing']['solution_sha256']==again['routing']['solution_sha256']
    assert b==before


def test_total_flight_keeps_cheaper_unsplit_baseline():
    result=plan_bundle(make_bundle(6,1),options('total_flight'),scene())
    r=result['routing']['window_search']
    assert result['checks']['passed'] and r['candidate_feasible'],r
    assert not r['adopted'] and r['reason']=='baseline_is_better_or_equal'
    assert r['candidate_metrics']['total_flight_s']>r['baseline_metrics']['total_flight_s']


def test_window_view_preserves_physics_and_continuity_and_service_ready():
    b=export_transect_bundle(make_bundle(3,1));g=RoutingGraph(b,options(),scene())
    remaining={g.tids[1]};view=window_graph(g,remaining,(600.2,2000),{'u0':'base'},{'u0':750.},True)
    assert view.ready['u0']==750 and view.policy['u0']['start']==['base']
    assert view.tids==[g.tids[1]] and len(view.by_uav['u0']['services'])==2
    assert view.by_uav['u0']['records'] is g.by_uav['u0']['records']
    assert len(g.tasks)==3 and not hasattr(g,'ready')


def test_no_permission_to_refuel_does_not_create_extra_sorties():
    b=make_bundle(6,1);b['fleet'][0]['refuel_sites']=[]
    result=plan_bundle(b,options(),scene())
    assert result['checks']['passed'],result['checks']
    assert len(result['sorties'])==1
    assert result['routing']['window_search']['stages'][0]['completed']==0


def test_window_strategy_rescues_infeasible_baseline_with_tight_deadline():
    result=plan_bundle(make_bundle(6,1),options(),scene(end=1000))
    report=result['routing']['window_search']
    assert not report['baseline_feasible'] and report['adopted'],report
    assert result['checks']['passed'] and not result['unassigned']
    assert result['metrics']['makespan_s']<=1000


def test_readiness_and_task_window_outside_current_slot_are_enforced():
    from h2.routing_solver import RoutingModel
    from h2.routing_planner import materialize
    b=export_transect_bundle(make_bundle(1,1));g=RoutingGraph(b,options(),scene())
    view=window_graph(g,set(g.tids),(600.2,2000),{'u0':'base'},{'u0':750.},True)
    m=RoutingModel(view,options(),diagnostic=True);routes,_=m.solve()
    sorties,_=materialize(m,routes[0])
    assert sorties and sorties[0]['start_s']>=750
    restricted=options();restricted.task_windows={g.tids[0]:(2500,3000)}
    m=RoutingModel(view,restricted,diagnostic=True);routes,_=m.solve()
    assert all(not route for candidate in routes for route in candidate)


def test_incomplete_window_candidate_is_not_reported_as_complete():
    result=plan_bundle(make_bundle(6,1),options(),scene(end=800))
    report=result['routing']['window_search']
    assert not report['candidate_feasible'] and report['remaining']>0
    assert not report['adopted'] and not result['checks']['passed']


def test_auxiliary_solver_error_keeps_checked_baseline(monkeypatch):
    from h2 import routing_windows
    def fail(*args):raise RuntimeError('CP Solver fail')
    monkeypatch.setattr(routing_windows,'improve_in_windows',fail)
    result=plan_bundle(make_bundle(3,1),options(),scene())
    assert result['checks']['passed']
    assert result['routing']['window_search']['reason']=='window_search_error'
