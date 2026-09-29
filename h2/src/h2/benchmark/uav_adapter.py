"""Deterministic synthetic UAV adaptation, never a standard VRPTW comparison.

Coordinates: UTM32N offset (500000,6000000), 10 m per source unit.
Service: original service seconds + 0.1 seconds per demand unit; demand is
synthetic workload, NOT battery or a conversion of vehicle capacity.
Windows: [5*ready, 5*due+1800] seconds, deliberately widened for UAV operations.
Numeric fleet seeds come from the supplied H1 S11 fixture. Site assignments and
payload selection are synthetic; geoscan_401_geo is normalized to geoscan_401.
Neither numeric seeds nor adapted tasks are manufacturer flight guarantees.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import random

from h2.contract import validate_bundle
from ._profile_seeds import PROFILES
from .vrptw import Instance


def adapt(instance: Instance, fleet_size: int = 3, customer_limit: int | None = None,
          seed: int = 42) -> tuple[dict, dict]:
    if not 2 <= fleet_size <= 10:
        raise ValueError('fleet_size must be 2..10')
    if customer_limit is not None and customer_limit <= 0:
        raise ValueError('customer_limit must be positive')
    customers = instance.customers[1:][:customer_limit]
    if not customers:
        raise ValueError('adaptation needs customers')
    rng = random.Random(seed)
    source_profiles = {u['model']: u for u in PROFILES}
    order = ['geoscan_gemini', 'geoscan_701', 'geoscan_201', 'geoscan_401_geo', 'geoscan_801']

    def point(c):
        return [500000.0+10*c.x, 6000000.0+10*c.y]

    # Deterministic west/east halves yield two spatial cluster-centroid sites.
    ordered = sorted(customers, key=lambda c: (c.x, c.y, c.id))
    cut = max(1, len(ordered)//2)
    groups = [ordered[:cut], ordered[cut:] or ordered[:cut]]
    sites = [dict(id=f'site-{i}', x=sum(point(c)[0] for c in group)/len(group)+30*i,
                  y=sum(point(c)[1] for c in group)/len(group)-100,
                  role='both', candidate=False) for i, group in enumerate(groups)]
    fleet = []
    for i in range(fleet_size):
        u = deepcopy(source_profiles[order[i % len(order)]])
        if u['model'] == 'geoscan_401_geo':
            u['model'] = 'geoscan_401'
        u.update(id=f'uav-{i}', start_site=sites[i % 2]['id'], landing_site=sites[i % 2]['id'])
        fleet.append(u)
    payloads = sorted({p for u in fleet for p in u['payload_classes']})
    tasks, eligible, reasons, windows, service = [], {}, {}, {}, {}
    for index, c in enumerate(customers):
        # Cycle supported payloads to exercise exclusions, with seed controlling phase.
        payload = payloads[(index + seed) % len(payloads)]
        xy = point(c)
        length = 10.0 + rng.randrange(3)
        endpoint = [xy[0]+length, xy[1]]
        tid = f'customer-{c.id}'
        job = f'vrptw-job-{c.id}'
        tasks.append(dict(id=tid, job_id=job, payload_class=payload,
                          payload_profile_id=f'synthetic_{payload}', agl_m=120.0,
                          transects=[dict(coords=[xy, endpoint], length_m=length, job_id=job)],
                          survey_length_m=length, turn_count=0, sweep_angle_deg=0.0,
                          entry=xy, exit=endpoint, geom_coords=[xy, endpoint],
                          internal_transition_m=0.0, fixed_wing_safe=True))
        eligible[tid] = [u['id'] for u in fleet if payload in u['payload_classes']]
        reasons.update({f"{tid}:{u['id']}": 'payload_class_mismatch' for u in fleet
                        if u['id'] not in eligible[tid]})
        windows[tid] = [5*c.ready, 5*c.due+1800]
        service[tid] = c.service + .1*c.demand
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    horizon = max(5*instance.customers[0].due+4000, max(w[1] for w in windows.values())+2000)
    provenance = dict(profile='vrptw_uav_adapted', instance=instance.name, seed=seed,
        coordinate_scale_m=10, coordinate_offset_m=[500000,6000000], time_window_scale=5,
        time_window_slack_s=1800, service_demand_factor_s=.1,
        fleet_numeric_source='supplied H1 S11_payload_compatibility.bundle.json',
        model_alias={'geoscan_401_geo': 'geoscan_401'},
        manufacturer_verified=False, bks_comparable=False,
        source_vehicle_capacity_used=False)
    bundle = dict(schema_version='gmp.h1_h2.v1', scene_id=f'vrptw-{instance.name}-{seed}',
        crs=dict(metric_epsg=32632, axis='ENU_metres'), generated_at=origin.isoformat(), source='synthetic',
        fleet=fleet, sites=sites,
        mission=dict(objectives=['makespan','total_flight'], window_start=origin.isoformat(),
                     window_end=(origin+timedelta(seconds=horizon)).isoformat(),
                     wind=dict(speed_ms=0, direction_deg_from=0), allow_different_start_end=True,
                     require_complete_coverage=True, require_schedule=True),
        tasks=tasks, feasibility=dict(eligible_uav_ids_by_task=eligible,ineligible_reasons=reasons),
        warnings=['Synthetic VRPTW-derived UAV scenario; not manufacturer performance or standard BKS.'],
        extensions={'h2_benchmark': provenance})
    validate_bundle(bundle)
    return bundle, dict(task_windows=windows, task_service_s=service)
