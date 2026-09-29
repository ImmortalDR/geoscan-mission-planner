"""Optional H3 bridge. H2 JSON remains its own explicitly versioned format.

The bridge imports existing gmp models at call time only. It does not vendor or
modify H1/H3 and never labels a plan SAFE or creates a safety certificate.
"""
from copy import deepcopy
from datetime import timedelta
import math

from .contract import fingerprint, load_bundle, timestamp


def to_gmp_plan(bundle: dict, plan: dict, scene=None):
    from gmp.models import Plan, Sortie, Waypoint, AtomicTask, Transect
    from shapely.geometry import LineString

    bundle = load_bundle(bundle)
    if plan.get('input_sha256') != fingerprint(bundle):
        raise ValueError('plan does not match supplied H1 bundle')
    if plan.get('schema_version') != 'h2.plan.v1':
        raise ValueError('unsupported H2 plan schema')
    if scene is not None and getattr(scene, 'id', bundle['scene_id']) != bundle['scene_id']:
        raise ValueError('scene id mismatch')
    origin = timestamp(plan['time_origin'])
    tasks = {}
    for t in bundle['tasks']:
        tasks[t['id']] = AtomicTask(
            id=t['id'], job_id=t['job_id'], payload_class=t['payload_class'],
            payload_profile_id=t['payload_profile_id'], agl_m=t['agl_m'],
            transects=[Transect(coords=[tuple(p) for p in tr['coords']],
                               length_m=tr['length_m'], job_id=tr['job_id']) for tr in t['transects']],
            survey_length_m=t['survey_length_m'], turn_count=t['turn_count'],
            sweep_angle_deg=t['sweep_angle_deg'], entry=tuple(t['entry']), exit=tuple(t['exit']),
            geom=LineString(t['geom_coords']), internal_transition_m=t['internal_transition_m'],
            fixed_wing_safe=t['fixed_wing_safe'], notes=deepcopy(t.get('notes', [])),
            route_variants=deepcopy(t.get('route_variants', [])))
    fleet = {u['id']: u for u in bundle['fleet']}
    sorties = []
    for s in plan['sorties']:
        u = fleet[s['uav_id']]
        usable = u['operational_endurance_min']*60*(1-u['energy_reserve_fraction'])
        points = []
        for i, p in enumerate(s['waypoints']):
            speed = 0.0
            if i:
                previous = s['waypoints'][i-1]
                dt = p['t_s']-previous['t_s']
                if dt > 0:
                    speed = math.hypot(p['x']-previous['x'],p['y']-previous['y'])/dt
            points.append(Waypoint(x=p['x'],y=p['y'],agl_m=p['agl_m'],amsl_m=p.get('z_m',p['agl_m']),
                t=origin+timedelta(seconds=p['t_s']),phase=p['phase'],task_id=p.get('task_id'),
                speed_ms=speed,remaining_endurance_s=usable-(p['t_s']-s['start_s'])))
        sorties.append(Sortie(uav_id=s['uav_id'],index=s['index'],start_site_id=s['start_site_id'],
            landing_site_id=s['landing_site_id'],task_ids=list(s['task_ids']),
            task_reversed=list(s['task_reversed']), task_variants=list(s.get('task_variants', [])),t_start=origin+timedelta(seconds=s['start_s']),
            t_end=origin+timedelta(seconds=s['end_s']),flight_time_s=s['flight_time_s'],
            distance_m=s['distance_m'],survey_length_m=s['survey_length_m'],
            transit_length_m=s['transit_length_m'],waypoints=points,transit_agl_m=u['cruise_agl_m'],
            energy=dict(usable_endurance_s=usable,resource_margin_s=s['resource_margin_s'])))
    metrics = deepcopy(plan['metrics'])
    metrics.update(makespan_min=metrics['makespan_s']/60,total_flight_min=metrics['total_flight_s']/60,
                   total_distance_km=metrics['total_distance_m']/1000,
                   used_uav_count=len({s.uav_id for s in sorties}),
                   h2_all_tasks_assigned=not plan['unassigned'])
    return Plan(scene_id=bundle['scene_id'],objective=plan['objective'],sorties=sorties,tasks=tasks,
        metrics=metrics,status='UNKNOWN',validation=None,certificate=None,
        diagnosis=dict(h2_status=plan['status'],h2_checks=deepcopy(plan['checks']),
                       unassigned=deepcopy(plan['unassigned']),assumptions=deepcopy(plan.get('assumptions',[])),
                       requires_h3_validation=True),
        solver_log=deepcopy(plan['solver_log']),deconfliction=deepcopy(plan['deconfliction']),created_at=origin)
