from copy import deepcopy
import math

import pytest
from h1_coverage.coverage.atomic import build_atomic_tasks
from h1_coverage.models import Transect
from h1_coverage.bundle import _task
from h2.contract import ContractError, load_bundle
from h2.scenarios import make_bundle
from h2.planner import plan_bundle, Settings
from h2.checks import check_plan
from h2.scheduler import Scheduler


def variant_bundle(count=2):
    bundle = make_bundle(1, 1)
    coords = [[[500100.,6000000.], [500100.,6000400.]],
              [[500200.,6000500.], [500200.,6000000.]]][:count]
    lines = [Transect(coords=[tuple(p) for p in c], length_m=math.dist(*c), job_id='job') for c in coords]
    task = build_atomic_tasks([lines], job_id='job', payload_class='rgb',
        payload_profile_id='rgb', agl_m=60, sweep_angle_deg=0,
        fixed_wing_safe_flags=[True])[0]
    task.id = 't0'
    task.alternate_fixed_wing_safe = True
    bundle['tasks'] = [_task(task)]
    bundle['schema_version'] = 'gmp.h1_h2.v2'
    return load_bundle(bundle)


@pytest.mark.parametrize('variant,reverse', [('primary',False),('primary',True),('alternate',False),('alternate',True)])
def test_each_of_four_routes_selected_and_checked(variant, reverse):
    bundle = variant_bundle()
    task = bundle['tasks'][0]
    route = next(v for v in task['route_variants'] if v['id'] == variant)
    path = route['geom_coords'][::-1] if reverse else route['geom_coords']
    bundle['sites'][0].update(x=path[0][0]-10, y=path[0][1])
    original = deepcopy(bundle)
    plan = plan_bundle(bundle, Settings(max_iterations=0,cpsat=False))
    assert plan['status'] == 'FEASIBLE', plan['checks']
    sortie = plan['sorties'][0]
    assert sortie['task_variants'] == [variant]
    assert sortie['task_reversed'] == [reverse]
    survey = [p for p in sortie['waypoints'] if p['phase'] == 'survey']
    assert [[p['x'], p['y']] for p in survey] == path
    expected = (task['survey_length_m']+route['internal_transition_m'])/7.5 + 6
    assert survey[-1]['t_s']-survey[0]['t_s'] == pytest.approx(expected)
    assert bundle == original
    from h2.integration import to_gmp_plan
    from gmp.api.h2_adapter import export_sorties
    converted = to_gmp_plan(bundle, plan)
    assert converted.sorties[0].task_variants == [variant]
    assert converted.tasks['t0'].path_coords(reverse, variant) == [tuple(p) for p in path]
    assert export_sorties(bundle,plan)[0]['task_variants'] == [variant]
    bad = deepcopy(plan)
    bad['sorties'][0]['task_variants'] = ['alternate' if variant == 'primary' else 'primary']
    assert not check_plan(bundle,bad)['passed']


def test_one_line_has_two_directions_only():
    bundle = variant_bundle(1)
    assert len(bundle['tasks'][0]['route_variants']) == 1
    for reverse in [False,True]:
        path = bundle['tasks'][0]['geom_coords'][::-1] if reverse else bundle['tasks'][0]['geom_coords']
        bundle['sites'][0].update(x=path[0][0]-10,y=path[0][1])
        sortie = plan_bundle(bundle,Settings(max_iterations=0,cpsat=False))['sorties'][0]
        assert sortie['task_variants'] == ['primary']
        assert sortie['task_reversed'] == [reverse]


@pytest.mark.parametrize('damage', ['coordinate','length','missing','unknown'])
def test_reject_corrupt_variant(damage):
    bundle = variant_bundle()
    variant = bundle['tasks'][0]['route_variants'][1]
    if damage == 'coordinate': variant['geom_coords'][1][0] += 1
    if damage == 'length': variant['internal_transition_m'] += 1
    if damage == 'missing': bundle['tasks'][0]['route_variants'].pop()
    if damage == 'unknown': variant['id'] = 'invented'
    with pytest.raises(ContractError): load_bundle(bundle)


def test_forbidden_alternate_connection_falls_back_to_primary():
    bundle = variant_bundle()
    bundle['sites'][0].update(x=500090,y=6000400)
    # Only the alternate's southern connector crosses this small NFZ.
    scene = dict(schema_version='h2.scene.v1',crs=bundle['crs'], forbidden=[dict(type='Polygon',
        coordinates=[[[500140,5999990],[500160,5999990],[500160,6000010],[500140,6000010],[500140,5999990]]])])
    plan = plan_bundle(bundle, Settings(max_iterations=0,cpsat=False), scene)
    assert plan['status'] == 'FEASIBLE',plan['checks']
    assert plan['sorties'][0]['task_variants'] == ['primary']


def test_fixed_wing_rejects_unsafe_alternate_without_expanding_eligibility():
    bundle = variant_bundle()
    bundle['fleet'][0]['uav_class'] = 'fixed_wing'
    bundle['tasks'][0]['route_variants'][1]['fixed_wing_safe'] = False
    bundle['sites'][0].update(x=500090,y=6000400)
    plan = plan_bundle(bundle, Settings(max_iterations=0,cpsat=False))
    assert plan['status'] == 'FEASIBLE',plan['checks']
    assert plan['sorties'][0]['task_variants'] == ['primary']


def test_relative_survey_timing_independent_of_base_with_terrain():
    bundle = variant_bundle()
    # Two nearby bases select the same directed variant, but change arrival time.
    profiles = []
    for x in [500000,500050]:
        bundle['sites'][0].update(x=x,y=6000000)
        scheduler = Scheduler(bundle,Settings(max_iterations=0,cpsat=False),None)
        scheduler.context.has_terrain = True
        scheduler.context.elevation = lambda x,y: 0.6*abs(y-6000200)
        route = scheduler.route('u0',('t0',),'base','base')
        points = [p for p in route['waypoints'] if p['phase']=='survey']
        start = points[0]['t_s']
        profiles.append([(p['x'],p['y'],p['z_m'],p['t_s']-start) for p in points])
    assert len(profiles[0]) == len(profiles[1])
    for a,b in zip(*profiles): assert a == pytest.approx(b)
