from copy import deepcopy
from itertools import permutations, product
import math

import pytest
import numpy as np

from h1_coverage.transect_bundle import export_transect_bundle
from h2.contract import validate_bundle, ContractError
from h2.scenarios import make_bundle
from h2.scheduler import Settings
from h2.planner import plan_bundle
from h2.routing_graph import RoutingGraph
from h2.routing_solver import RoutingModel
from h2.checks import check_plan
from h2.routing_altitude import transit_envelope
from h2.routing_seed import sweep_routes


def options(objective='makespan'):
    return Settings(algorithm='routing',search_depth='quick',time_budget_s=4,objective=objective)


def group(n=3,fleet=2,endurance=30):
    b=make_bundle(n,fleet,endurance)
    lines=[t['transects'][0] for t in b['tasks']]
    for i,line in enumerate(lines):
        if i%2:line['coords'].reverse()
    t=b['tasks'][0]
    coords=[p for line in lines for p in line['coords']]
    internal=sum(math.dist(a['coords'][-1],z['coords'][0]) for a,z in zip(lines,lines[1:]))
    t.update(transects=lines,geom_coords=coords,entry=coords[0],exit=coords[-1],survey_length_m=400*n,
             internal_transition_m=internal,turn_count=n-1)
    b['tasks']=[t];b['feasibility']['eligible_uav_ids_by_task']={'t0':[u['id'] for u in b['fleet']]}
    return b


def test_v3_splits_group_and_preserves_every_coordinate_and_requirement():
    b=group();original=deepcopy(b);v3=export_transect_bundle(b)
    assert b==original
    validate_bundle(v3)
    assert len(v3['tasks'])==3
    for i,t in enumerate(v3['tasks']):
        assert t['transects']==[b['tasks'][0]['transects'][i]]
        assert t['parent_task_id']=='t0' and t['transect_index']==i
        assert t['payload_profile_id']==b['tasks'][0]['payload_profile_id']
    broken=deepcopy(v3);broken['tasks'].pop();broken['feasibility']['eligible_uav_ids_by_task'].pop('t0#G0002')
    with pytest.raises(ContractError,match='missing transects'):validate_bundle(broken)


@pytest.mark.parametrize('objective',['makespan','total_flight'])
def test_complete_transects_both_metrics_and_repeated_identical_solution(objective):
    b=group(4,2);original=deepcopy(b)
    p=plan_bundle(b,options(objective));again=plan_bundle(b,options(objective))
    assert p['status']=='FEASIBLE',p['checks']
    assert p['routing']['solution_sha256']==again['routing']['solution_sha256']
    assert p['sorties']==again['sorties'] and b==original
    assigned=[t for s in p['sorties'] for t in s['task_ids']]
    assert len(assigned)==len(set(assigned))==4
    for s in p['sorties']:
        for tid,reverse in zip(s['task_ids'],s['task_reversed']):
            task=next(t for t in p['tasks'] if t['id']==tid)
            pts=[w for w in s['waypoints'] if w.get('task_id')==tid and w['phase']=='survey']
            expected=task['geom_coords'][::-1] if reverse else task['geom_coords']
            assert (pts[0]['x'],pts[0]['y'])==tuple(expected[0])
            assert (pts[-1]['x'],pts[-1]['y'])==tuple(expected[-1])


def test_group_can_be_split_across_aircraft():
    b=group(8,2,8);p=plan_bundle(b,options())
    assert p['status']=='FEASIBLE',p['checks']
    assert len({s['uav_id'] for s in p['sorties']})==2
    assert len(p['sorties'])>=3


