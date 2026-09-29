"""Strict consumer of the frozen H1 wire contract; no H1 geometry generation."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

VERSION = "gmp.h1_h2.v2"
SUPPORTED_VERSIONS = {"gmp.h1_h2.v1", VERSION, "gmp.h1_h2.v3"}
OBJECTIVES = {"makespan", "total_flight"}
PAYLOADS = {"rgb", "multispectral", "thermal", "lidar", "geophysics", "rgb_video"}


class ContractError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise ContractError(message)


def number(value, name, minimum=None, positive=False):
    require(type(value) in (int, float) and math.isfinite(value), f"{name}: finite number required")
    require(minimum is None or value >= minimum, f"{name}: below minimum {minimum}")
    require(not positive or value > 0, f"{name}: must be positive")


def fields(obj, names, name):
    require(isinstance(obj, dict), f"{name}: object required")
    for key in names.split():
        require(key in obj, f"{name}: missing {key}")


def text(value, name):
    require(isinstance(value, str) and bool(value.strip()), f"{name}: nonempty string required")


def point(p, name):
    require(isinstance(p, (list, tuple)) and len(p) == 2, f"{name}: [x,y] required")
    for v in p:
        number(v, name)


def timestamp(s):
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
        require(t.tzinfo is not None, "timestamp must include timezone")
        return t
    except (ValueError, TypeError, AttributeError) as e:
        raise ContractError(f"invalid ISO-8601 timestamp: {s!r}") from e


def poly_length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_bundle(d):
    fields(d, "schema_version scene_id crs generated_at source fleet sites mission tasks feasibility warnings", "bundle")
    require(d["schema_version"] in SUPPORTED_VERSIONS, f"unsupported schema_version: {d['schema_version']!r}")
    text(d["scene_id"], "scene_id")
    timestamp(d["generated_at"])
    require(d["source"] in {"fixture", "coverage", "synthetic"}, "invalid source")
    fields(d["crs"], "metric_epsg axis", "crs")
    epsg = str(d["crs"]["metric_epsg"]).removeprefix("EPSG:")
    require(epsg.isdigit() and epsg != "4326" and d["crs"]["axis"] == "ENU_metres", "metric ENU CRS required")
    # Verify metres rather than silently interpreting feet or geographic degrees.
    from pyproj import CRS
    try:
        crs = CRS.from_epsg(int(epsg))
        require(crs.is_projected and all(abs(a.unit_conversion_factor - 1) < 1e-12 for a in crs.axis_info[:2]), "CRS must be projected metres")
    except ContractError:
        raise
    except Exception as e:
        raise ContractError("invalid metric_epsg") from e
    ids = {}
    for name in ("fleet", "sites", "tasks"):
        require(isinstance(d[name], list), f"{name}: array required")
        ids[name] = set()
        for row in d[name]:
            fields(row, "id", name)
            text(row["id"], name + ".id")
            require(row["id"] not in ids[name], f"duplicate {name} id {row['id']}")
            ids[name].add(row["id"])
    require(isinstance(d["warnings"], list) and all(isinstance(w, str) for w in d["warnings"]), "warnings: string array required")
    for s in d["sites"]:
        fields(s, "x y role", "site")
        point([s["x"], s["y"]], "site")
        require(s["role"] in {"both", "start", "landing", "reserve"}, "invalid site role")
        require(type(s.get("candidate", False)) is bool, "candidate: bool required")
    require(any(s["role"] in {"both", "start"} for s in d["sites"]), "no start-capable site")
    require(any(s["role"] in {"both", "landing", "reserve"} for s in d["sites"]), "no landing-capable site")
    for u in d["fleet"]:
        fields(u, "model uav_class ground_speed_ms operational_endurance_min max_wind_ms payload_classes energy_reserve_fraction horizontal_separation_m vertical_separation_m turnaround_buffer_m start_site landing_site takeoff_time_s landing_time_s service_time_s cruise_agl_m", "uav")
        text(u["model"], "model")
        require(u["uav_class"] in {"fixed_wing", "multirotor", "vtol"}, "invalid uav_class")
        for k in ("ground_speed_ms", "operational_endurance_min", "horizontal_separation_m", "vertical_separation_m"):
            number(u[k], k, positive=True)
        for k in ("max_wind_ms", "energy_reserve_fraction", "turnaround_buffer_m", "takeoff_time_s", "landing_time_s", "service_time_s"):
            number(u[k], k, minimum=0)
        require(u["energy_reserve_fraction"] <= .5, "reserve exceeds 0.5")
        number(u["cruise_agl_m"], "cruise_agl_m", minimum=40)
        require(isinstance(u["payload_classes"], list) and len(u["payload_classes"]) > 0, "empty payload_classes")
        require(all(isinstance(p, str) for p in u["payload_classes"]), "payload_classes must contain strings")
        for k in ("start_site", "landing_site"):
            require(u[k] is None or u[k] in ids["sites"], f"unknown {k}")
        if 'refuel_sites' in u:
            value = u['refuel_sites']
            require(value is None or (isinstance(value, list) and all(isinstance(sid,str) for sid in value) and len(value)==len(set(value))
                    and all(sid in ids['sites'] for sid in value)), 'invalid refuel_sites')
            if value is not None:
                require(all(next(s for s in d['sites'] if s['id']==sid)['role']=='both'
                            and not next(s for s in d['sites'] if s['id']==sid).get('candidate',False)
                            for sid in value), 'refuel site must be active and support landing/relaunch')
    m = d["mission"]
    fields(m, "objectives window_start window_end wind allow_different_start_end", "mission")
    require(isinstance(m["objectives"], list) and bool(m["objectives"]) and all(o in OBJECTIVES for o in m["objectives"]), "invalid objectives")
    for k in ("allow_different_start_end", "require_complete_coverage", "require_schedule"):
        require(type(m.get(k, True)) is bool, f"{k}: bool required")
    for k in ("window_start", "window_end"):
        if m[k] is not None:
            timestamp(m[k])
    if m["window_start"] and m["window_end"]:
        require(timestamp(m["window_start"]) < timestamp(m["window_end"]), "empty mission window")
    fields(m["wind"], "speed_ms direction_deg_from", "wind")
    number(m["wind"]["speed_ms"], "wind.speed_ms", minimum=0)
    number(m["wind"]["direction_deg_from"], "wind.direction_deg_from")
    fields(d["feasibility"], "eligible_uav_ids_by_task ineligible_reasons", "feasibility")
    eligible = d["feasibility"]["eligible_uav_ids_by_task"]
    require(isinstance(eligible, dict), "eligible map required")
    require(set(eligible) == ids["tasks"], "feasibility task ids do not match tasks")
    fleet = {u["id"]: u for u in d["fleet"]}
    for t in d["tasks"]:
        fields(t, "job_id payload_class payload_profile_id agl_m transects survey_length_m turn_count sweep_angle_deg entry exit geom_coords internal_transition_m fixed_wing_safe", "task")
        for k in ("job_id", "payload_profile_id"):
            text(t[k], k)
        require(t["payload_class"] in PAYLOADS, "invalid payload_class")
        number(t["agl_m"], "agl_m", minimum=40)
        number(t["survey_length_m"], "survey_length_m", positive=True)
        number(t["internal_transition_m"], "internal_transition_m", minimum=0)
        number(t["sweep_angle_deg"], "sweep_angle_deg")
        require(type(t["turn_count"]) is int and t["turn_count"] >= 0, "turn_count: nonnegative integer required")
        require(type(t["fixed_wing_safe"]) is bool, "fixed_wing_safe: bool required")
        require(isinstance(t["transects"], list) and len(t["transects"]) > 0, "empty transects")
        for tr in t["transects"]:
            fields(tr, "coords length_m job_id", "transect")
            require(tr["job_id"] == t["job_id"], "transect job mismatch")
            require(isinstance(tr["coords"], list) and len(tr["coords"]) >= 2, "short transect")
            for p in tr["coords"]:
                point(p, "transect.coords")
            number(tr["length_m"], "length_m", positive=True)
            require(abs(poly_length(tr["coords"]) - tr["length_m"]) <= 1e-3, "transect length mismatch")
        require(abs(sum(tr["length_m"] for tr in t["transects"]) - t["survey_length_m"]) <= 1e-3, "survey length mismatch")
        require(t["entry"] == t["transects"][0]["coords"][0] and t["exit"] == t["transects"][-1]["coords"][-1], "task entry/exit mismatch")
        require(isinstance(t["geom_coords"], list) and len(t["geom_coords"]) >= 2, "empty geom_coords")
        for p in t["geom_coords"]:
            point(p, "geom_coords")
        require(t["geom_coords"][0] == t["entry"] and t["geom_coords"][-1] == t["exit"], "geom endpoints mismatch")
        require("uav_id" not in t and "start_time" not in t, "H1 task contains assignment/time")
        if d["schema_version"] in (VERSION, "gmp.h1_h2.v3"):
            validate_route_variants(t)
        else:
            require("route_variants" not in t, "route_variants requires v2")
        e = eligible[t["id"]]
        require(isinstance(e, list) and all(uid in fleet for uid in e), "unknown eligible UAV")
        require(t["fixed_wing_safe"] or all(fleet[uid]["uav_class"] != "fixed_wing" for uid in e), "G5 unsafe fixed-wing eligibility")
    require(not (d["source"] == "coverage" and m.get("require_complete_coverage", True) and not d["tasks"]), "empty complete coverage")
    if d['schema_version'] == 'gmp.h1_h2.v3':
        require(d.get('atomicity') == 'whole_transect', 'v3 atomicity')
        parents = {t['id']:t for t in d.get('parent_tasks', [])}
        expected = {(pid,i) for pid,t in parents.items() for i in range(len(t['transects']))}
        actual = set()
        for t in d['tasks']:
            key = (t.get('parent_task_id'), t.get('transect_index'))
            require(key in expected and key not in actual, 'v3 transect provenance')
            actual.add(key)
            parent = parents[key[0]]
            require(t.get('transect_id')==t['id'] and len(t['transects'])==1 and
                    t['transects'][0]==parent['transects'][key[1]], 'v3 immutable transect')
            require(all(t[k]==parent[k] for k in ('job_id','payload_class','payload_profile_id','agl_m')),
                    'v3 survey requirement changed')
        require(actual==expected, 'v3 missing transects')
    return d


def load_bundle(source):
    if isinstance(source, (str, Path)):
        with open(source, encoding="utf-8") as f:
            d = json.load(f)
    else:
        d = deepcopy(source)
    return validate_bundle(d)


def mission_clock(bundle):
    m = bundle["mission"]
    # Explicit origin, even with unbounded mission. No local clock-dependent plans.
    origin = timestamp(m["window_start"] or bundle["generated_at"])
    horizon = (timestamp(m["window_end"]) - origin).total_seconds() if m["window_end"] else None
    return origin, horizon


def validate_route_variants(t):
    variants = t.get("route_variants")
    count = 2 if len(t["transects"]) > 1 else 1
    require(isinstance(variants, list) and len(variants) == count, "route_variants count")
    for index, variant in enumerate(variants):
        fields(variant, "id geom_coords internal_transition_m fixed_wing_safe", "route_variant")
        require(variant["id"] == ("primary" if index == 0 else "alternate"), "route_variant id")
        require(type(variant["fixed_wing_safe"]) is bool, "variant fixed_wing_safe")
        lines = [list(reversed(tr["coords"])) if index else tr["coords"] for tr in t["transects"]]
        expected = []
        for line in lines:
            expected.extend(line[1:] if expected and expected[-1] == line[0] else line)
        require(variant["geom_coords"] == expected, "variant changes survey geometry or connections")
        internal = sum(math.dist(a[-1], b[0]) for a, b in zip(lines, lines[1:]))
        number(variant["internal_transition_m"], "variant transition", minimum=0)
        require(abs(variant["internal_transition_m"] - internal) < 1e-3, "variant transition mismatch")
        if index == 0:
            require(variant["fixed_wing_safe"] == t["fixed_wing_safe"], "primary safety mismatch")
            require(variant["geom_coords"] == t["geom_coords"], "primary geometry mismatch")
            require(abs(internal - t["internal_transition_m"]) < 1e-3, "primary transition mismatch")
