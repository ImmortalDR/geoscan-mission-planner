"""Independent consistency checks for H2 output; not an H3 certificate."""
from collections import Counter, defaultdict
import math
from shapely.geometry import LineString, Point, shape

from .task_routes import route_variants
from .contract import mission_clock, fingerprint
from .deconfliction import detect_conflicts
from .transit import TransitContext, NoPath


def _length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def _corners(points):
    out = []
    for p in points:
        p = tuple(p)
        if out and math.dist(out[-1], p) < 1e-7:
            continue
        while len(out) > 1:
            a, b = out[-2:]
            cross = (b[0]-a[0])*(p[1]-b[1])-(b[1]-a[1])*(p[0]-b[0])
            dot = (b[0]-a[0])*(p[0]-b[0])+(b[1]-a[1])*(p[1]-b[1])
            if abs(cross) > 1e-6*max(1, math.dist(a, b), math.dist(b, p)) or dot < 0:
                break
            out.pop()
        out.append(p)
    return out


def check_plan(bundle, plan, settings=None, scene=None):
    """Check assignments, immutable survey paths, schedules and resource bounds.

    Resource is independently recomputed from trajectory duration under the
    declared time-linear model. This is not a manufacturer battery model.
    Geographic reserve checks sample paths every 25 m and at all waypoints.
    """
    violations = []

    def fail(code, **details):
        violations.append(dict(code=code, **details))

    def option(key, default):
        return settings.get(key, default) if isinstance(settings, dict) else getattr(settings, key, default)

    def close(actual, expected):
        return isinstance(actual, (int, float)) and math.isfinite(actual) and abs(actual-expected) <= max(1e-5, abs(expected)*1e-7)

    scope = dict(geometry="immutable survey and supplied static geographic context",
                 resource="independent time-linear endurance with reserve",
                 geographic_coverage="supplied scene only" if scene else "not provided; flat unobstructed assumption",
                 emergency_reachability="25 m sampled static paths", certificate=False)
    try:
        tasks = {t["id"]: t for t in bundle["tasks"]}
        fleet = {u["id"]: u for u in bundle["fleet"]}
        sites = {s["id"]: s for s in bundle["sites"]}
        context = TransitContext(scene, bundle["crs"]["metric_epsg"])
        unrestricted = context.allowed is None and context.forbidden.is_empty
        _, horizon = mission_clock(bundle)
        if "input_sha256" in plan and plan["input_sha256"] != fingerprint(bundle):
            fail("input_fingerprint_mismatch")
        if "crs" in plan and plan["crs"] != bundle["crs"]:
            fail("crs_mismatch")
        if "tasks" in plan and plan["tasks"] != bundle["tasks"]:
            fail("task_geometry_changed")
        assigned, omitted, by_uav = Counter(), Counter(), defaultdict(list)
        for item in plan.get("unassigned", []):
            tid = item["task_id"]
            omitted[tid] += 1
            fail("unassigned_task", task_id=tid, reason=item.get("reason"))
        sortie_ids = Counter(s["id"] for s in plan["sorties"])
        if any(n > 1 for n in sortie_ids.values()):
            fail("duplicate_sortie_id")
        total, finish, distance_total = 0., 0., 0.
        valid_for_conflicts = []
        for s in plan["sorties"]:
            sid, uid = s["id"], s["uav_id"]
            assigned.update(s["task_ids"])
            if uid not in fleet:
                fail("unknown_uav", sortie_id=sid)
                continue
            u = fleet[uid]
            by_uav[uid].append(s)
            points = s["waypoints"]
            if len(points) < 2 or not all(all(isinstance(p.get(k), (int, float)) and math.isfinite(p[k]) for k in ("x", "y", "agl_m", "t_s")) for p in points):
                fail("invalid_waypoints", sortie_id=sid)
                continue
            start, end = points[0]["t_s"], points[-1]["t_s"]
            if abs(points[0]["agl_m"]) > 1e-5 or abs(points[-1]["agl_m"]) > 1e-5:
                fail("site_ground_altitude", sortie_id=sid)
            elapsed = end-start
            total += elapsed
            finish = max(finish, end)
            coords = [(p["x"], p["y"]) for p in points]
            for p in points:
                expected_z = p["agl_m"]+context.elevation(p["x"], p["y"])
                if (context.has_terrain or "z_m" in p) and not close(p.get("z_m"), expected_z):
                    fail("altitude_reference_mismatch", sortie_id=sid)
                    break
            length = _length(coords)
            distance_total += length
            if not close(s.get("start_s"), start) or not close(s.get("end_s"), end):
                fail("declared_time_mismatch", sortie_id=sid)
            if not close(s.get("flight_time_s"), elapsed):
                fail("declared_flight_time_mismatch", sortie_id=sid)
            if not close(s.get("distance_m"), length):
                fail("declared_distance_mismatch", sortie_id=sid)
            operating = (scene or {}).get("operating_window", {})
            if start < operating.get("start_s", 0.) - 1e-6 or end > operating.get("end_s", math.inf) + 1e-6:
                fail("operating_window", sortie_id=sid)
            if start < 0 or (horizon is not None and end > horizon+1e-6):
                fail("mission_window", sortie_id=sid)
            usable = u["operational_endurance_min"]*60*(1-u["energy_reserve_fraction"])
            if elapsed > usable+1e-6 or elapsed < 0:
                fail("resource_exceeded", sortie_id=sid, actual_s=elapsed, usable_s=usable)
            if "resource_margin_s" in s and not close(s["resource_margin_s"], usable-elapsed):
                fail("declared_resource_margin_mismatch", sortie_id=sid)
            for key, index, roles, pin in (("start_site_id", 0, {"both", "start"}, "start_site"), ("landing_site_id", -1, {"both", "landing"}, "landing_site")):
                site = sites.get(s.get(key))
                if site is None or site.get("candidate", False) or site["role"] not in roles:
                    fail("invalid_site", sortie_id=sid, field=key)
                elif math.dist(coords[index], (site["x"], site["y"])) > 1e-5:
                    fail("site_position_mismatch", sortie_id=sid, field=key)
                if "refuel_sites" not in u and pin == "landing_site" and u.get(pin) is not None and s.get(key) != u[pin]:
                    fail("pinned_site_mismatch", sortie_id=sid, field=key)
            if "refuel_sites" not in u and not bundle["mission"]["allow_different_start_end"] and s["start_site_id"] != s["landing_site_id"]:
                fail("different_sites_forbidden", sortie_id=sid)
            valid_times = True
            occupancy = Counter()
            for a, b in zip(points, points[1:]):
                dt, d = b["t_s"]-a["t_s"], math.hypot(b["x"]-a["x"], b["y"]-a["y"])
                if a.get("phase") == b.get("phase"):
                    occupancy[a.get("phase")] += max(0, dt)
                if dt < 0 or (dt == 0 and (d > 1e-6 or abs(a["agl_m"]-b["agl_m"]) > 1e-6)):
                    fail("nonmonotonic_time", sortie_id=sid)
                    valid_times = False
                if d > u["ground_speed_ms"]*max(dt, 0)+1e-5:
                    fail("excessive_speed", sortie_id=sid)
                za = a.get("z_m", a["agl_m"])
                zb = b.get("z_m", b["agl_m"])
                if not all(math.isfinite(z) for z in (za, zb)) or abs(zb-za) > option("vertical_speed_ms", 3.)*max(dt, 0)+1e-5:
                    fail("excessive_vertical_speed", sortie_id=sid)
                segment_coords = [(a["x"], a["y"]), (b["x"], b["y"])]
                segment = Point(segment_coords[0]) if segment_coords[0] == segment_coords[1] else LineString(segment_coords)
                for zone in (scene or {}).get("temporal", []):
                    if a["t_s"] <= zone["end_s"] and b["t_s"] >= zone["start_s"] and segment.intersects(shape(zone["geometry"])):
                        fail("temporal_restriction", sortie_id=sid, start_s=a["t_s"], end_s=b["t_s"])
            if occupancy["takeoff"] < u["takeoff_time_s"]-1e-5 or occupancy["landing"] < u["landing_time_s"]-1e-5:
                fail("takeoff_landing_duration", sortie_id=sid)
            if any(p.get("task_id") is not None and p["task_id"] not in s["task_ids"] for p in points):
                fail("unknown_waypoint_task", sortie_id=sid)
            if valid_times:
                valid_for_conflicts.append(s)
            if not unrestricted and not context.geometry_clear(coords):
                fail("geographic_intersection", sortie_id=sid)
            timings = s.get("task_times", [])
            if Counter(t["task_id"] for t in timings) != Counter(s["task_ids"]):
                fail("task_timing_ids", sortie_id=sid)
            if [t["task_id"] for t in sorted(timings, key=lambda t: t["start_s"])] != s["task_ids"] or any(a["end_s"] > b["start_s"]+1e-6 for a, b in zip(timings, timings[1:])):
                fail("task_order", sortie_id=sid)
            reversals = s.get("task_reversed", [False]*len(s["task_ids"]))
            variant_ids = s.get("task_variants", ["primary"]*len(s["task_ids"]))
            if len(variant_ids) != len(s["task_ids"]) or len(reversals) != len(s["task_ids"]):
                fail("task_variant_count", sortie_id=sid)
            for i, tid in enumerate(s["task_ids"]):
                if tid not in tasks:
                    continue
                if uid not in bundle["feasibility"]["eligible_uav_ids_by_task"][tid]:
                    fail("ineligible_assignment", task_id=tid, sortie_id=sid)
                task = tasks[tid]
                variant = next((v for v in route_variants(task)
                                if i < len(variant_ids) and v["id"] == variant_ids[i]), None)
                if variant is None:
                    fail("unknown_task_variant", task_id=tid, sortie_id=sid)
                    continue
                if u["uav_class"] == "fixed_wing" and not variant["fixed_wing_safe"]:
                    fail("unsafe_task_variant", task_id=tid, sortie_id=sid)
                expected = list(variant["geom_coords"])
                if i < len(reversals) and reversals[i]:
                    expected.reverse()
                actual = [p for p in points if p.get("task_id") == tid and p.get("phase") == "survey"]
                canonical_expected = _corners(expected)
                canonical_actual = _corners([(p["x"], p["y"]) for p in actual])
                if len(canonical_actual) != len(canonical_expected) or any(math.dist(a, b) > 1e-5 for a, b in zip(canonical_actual, canonical_expected)) or any(abs(p["agl_m"]-task["agl_m"]) > 1e-5 for p in actual):
                    fail("survey_geometry_mismatch", task_id=tid, sortie_id=sid)
                timing = next((t for t in timings if t["task_id"] == tid), None)
                if timing and actual:
                    a, b = timing["start_s"], timing["end_s"]
                    if not close(a, actual[0]["t_s"]) or not close(b, actual[-1]["t_s"]) or not start <= a <= b <= end:
                        fail("task_timing_mismatch", task_id=tid)
                    turn = math.pi*max(50., u["turnaround_buffer_m"])/u["ground_speed_ms"] if u["uav_class"] == "fixed_wing" else option("multirotor_turn_s", 6.)
                    minimum = max((task["survey_length_m"]+variant["internal_transition_m"])/(u["ground_speed_ms"]*option("survey_speed_factor", .75))+task["turn_count"]*turn, option("task_service_s", {}).get(tid, 0))
                    if b-a < minimum-1e-5:
                        fail("task_duration_short", task_id=tid)
                    window = option("task_windows", {}).get(tid)
                    if window and not window[0]-1e-6 <= a <= window[1]+1e-6:
                        fail("task_window", task_id=tid)
            # Explicit reserve-site reachability, including intermediate locations.
            landing_sites = [site for site in sites.values() if not site.get("candidate", False) and site["role"] in {"both", "landing", "reserve"}]
            buffer = u["turnaround_buffer_m"] if u["uav_class"] == "fixed_wing" else 0.
            unreachable = False
            for a, b in zip(points, points[1:]):
                n = max(1, math.ceil(math.hypot(b["x"]-a["x"], b["y"]-a["y"])/25))
                for k in range(n+1):
                    f = k/n
                    xy = tuple(a[key]+f*(b[key]-a[key]) for key in ("x", "y"))
                    now = a["t_s"]+f*(b["t_s"]-a["t_s"])
                    remaining = usable-(now-start)+1e-5
                    landing = 0 if a.get("phase") == b.get("phase") == "landing" else u["landing_time_s"]
                    reachable = False
                    for site in landing_sites:
                        try:
                            destination = (site["x"], site["y"])
                            straight = math.dist(xy, destination)
                            if straight/u["ground_speed_ms"]+landing > remaining:
                                continue
                            return_distance = straight if unrestricted else _length(context.path(xy, destination, buffer))
                            if return_distance/u["ground_speed_ms"]+landing <= remaining:
                                reachable = True
                                break
                        except NoPath:
                            pass
                    if not reachable:
                        unreachable = True
                        break
                if unreachable:
                    break
            if unreachable:
                fail("reserve_site_unreachable", sortie_id=sid)
        for tid in set(tasks) | set(assigned) | set(omitted):
            if tid not in tasks:
                fail("unknown_task", task_id=tid)
            elif assigned[tid]+omitted[tid] != 1:
                fail("task_accounting", task_id=tid, assigned=assigned[tid], unassigned=omitted[tid])
        for uid, sorties in by_uav.items():
            sorties.sort(key=lambda s: s["waypoints"][0]["t_s"])
            if sorties and fleet[uid].get("start_site") is not None and sorties[0]["start_site_id"] != fleet[uid]["start_site"]:
                fail("pinned_site_mismatch", sortie_id=sorties[0]["id"], field="start_site_id")
            if 'refuel_sites' in fleet[uid] and sorties:
                u=fleet[uid]
                if u['landing_site'] is not None and sorties[-1]['landing_site_id']!=u['landing_site']:
                    fail('pinned_final_site_mismatch',uav_id=uid)
                for previous in sorties[:-1]:
                    sid=previous['landing_site_id'];site=sites.get(sid,{})
                    if site.get('role')!='both' or site.get('candidate',False) or (u['refuel_sites'] is not None and sid not in u['refuel_sites']):
                        fail('refuel_site_forbidden',uav_id=uid,site_id=sid)
            for a, b in zip(sorties, sorties[1:]):
                if b["waypoints"][0]["t_s"] < a["waypoints"][-1]["t_s"]+fleet[uid]["service_time_s"]-1e-6:
                    fail("service_gap", uav_id=uid)
                if a["landing_site_id"] != b["start_site_id"]:
                    fail("site_continuity", uav_id=uid)
        for conflict in detect_conflicts(valid_for_conflicts, bundle["fleet"]):
            fail("conflict", **conflict)
        expected_metrics = dict(makespan_s=finish, total_flight_s=total, total_distance_m=distance_total,
                                sortie_count=len(plan["sorties"]), used_uav_count=len(by_uav),
                                sorties=len(plan["sorties"]), unassigned=len(plan.get("unassigned", [])))
        for key, expected in expected_metrics.items():
            if key in plan.get("metrics", {}) and not close(plan["metrics"][key], expected):
                fail("metric_mismatch", metric=key, expected=expected)
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError) as exc:
        fail("malformed_plan", message=str(exc))
    return dict(passed=not violations, violations=violations, scope=scope)