@pytest.mark.parametrize('reverse',[False,True])
@pytest.mark.parametrize('split',[False,True])
def test_sweep_seed_keeps_neighbours_in_whole_groups_or_contiguous_strips(reverse,split):
    b=export_transect_bundle(group(8,2,30));s=options()
    m=RoutingModel(RoutingGraph(b,s),s)
    routes,missing=sweep_routes(m,reverse,split)
    assert not missing and routes is not None
    assert (routes,missing)==sweep_routes(m,reverse,split)
    owners={};used=0
    for v,route in enumerate(routes):
        tasks=[m.nodes[i].service//2 for i in route if m.nodes[i].kind=='survey']
        if not tasks:continue
        used+=1
        assert tasks==sorted(tasks,reverse=reverse)
        assert max(tasks)-min(tasks)+1==len(tasks)
        for i in tasks:
            assert i not in owners
            owners[i]=v
    assert len(owners)==8
    assert used==(2 if split else 1)
    ordered=[owners[i] for i in range(8)]
    assert sum(a!=z for a,z in zip(ordered,ordered[1:]))==(1 if split else 0)
    # Validate the proposed assignment with the full Routing constraints too.
    m.routing.CloseModel()
    assert m.routing.ReadAssignmentFromRoutes(routes,True) is not None


def test_compact_seed_respects_forced_alternating_eligibility():
    b=export_transect_bundle(group(6,2,30))
    for i,t in enumerate(b['tasks']):
        b['feasibility']['eligible_uav_ids_by_task'][t['id']]=[f'u{i%2}']
    s=options();m=RoutingModel(RoutingGraph(b,s),s)
    routes,missing=sweep_routes(m)
    assert routes is not None and not missing
    assigned=[]
    for v,route in enumerate(routes):
        for i in route:
            if m.nodes[i].kind=='survey':
                task=m.nodes[i].service//2
                assert v==task%2
                assigned.append(task)
    assert sorted(assigned)==list(range(6))


def test_individual_eligibility_is_not_shared_with_profile_cache():
    b=make_bundle(3,2)
    b['fleet'][1]=dict(b['fleet'][0],id='u1')
    b['feasibility']['eligible_uav_ids_by_task']={'t0':['u0'],'t1':['u1'],'t2':['u1']}
    p=plan_bundle(b,options())
    assert p['status']=='FEASIBLE'
    for s in p['sorties']:
        for tid in s['task_ids']:assert s['uav_id'] in b['feasibility']['eligible_uav_ids_by_task'][tid.split('#G')[0]]
    assert len(p['routing']['graph']['profiles'])==1


def test_no_compatible_aircraft_is_diagnostic_and_never_complete():
    b=make_bundle(3,1);b['feasibility']['eligible_uav_ids_by_task']['t2']=[]
    p=plan_bundle(b,options())
    assert p['status']!='FEASIBLE' and len(p['unassigned'])==1
    assert sum(len(s['task_ids']) for s in p['sorties'])==2
    assert not p['checks']['passed']


def test_repeat_refuelling_resets_only_after_actual_service():
    b=make_bundle(8,1,8);b['fleet'][0]['refuel_sites']=None
    p=plan_bundle(b,options())
    assert p['status']=='FEASIBLE',p['checks']
    assert len(p['sorties'])>1
    for a,z in zip(p['sorties'],p['sorties'][1:]):
        assert z['start_site_id']==a['landing_site_id']
        assert z['start_s']>=a['end_s']+b['fleet'][0]['service_time_s']-1e-6
    for s in p['sorties']:
        assert s['required_return_resource_s']<=8*60*.8+1e-6
    b['fleet'][0]['refuel_sites']=[]
    no=plan_bundle(b,options())
    assert no['status']!='FEASIBLE' and len(no['sorties'])<=1


def test_dummy_is_terminal_and_transfers_have_positive_physical_cost():
    b=make_bundle(2,1)
    b['sites'].append(dict(b['sites'][0],id='remote',x=502000.))
    b['fleet'][0].update(start_site=None,landing_site=None,refuel_sites=None)
    g=RoutingGraph(export_transect_bundle(b),options());m=RoutingModel(g,options())
    p=g.by_uav['u0'];a,z=g.bases.values()
    assert p['dist'][a,z]>100 and p['dist'][z,a]>100
    assert (a,z) not in g.edges and (z,a) not in g.edges
    assert all(v['kind']!='dummy' for v in g.vertices)
    for i,node in enumerate(m.nodes):
        if node.kind=='finish_base':
            assert list(m.valid[0][i].nonzero()[0])==[m.ends[0]]
        if node.kind in ('start_base','refuel'):
            assert all(not m.valid[0][i,j] for j,dest in enumerate(m.nodes)
                       if dest.kind in ('refuel','finish_base'))
    assert any(f['properties'].get('directed') for f in g.geojson()['features'])


def test_all_bases_connect_all_corners_and_shortcuts_are_sparse_deterministic():
    b=make_bundle(20,1)
    b['sites'].append(dict(b['sites'][0],id='far',x=505000.))
    bundle=export_transect_bundle(b)
    g=RoutingGraph(bundle,options());again=RoutingGraph(bundle,options())
    assert g.edges==again.edges and g.geojson()==again.geojson()
    for base in g.bases.values():
        for corner in g.corners:
            assert g.edges[base,corner]==g.edges[corner,base]=='base_link'
    assert not any(a in g.bases.values() and z in g.bases.values() for a,z in g.edges)
    count=sum(kind=='long_link' for kind in g.edges.values())//2
    assert 0<count<=g.long_links*len(g.corners)
    # Shortest physical transit cannot use a base as an intermediate waypoint.
    for a in g.corners:
        for z in g.corners:
            edges=g.path_edges('u0',a,z)
            assert not any(x in g.bases.values() or y in g.bases.values() for x,y in edges)


def boundary_topology(offsets, angle_rad=0.):
    # Isolate topology selection from DEM/solver costs, keeping alternating
    # H1 line orientation and both endpoints of each original transect.
    bundle=export_transect_bundle(group(len(offsets),1))
    g=RoutingGraph.__new__(RoutingGraph)
    g.tasks={t['id']:t for t in bundle['tasks']}
    rotation=np.array([[math.cos(angle_rad),-math.sin(angle_rad)],
                       [math.sin(angle_rad), math.cos(angle_rad)]])
    for t,y in zip(g.tasks.values(),offsets):
        coords=np.array([[0.,y],[400.,y]])@rotation.T
        if t['transect_index']%2:coords=coords[::-1]
        t.update(entry=coords[0].tolist(),exit=coords[1].tolist())
    g.vertices=[];g.ends={};g.bases={};g.edges={};g.sites={}
    g.knn=4;g.long_links=2;g.corner_spacing_m=300.
    g._topology()
    chosen={g.tasks[g.vertices[i]['task_id']]['transect_index'] for i in g.corners}
    return g,chosen


@pytest.mark.parametrize('angle_rad',[0.,.73,math.pi/2])
@pytest.mark.parametrize('offsets,expected',[
    ([0.],{0}),
    ([0.,300.],{0,1}),
    ([0.,150.,300.],{0,2}),
    ([0.,250.,500.],{0,1,2}),
    ([0.,100.,200.,300.,400.,500.],{0,2,5}),
    ([0.,10.,240.,260.,500.],{0,2,4}),
    ([0.,250.,500.,750.,1000.],{0,1,2,3,4}),
    # No invented transect when the only two available lines are >300 m apart.
    ([0.,500.],{0,1}),
])
def test_boundary_portals_bisect_physical_distance_uniformly(offsets,expected,angle_rad):
    g,chosen=boundary_topology(offsets,angle_rad)
    assert chosen==expected
    assert len(g.corners)==2*len(expected)==len(set(g.corners))
    for tid,t in g.tasks.items():
        assert all((i in g.corners)==(t['transect_index'] in expected) for i in g.ends[tid])


def test_middle_portals_get_physical_direct_base_links_and_preserve_input():
    bundle=export_transect_bundle(group(11,1));original=deepcopy(bundle)
    g=RoutingGraph(bundle,options());again=RoutingGraph(bundle,options())
    assert bundle==original and g.corners==again.corners and g.edges==again.edges
    assert len(g.corners)==6  # 500 m width: first, middle, last, on both sides.
    tid=g.tids[5];base=next(iter(g.bases.values()))
    for endpoint in g.ends[tid]:
        assert endpoint in g.corners
        assert g.edges[base,endpoint]==g.edges[endpoint,base]=='base_link'
        assert (base,endpoint) in g.by_uav['u0']['records']
        assert (endpoint,base) in g.by_uav['u0']['records']
    portals=[f for f in g.geojson()['features'] if f['geometry']['type']=='Point' and f['properties'].get('portal')]
    assert len(portals)==6 and g.report()['corner_spacing_m']==300.


def test_q_is_entry_threshold_and_covers_long_segment_interior():
    g=RoutingGraph(export_transect_bundle(make_bundle(2,1)),options())
    p=g.by_uav['u0'];a,z=g.ends[g.tids[0]]
    assert p['q'][a,z]>=p['dist'][a,z]+p['returns'][z]+g.return_margin-1e-8
    row=p['services'][0]
    assert row['required_s']>=row['time_s']+p['returns'][row['exit']]+g.return_margin-1e-8
    assert row['required_s']>row['time_s']


def test_small_total_flight_matches_exhaustive_order_direction_search():
    b=make_bundle(3,1);s=options('total_flight');g=RoutingGraph(export_transect_bundle(b),s)
    m=RoutingModel(g,s);best=math.inf
    for order in permutations(range(3)):
        for directions in product((0,1),repeat=3):
            route=[m.start_choices[0][0]]+[2*i+d for i,d in zip(order,directions)]+[m.finish_choices[0][0]]
            cost=sum(m.flight[0][a,z] for a,z in zip(route,route[1:]))/10
            best=min(best,cost)
    result=plan_bundle(b,s)
    assert result['status']=='FEASIBLE'
    assert result['metrics']['total_flight_s']==pytest.approx(best,abs=1.)


def test_straight_fence_chain_does_not_repeat_artificial_turns():
    g=RoutingGraph(export_transect_bundle(group(5,1)),options())
    p=g.by_uav['u0'];a=g.ends[g.tids[0]][0]
    # Alternating H1 orientation still has a continuous same-side fence.
    z=g.ends[g.tids[-1]][0]
    edges=g.path_edges('u0',a,z)
    assert len(edges)>1
    # One 90-degree departure plus one 90-degree arrival; zero internal turns.
    assert p['dist'][a,z]==pytest.approx(sum(p['weights'][edge] for edge in edges)+options().multirotor_turn_s)


def test_fence_continues_across_h1_chunks_of_the_same_zone():
    # These three parent tasks are adjacent individual lines from one job.
    g=RoutingGraph(export_transect_bundle(make_bundle(3,1)),options())
    a=g.ends[g.tids[0]][0];z=g.ends[g.tids[1]][0]
    assert g.edges[a,z]=='fence'
    assert set(g.by_uav['u0']['records'][a,z][:,4])=={60.}
    # Straight, level transit must take distance / speed, without a safety multiplier.
    expected=math.dist(g.vertices[a]['xy'],g.vertices[z]['xy'])/g.fleet['u0']['ground_speed_ms']
    assert g.by_uav['u0']['weights'][a,z]==pytest.approx(expected)
    assert g.by_uav['u0']['records'][a,z][-1,3]==pytest.approx(expected)


def test_transit_can_stay_above_depressions_without_lowering_clearance():
    xy=np.column_stack((np.arange(7)*30.,np.zeros(7)))
    ground=np.array([0.,30.,0.,40.,0.,20.,0.]);z=ground+60.
    times=np.r_[0.,np.cumsum(np.maximum(3.,np.abs(np.diff(z))/3.))]
    original=np.column_stack((xy,z,times,np.full(7,60.)))
    smoothed=transit_envelope(original,18.,3.)
    assert np.array_equal(original[[0,-1],:3],smoothed[[0,-1],:3])
    assert np.array_equal(original[[0,-1],4],smoothed[[0,-1],4])
    assert np.all(smoothed[:,2]>=original[:,2]) and np.all(smoothed[:,4]>=original[:,4])
    assert smoothed[-1,3]<original[-1,3]*.9
    assert np.all(np.abs(np.diff(smoothed[:,2]))/np.diff(smoothed[:,3])<=3.+1e-8)
    assert np.all(np.diff(smoothed[:,0])/np.diff(smoothed[:,3])<=10.+1e-8)


def test_explicit_refuel_policy_checks_final_base_and_intermediate_permission():
    b=make_bundle(7,1,8);b['fleet'][0]['refuel_sites']=None
    p=plan_bundle(b,options());v3=export_transect_bundle(b)
    assert p['status']=='FEASIBLE'
    v3['fleet'][0]['refuel_sites']=[]
    changed=deepcopy(p);changed.pop('input_sha256')
    check=check_plan(v3,changed,options())
    assert any(v['code']=='refuel_site_forbidden' for v in check['violations'])


def test_invalid_refuel_base_rejected():
    b=export_transect_bundle(make_bundle(1,1))
    b['sites'].append(dict(b['sites'][0],id='land',role='landing'))
    b['fleet'][0]['refuel_sites']=['land']
    with pytest.raises(ContractError,match='refuel site'):validate_bundle(b)


@pytest.mark.parametrize('objective',['makespan','total_flight'])
def test_geographic_order_improves_over_original_alternating_route(objective):
    b=make_bundle(8,1,180)
    for i,t in enumerate(b['tasks']):
        for coords in (t['geom_coords'],t['transects'][0]['coords']):
            for p in coords:p[0]+=5000 if i%2 else 0
        t['entry']=t['geom_coords'][0][:];t['exit']=t['geom_coords'][-1][:]
    old=plan_bundle(b,Settings(max_iterations=0,cpsat=False,objective=objective))
    new=plan_bundle(b,options(objective))
    assert new['status']=='FEASIBLE'
    assert new['metrics'][objective+'_s']<old['metrics'][objective+'_s']*.7
