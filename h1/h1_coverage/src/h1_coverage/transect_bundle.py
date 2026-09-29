"""Versioned H1 export: indivisible survey transects, with parent provenance."""
from copy import deepcopy
import math

VERSION = 'gmp.h1_h2.v3'


def export_transect_bundle(source):
    """Retain every coordinate and reassess only known group-level restrictions.

    Explicit/custom exclusions remain exclusions. H2 still decides aircraft and
    times; this export supplies only sensor/wind/geometry eligibility.
    """
    if source['schema_version'] == VERSION:
        return deepcopy(source)
    if source['schema_version'] not in ('gmp.h1_h2.v1', 'gmp.h1_h2.v2'):
        raise ValueError('Unsupported H1 source version')
    out = deepcopy(source)
    out['schema_version'] = VERSION
    out['parent_schema_version'] = source['schema_version']
    out['parent_tasks'] = deepcopy(source['tasks'])
    out['tasks'] = []
    eligible, reasons = {}, {}
    old = source['feasibility']
    for parent in source['tasks']:
        for index, tr in enumerate(parent['transects']):
            t = deepcopy(parent)
            tid = f"{parent['id']}#G{index:04d}"
            coords = deepcopy(tr['coords'])
            vectors = [(b[0]-a[0], b[1]-a[1]) for a,b in zip(coords,coords[1:])]
            turns = sum(abs(a[0]*b[1]-a[1]*b[0]) > 1e-7 or a[0]*b[0]+a[1]*b[1] < 0
                        for a,b in zip(vectors,vectors[1:]))
            safe = bool(parent['fixed_wing_safe'] or turns == 0)
            t.update(id=tid, transect_id=tid, parent_task_id=parent['id'], transect_index=index,
                     transects=[deepcopy(tr)], survey_length_m=tr['length_m'], turn_count=turns,
                     geom_coords=coords, entry=coords[0], exit=coords[-1], internal_transition_m=0.,
                     fixed_wing_safe=safe,
                     route_variants=[dict(id='primary', geom_coords=deepcopy(coords),
                                          internal_transition_m=0., fixed_wing_safe=safe)])
            eligible[tid] = []
            for u in source['fleet']:
                uid = u['id']
                reason = old.get('ineligible_reasons', {}).get(f"{parent['id']}:{uid}")
                if uid not in old['eligible_uav_ids_by_task'][parent['id']]:
                    if reason not in ('task_exceeds_usable_endurance', 'fixed_wing_turn_unsafe_near_nfz'):
                        reasons[f'{tid}:{uid}'] = reason or 'inherited_explicit_exclusion'
                        continue
                if t['payload_class'] not in u['payload_classes']:
                    reason = 'payload_class_mismatch'
                elif source['mission']['wind']['speed_ms'] > u['max_wind_ms']:
                    reason = 'wind_limit'
                elif u['uav_class'] == 'fixed_wing' and not safe:
                    reason = 'fixed_wing_turn_unsafe_near_nfz'
                elif tr['length_m']/u['ground_speed_ms'] > u['operational_endurance_min']*60*(1-u['energy_reserve_fraction']):
                    reason = 'task_exceeds_usable_endurance'
                else:
                    reason = None
                if reason:
                    reasons[f'{tid}:{uid}'] = reason
                else:
                    eligible[tid].append(uid)
            out['tasks'].append(t)
    out['feasibility'] = dict(eligible_uav_ids_by_task=eligible, ineligible_reasons=reasons)
    out['atomicity'] = 'whole_transect'
    return out
