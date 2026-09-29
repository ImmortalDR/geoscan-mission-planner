"""Anytime assignment/routing: feasible incumbent first, then CP-SAT and LNS."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import math
import random
import time

from .algorithms.assignment import _cp_assignment
from .algorithms.temporal import _temporal_schedule
from .contract import load_bundle, fingerprint
from .scheduler import Settings, Scheduler, RouteFailure
from .deconfliction import detect_conflicts, resolve_conflicts


def metrics(sorties):
    return dict(makespan_s=max((s["end_s"] for s in sorties), default=0.),
                total_flight_s=sum(s["flight_time_s"] for s in sorties),
                total_distance_m=sum(s["distance_m"] for s in sorties),
                sortie_count=len(sorties), used_uav_count=len({s["uav_id"] for s in sorties}))


def objective_value(sorties, objective):
    m = metrics(sorties)
    return m[objective+"_s"]


def infeasibility_bounds(scheduler):
    """Sufficient impossibility proofs under the declared time-linear model.

    Only tasks forced to one physical UAV are used; no heuristic failure is
    presented as a mathematical proof. Transit/climb omitted => lower bound.
    """
    proofs = []
    for uid, u in scheduler.fleet.items():
        tids = [tid for tid, eligible in scheduler.eligible.items() if set(eligible) == {uid}]
        if not tids:
            continue
        usable = u["operational_endurance_min"]*60*(1-u["energy_reserve_fraction"])
        work = sum(scheduler.task_seconds(uid, tid) for tid in tids)
        capacity = usable-u["takeoff_time_s"]-u["landing_time_s"]
        if capacity <= 0:
            proofs.append(dict(code="no_airborne_work_capacity",uav_id=uid,task_ids=tids))
            continue
        minimum_sorties = max(1, math.ceil((work-1e-6)/capacity))
        lower_bound = work+minimum_sorties*(u["takeoff_time_s"]+u["landing_time_s"])+(minimum_sorties-1)*u["service_time_s"]
        if scheduler.horizon is not None and lower_bound > scheduler.horizon+1e-6:
            proofs.append(dict(code="forced_work_exceeds_window",uav_id=uid,task_ids=tids,
                               minimum_duration_s=lower_bound,window_s=scheduler.horizon,
                               minimum_sorties=minimum_sorties))
        landing = scheduler.sites.get(u["landing_site"])
        if landing and landing["role"] not in {"both", "start"} and minimum_sorties > 1:
            proofs.append(dict(code="landing_site_cannot_launch_required_next_sortie",uav_id=uid,
                               task_ids=tids,landing_site=landing["id"],minimum_sorties=minimum_sorties,
                               survey_work_s=work,usable_endurance_s=usable))
    return proofs


def plan_bundle(source, settings=None, scene=None, progress=None):
    start = time.monotonic()
    settings = settings or Settings()
    settings.validate()
    bundle = load_bundle(source)
    if settings.objective not in bundle["mission"]["objectives"]:
        raise ValueError("objective is not allowed by mission.objectives")
    unknown = (set(settings.task_windows) | set(settings.task_service_s)) - {t["id"] for t in bundle["tasks"]}
    if unknown:
        raise ValueError(f"unknown task side-input ids: {sorted(unknown)}")
    if settings.algorithm == 'routing':
        from h1_coverage.transect_bundle import export_transect_bundle
        from .routing_planner import plan_routing
        return plan_routing(export_transect_bundle(bundle), settings, scene, progress)
    if settings.search_depth is not None:
        from .annealing import plan_annealed
        return plan_annealed(bundle, settings, scene, progress)
    sch = Scheduler(bundle, settings, scene)
    deadline = start+settings.time_budget_s
    # The budget limits improvements, not whether mandatory tasks are visited.
    sch.deadline = None
    rng = random.Random(settings.seed)
    assignment = {uid: [] for uid in sch.fleet}
    raw_by_uav = {uid: [] for uid in sch.fleet}
    reasons = {}
    failure_details = {}
    history = []

    def report(stage, routes, missing):
        entry = dict(stage=stage, elapsed_s=time.monotonic()-start,
                     objective_value=objective_value(routes, settings.objective), unassigned_count=len(missing))
        history.append(entry)
        if progress:
            progress(deepcopy(entry))

    # Complete a finite construction pass before applying the search deadline.
    task_order = sorted(sch.tasks, key=lambda tid: (len(sch.eligible[tid]), -sch.tasks[tid]["survey_length_m"], tid))
    for tid in task_order:
        if not sch.eligible[tid]:
            reasons[tid] = "no_eligible_uav"
            continue
        choices = []
        failures = []
        for uid in sch.eligible[tid]:
            try:
                routes = sch.append_task(uid, raw_by_uav[uid], tid)
                all_routes = [s for k,v in raw_by_uav.items() for s in (routes if k == uid else v)]
                choices.append((objective_value(all_routes, settings.objective), uid, routes))
            except RouteFailure as exc:
                failures.append(dict(uav_id=uid, reason=str(exc), **exc.details))
                continue
        if choices:
            _, uid, routes = min(choices, key=lambda c: c[:2])
            assignment[uid].append(tid)
            raw_by_uav[uid] = routes
        else:
            reasons[tid] = "no_resource_site_window_route_found"
            failure_details[tid] = failures

    def evaluate(a, precomputed=None):
        routes = sch.schedule(a) if precomputed is None else precomputed
        routes = _temporal_schedule(routes, scene, sch.horizon)
        before = detect_conflicts(routes, bundle["fleet"])
        ladder = []
        if settings.deconflict:
            routes, conflicts, ladder = resolve_conflicts(
                routes, bundle["fleet"], sch.horizon, return_actions=True
            )
        else:
            conflicts = before
        # Conflict shifts can move another sortie into temporal restricted time.
        for _ in range(3):
            adjusted = _temporal_schedule(routes, scene, sch.horizon)
            if adjusted == routes:
                break
            routes = adjusted
            if settings.deconflict:
                routes, conflicts, more = resolve_conflicts(
                    routes, bundle["fleet"], sch.horizon, return_actions=True
                )
                ladder = list(ladder) + list(more)
        conflicts = detect_conflicts(routes, bundle["fleet"])
        if any((w := settings.task_windows.get(t["task_id"])) and not w[0]-1e-6 <= t["start_s"] <= w[1]+1e-6 for s in routes for t in s["task_times"]):
            raise RouteFailure("deconfliction violates task time window")
        return routes, conflicts, before, ladder

    best_ladder = []
    try:
        best_routes, best_conflicts, before, best_ladder = evaluate(
            assignment, [s for v in raw_by_uav.values() for s in v]
        )
    except RouteFailure:
        best_routes = [s for routes in raw_by_uav.values() for s in routes]
        best_conflicts = detect_conflicts(best_routes, bundle["fleet"])
        before = best_conflicts
        best_ladder = []
        # Independent checker below prevents a raw schedule from being passed.
    best_assignment = deepcopy(assignment)

    def valid_candidate(routes):
        from .checks import check_plan
        assigned_ids = {t for s in routes for t in s["task_ids"]}
        partial = dict(sorties=routes, metrics=metrics(routes), tasks=bundle["tasks"],
                       unassigned=[dict(task_id=t, reason="partial") for t in sch.tasks if t not in assigned_ids])
        check = check_plan(bundle, partial, settings, scene)
        return not any(v["code"] != "unassigned_task" for v in check["violations"])

    def key(a, routes, conflicts):
        assigned = sum(len(x) for x in a.values())
        return (len(sch.tasks)-assigned, len(conflicts), objective_value(routes, settings.objective))

    best_key = key(assignment, best_routes, best_conflicts)
    best_valid = valid_candidate(best_routes)
    report("greedy", best_routes, reasons)
    sch.deadline = deadline
    if settings.cpsat and sch.tasks and time.monotonic() < deadline:
        candidate = _cp_assignment(sch, min(2., max(.001,(deadline-time.monotonic())*.25)), settings.seed, deadline)
        if candidate is not None:
            try:
                routes, conflicts, prior, ladder = evaluate(candidate)
                if (not best_valid or key(candidate, routes, conflicts) < best_key) and valid_candidate(routes):
                    best_assignment, best_routes, best_conflicts, before, best_ladder = (
                        candidate, routes, conflicts, prior, ladder
                    )
                    best_key = key(candidate, routes, conflicts)
                    best_valid = True
                    report("cpsat_incumbent", routes, [])
            except RouteFailure:
                pass
    # Relocate / reorder / reverse subsequence / swap neighborhoods. Previously
    # unassigned tasks can be inserted; partial incumbents never masquerade as full.
    iterations = 0
    for iteration in range(settings.max_iterations):
        if time.monotonic() >= deadline or not sch.tasks:
            break
        candidate = deepcopy(best_assignment)
        placed = {tid for seq in candidate.values() for tid in seq}
        missing = sorted(set(sch.tasks)-placed)
        tid = rng.choice(missing if missing else list(sch.tasks))
        eligible = sch.eligible[tid]
        if not eligible:
            continue
        for seq in candidate.values():
            if tid in seq:
                seq.remove(tid)
        uid = rng.choice(eligible)
        candidate[uid].insert(rng.randrange(len(candidate[uid])+1), tid)
        if iteration % 3 == 1 and len(candidate[uid]) > 2:
            i, j = sorted(rng.sample(range(len(candidate[uid])), 2))
            candidate[uid][i:j+1] = reversed(candidate[uid][i:j+1])
        if iteration % 3 == 2:
            others = [k for k,v in candidate.items() if v and k != uid]
            if others and candidate[uid]:
                other = rng.choice(others)
                x, y = rng.randrange(len(candidate[uid])), rng.randrange(len(candidate[other]))
                ta, tb = candidate[uid][x], candidate[other][y]
                if uid in sch.eligible[tb] and other in sch.eligible[ta]:
                    candidate[uid][x], candidate[other][y] = tb, ta
        iterations += 1
        try:
            routes, conflicts, prior, ladder = evaluate(candidate)
        except RouteFailure:
            continue
        if (not best_valid or key(candidate, routes, conflicts) < best_key) and valid_candidate(routes):
            best_assignment, best_routes, best_conflicts, before, best_ladder = (
                candidate, routes, conflicts, prior, ladder
            )
            best_key = key(candidate, routes, conflicts)
            best_valid = True
            report("lns_incumbent", routes, set(sch.tasks)-{t for seq in candidate.values() for t in seq})
    assigned = {tid for s in best_routes for tid in s["task_ids"]}
    unassigned = [dict(task_id=tid, reason=reasons.get(tid, "search_did_not_find_feasible_assignment"),
                       construction_attempts=failure_details.get(tid, [])) for tid in sch.tasks if tid not in assigned]
    plan = dict(schema_version="h2.plan.v1", scene_id=bundle["scene_id"],
                input_schema_version=bundle["schema_version"], input_sha256=fingerprint(bundle),
                crs=deepcopy(bundle["crs"]), time_origin=sch.origin.isoformat(),
                objective=settings.objective, status="UNRESOLVED", tasks=deepcopy(bundle["tasks"]),
                sorties=best_routes, unassigned=unassigned, metrics=metrics(best_routes),
                solver_log=history, settings=asdict(settings),
                deconfliction=dict(
                    before=before,
                    remaining=best_conflicts,
                    ladder_actions=best_ladder,
                    ladder_used=sorted({a["rung"] for a in best_ladder}),
                ),
                assumptions=["linear time resource model", "fixed-wing launch/landing scheduling envelopes; H3 checks manoeuvres"],
                requires_h3_validation=True, certificate=None)
    if scene is None:
        plan["assumptions"].append("airspace unavailable: straight-line transit requires H3 validation")
    if not sch.context.has_terrain:
        plan["assumptions"].append("flat reference elevation 0: actual AMSL/terrain separation unverified")
    from .checks import check_plan
    plan["checks"] = check_plan(bundle, plan, settings=settings, scene=scene)
    plan["infeasibility_proofs"] = infeasibility_bounds(sch)
    if plan["checks"]["passed"] and not unassigned and not best_conflicts:
        plan["status"] = "FEASIBLE"
    elif plan["infeasibility_proofs"] or any(x["reason"] == "no_eligible_uav" for x in unassigned):
        plan["status"] = "INFEASIBLE"
    plan["metrics"].update(runtime_s=time.monotonic()-start, iterations=iterations,
                           unassigned_count=len(unassigned), conflict_count=len(best_conflicts))
    return plan
