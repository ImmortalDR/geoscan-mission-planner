from copy import deepcopy

from h1_coverage.transect_bundle import export_transect_bundle
from h2.scenarios import make_bundle
from h2.scheduler import Settings
from h2.routing_graph import RoutingGraph
from h2.routing_solver import RoutingModel
from h2.routing_seed import sequence_route
from h2.routing_blocks import refine_blocks,task_sequences


def model(bundle,objective='makespan'):
    s=Settings(algorithm='routing',objective=objective,search_depth='quick',time_budget_s=4,survey_speed_factor=1.)
    return RoutingModel(RoutingGraph(export_transect_bundle(bundle),s),s)


def test_scrambled_zone_becomes_snake_without_changing_ownership_or_geometry():
    b=make_bundle(5,1,180);original=deepcopy(b);m=model(b)
    routes=[sequence_route(m,0,[3,1,4,0,2])]
    improved,report=refine_blocks(m,routes,80)
    assert improved and report['final_cost']<report['initial_cost']
    seq=task_sequences(m,improved[-1][1])[0]
    assert seq in [list(range(5)),list(reversed(range(5)))]
    assert any(a['operation']=='snake_order' for a in report['accepted'])
    again,other=refine_blocks(m,routes,80)
    assert again==improved and other['accepted']==report['accepted']
    assert b==original and report['evaluations']<=80


def test_whole_snake_moves_to_faster_compatible_aircraft():
    b=make_bundle(4,2,60);b['fleet'][0]['ground_speed_ms']=5.
    m=model(b,'total_flight')
    routes=[sequence_route(m,0,list(range(4))),[]]
    improved,report=refine_blocks(m,routes,100)
    assert improved and any(a['operation']=='move_snake' for a in report['accepted'])
    seq=task_sequences(m,improved[-1][1])
    assert not seq[0] and sorted(seq[1])==list(range(4))


def test_swap_complete_zones_between_aircraft_at_opposite_bases():
    b=make_bundle(4,2,30)
    b['sites'].append(dict(b['sites'][0],id='east',x=504000.))
    b['fleet'][1].update(start_site='east',landing_site='east',refuel_sites=[])
    b['fleet'][0]['refuel_sites']=[]
    for i,t in enumerate(b['tasks']):
        t['job_id']='west' if i<2 else 'east'
        t['transects'][0]['job_id']=t['job_id']
        if i>=2:
            for coords in (t['geom_coords'],t['transects'][0]['coords']):
                for p in coords:p[0]+=4000.
            t['entry']=t['geom_coords'][0][:];t['exit']=t['geom_coords'][-1][:]
    m=model(b)
    routes=[sequence_route(m,0,[2,3]),sequence_route(m,1,[0,1])]
    assert all(routes)
    improved,report=refine_blocks(m,routes,160)
    assert improved and any(a['operation']=='swap_snakes' for a in report['accepted'])
    seq=task_sequences(m,improved[-1][1])
    assert sorted(seq[0])==[0,1] and sorted(seq[1])==[2,3]


def test_block_transfer_cannot_bypass_sensor_eligibility():
    b=make_bundle(4,2,60);b['fleet'][0]['ground_speed_ms']=5.
    b['feasibility']['eligible_uav_ids_by_task']={t['id']:['u0'] for t in b['tasks']}
    m=model(b,'total_flight');routes=[sequence_route(m,0,list(range(4))),[]]
    improved,report=refine_blocks(m,routes,80)
    assert all(not task_sequences(m,r)[1] for _,r in improved)
    assert not any(a['operation'] in ('move_snake','swap_snakes') for a in report['accepted'])
