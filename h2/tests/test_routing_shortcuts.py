from copy import deepcopy
import math

import numpy as np
import pytest
from shapely.geometry import box, mapping

from h1_coverage.transect_bundle import export_transect_bundle
from h2.scenarios import make_bundle
from h2.scheduler import Settings
from h2.routing_graph import RoutingGraph, transit_motion
from h2.routing_solver import RoutingModel
from h2.routing_shortcuts import TransitShortcuts
from h2.estimate_grid import Elevations
from h2.transit import TransitContext


def model(scene=None):
    settings=Settings(algorithm='routing',search_depth='quick',time_budget_s=4)
    return RoutingModel(RoutingGraph(export_transect_bundle(make_bundle(2,1)),settings,scene),settings)


def detour(m):
    g=m.g
    a=g.ends[g.tids[0]][1];b=g.ends[g.tids[1]][1];base=g.bases['base']
    return [(a,b),(b,base)]


def test_direct_shortcut_saves_time_includes_turns_and_preserves_graph():
    m=model();g=m.g;p=g.by_uav['u0'];edges=detour(m)
    records={edge:p['records'][edge].copy() for edge in edges}
    original=deepcopy(g.bundle)
    cut=TransitShortcuts(g)
    result=cut.shorten(m,0,0,m.finish_choices[0][0],edges,1000.)
    assert result is not None and cut.stats['removed_edges']==1
    assert cut.stats['saved_flight_s']>0
    points=result['trajectories'][0]
    assert tuple(points[0,:2])==g.vertices[edges[0][0]]['xy']
    assert tuple(points[-1,:2])==g.vertices[edges[-1][1]]['xy']
    # Eastward survey -> westward direct return: a full half-turn is charged.
    assert points[-1,3]>=50.+m.settings.multirotor_turn_s-1e-7
    assert result['required_s']>=points[-1,3]+p['landing']+g.return_margin
    assert all(np.array_equal(records[e],p['records'][e]) for e in edges)
    assert g.bundle==original
    again=TransitShortcuts(g).shorten(m,0,0,m.finish_choices[0][0],edges,1000.)
    assert np.array_equal(points,again['trajectories'][0])


def test_shortcut_cannot_cut_through_forbidden_area():
    scene=dict(schema_version='h2.scene.v1',crs=32637,
               forbidden=[mapping(box(500045.,5999995.,500055.,6000005.))])
    m=model(scene);cut=TransitShortcuts(m.g)
    assert cut.shorten(m,0,0,m.finish_choices[0][0],detour(m),1000.) is None
    assert cut.stats['blocked']==1 and cut.stats['accepted']==0


def test_faster_shortcut_still_requires_continuous_return_resource():
    m=model();cut=TransitShortcuts(m.g)
    assert cut.shorten(m,0,0,m.finish_choices[0][0],detour(m),1.) is None
    assert cut.stats['resource_rejections']>0 and cut.stats['accepted']==0


def test_unfavourable_physical_time_is_not_accepted(monkeypatch):
    m=model();cut=TransitShortcuts(m.g);direct=cut._direct
    def expensive(uid,a,b):
        row=direct(uid,a,b)
        points=row[2].copy();points[:,3]*=100.
        return row[:2]+(points,)+row[3:]
    monkeypatch.setattr(cut,'_direct',expensive)
    assert cut.shorten(m,0,0,m.finish_choices[0][0],detour(m),100000.) is None
    assert cut.stats['accepted']==0


def test_single_edge_is_not_a_shortcut_candidate():
    m=model();cut=TransitShortcuts(m.g)
    assert cut.shorten(m,0,0,m.finish_choices[0][0],detour(m)[1:],1000.) is None
    assert cut.stats['direct_evaluations']==0


def test_direct_motion_uses_terrain_and_vertical_speed_with_fixed_end_states():
    m=model();g=m.g;u=g.fleet['u0'];p=g.by_uav['u0']
    terrain=np.zeros((9,29));terrain[4,14]=300.
    context=TransitContext(dict(schema_version='h2.scene.v1',crs=32637,
        terrain=dict(origin=[499900.,5999900.],cell_size_m=25.,values=terrain.tolist())),32637)
    start=g.vertices[g.ends[g.tids[0]][1]];end=g.vertices[g.bases['base']]
    points=transit_motion(context,g.settings,Elevations(context),u,start,end,'shortcut',p['turn_seconds'],direct_only=True)
    assert np.max(points[:,2])>=360.
    assert points[-1,3]>math.dist(start['xy'],end['xy'])/u['ground_speed_ms']
    assert points[0,4]==points[-1,4]==60.
    assert np.all(np.abs(np.diff(points[:,2]))<=g.settings.vertical_speed_ms*np.diff(points[:,3])+1e-7)


@pytest.mark.parametrize('failed_check,extra_delay',[(True,0.),(False,1000.)])
def test_final_schedule_falls_back_on_failure_or_worse_metric(monkeypatch,failed_check,extra_delay):
    # Isolate the acceptance gate: geometric feasibility is covered above and
    # by check_plan tests; a shorter flight must not bypass this final gate.
    import h2.routing_planner as planner
    m=model();baseline=dict(checks=dict(passed=True),metrics=dict(makespan_s=100.,total_flight_s=100.))
    sorties=[dict(id='u0_S1',uav_id='u0',start_s=0.,end_s=90.,flight_time_s=90.,distance_m=900.)]
    def materialize(*args):
        args[-1].stats['accepted']=1
        return sorties,[]
    monkeypatch.setattr(planner,'materialize',materialize)
    monkeypatch.setattr(planner,'repair_schedule',lambda *a,**kw: (
        [dict(sorties[0],start_s=extra_delay,end_s=90.+extra_delay)],[],[]))
    monkeypatch.setattr(planner,'check_plan',lambda *a:dict(passed=not failed_check,
        violations=[dict(code='temporal_restriction')] if failed_check else []))
    result,report=planner.polish_transits(m,[],baseline,m.g.bundle,m.settings,None,lambda event:None)
    assert result is baseline and not report['adopted']
