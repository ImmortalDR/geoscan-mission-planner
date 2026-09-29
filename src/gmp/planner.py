"""End-to-end planning pipeline.

coverage -> capacity/reachability feasibility -> optimisation (baselines,
CP-SAT, LNS) -> scheduling -> 4D deconfliction -> independent validation ->
Safety Certificate -> recommendations.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from .coverage.engine import CoverageResult, build_coverage
from .deconfliction.resolver import resolve as resolve_conflicts
from .energy.model import ResourceContext
from .energy.scheduler import MissionScheduler, ScheduleOptions, compute_metrics
from .kb.payloads import default_payload_kb
from .models import Assignment, Plan, Scene
from .optimizer import baselines as baseline_mod
from .optimizer.core import PlanningContext
from .optimizer.cpsat import solve_assignment
from .optimizer.lns import LnsSolver
from .safety.certificate import issue_certificate
from .safety.validator import INFEASIBLE, SAFE, UNSAFE, SafetyValidator
from .transit import RouterBank

ProgressCb = Callable[[dict[str, Any]], None]


@dataclass
class PlannerOptions:
    objective: str = "makespan"
    time_budget_s: float = 60.0
    cpsat_share: float = 0.35
    seed: int = 20260918
    deconflict: bool = True
    recommend: bool = True
    max_recommendations: int = 5
    sweep_step_deg: float = 15.0
    detector_step_s: float = 5.0
    run_baseline_benchmark: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "time_budget_s": self.time_budget_s,
            "cpsat_share": self.cpsat_share,
            "seed": self.seed,
            "deconflict": self.deconflict,
            "recommend": self.recommend,
            "sweep_step_deg": self.sweep_step_deg,
            "detector_step_s": self.detector_step_s,
        }


# --------------------------------------------------------------------------- #
# Feasibility analysis
# --------------------------------------------------------------------------- #
def capacity_analysis(scene: Scene, coverage: CoverageResult) -> dict[str, Any]:
    """Compare required survey work with the fleet capacity inside the window.

    This is what makes an over-subscribed scene provable rather than hidden: for
    each payload class we compute required survey hours and the airborne hours
    the compatible, wind-feasible aircraft can deliver in the mission window.
    """
    kb = default_payload_kb()
    window_s = (scene.mission.effective_end() - scene.mission.effective_start()).total_seconds()
    wind = scene.mission.wind.speed_ms

    per_class: dict[str, dict[str, Any]] = {}
    for task in coverage.tasks.values():
        cls = per_class.setdefault(
            task.payload_class,
            {"payload_class": task.payload_class, "survey_length_m": 0.0, "tasks": 0},
        )
        cls["survey_length_m"] += task.survey_length_m
        cls["tasks"] += 1

    fleet_by_class: dict[str, list[str]] = {}
    for cls in per_class:
        fleet_by_class[cls] = [u.id for u in scene.fleet if u.supports(cls) and wind <= u.max_wind_ms]

    shortfalls: list[dict[str, Any]] = []
    for cls, data in per_class.items():
        uavs = [scene.uav(u) for u in fleet_by_class[cls]]
        uavs = [u for u in uavs if u is not None]
        if not uavs:
            data.update(
                {
                    "compatible_uavs": [],
                    "required_survey_h": None,
                    "available_airborne_h": 0.0,
                    "feasible": False,
                    "reason": "no wind-feasible UAV carries this payload",
                }
            )
            shortfalls.append(data)
            continue
        speeds = [u.ground_speed_ms * kb.survey_speed_factor(cls) for u in uavs]
        best_speed = max(speeds)
        required_h = data["survey_length_m"] / max(best_speed, 0.1) / 3600.0
        available_h = 0.0
        for u in uavs:
            cycle_s = u.usable_endurance_s + u.service_time_s
            n_cycles = max(window_s / cycle_s, 0.0)
            available_h += n_cycles * u.usable_endurance_s / 3600.0
        # Transit and turns consume part of every sortie.
        usable_fraction = 0.8
        data.update(
            {
                "compatible_uavs": [u.id for u in uavs],
                "survey_length_km": round(data["survey_length_m"] / 1000.0, 1),
                "best_survey_speed_ms": round(best_speed, 2),
                "required_survey_h": round(required_h, 2),
                "available_airborne_h": round(available_h, 2),
                "available_survey_h_estimate": round(available_h * usable_fraction, 2),
                "feasible": required_h <= available_h * usable_fraction,
                "utilisation_percent": round(
                    100.0 * required_h / max(available_h * usable_fraction, 1e-9), 1
                ),
            }
        )
        if not data["feasible"]:
            data["reason"] = (
                f"required {data['required_survey_h']} survey hours exceed the estimated "
                f"{data['available_survey_h_estimate']} hours deliverable by "
                f"{len(uavs)} compatible aircraft inside the mission window"
            )
            shortfalls.append(data)

    return {
        "mission_window_h": round(window_s / 3600.0, 2),
        "per_payload_class": list(per_class.values()),
        "over_subscribed": bool(shortfalls),
        "shortfalls": shortfalls,
        "note": (
            "estimate assumes 80 % of airborne time is spent on survey lines; it is a "
            "screening test, the authoritative verdict comes from the scheduled plan"
        ),
    }


def reachability_analysis(pc: PlanningContext) -> dict[str, Any]:
    """Tasks that no single UAV can fly from any permitted site and return."""
    unreachable: list[dict[str, Any]] = []
    reachable = 0
    for tid in pc.task_ids:
        cands = pc.eligible[tid]
        best: dict[str, Any] | None = None
        ok = False
        for uid in cands:
            cost, feasible = pc.trip_cost(uid, [tid])
            uav = pc.uav(uid)
            if best is None or cost < best["required_s"]:
                best = {
                    "uav": uid,
                    "required_s": cost,
                    "usable_s": uav.usable_endurance_s,
                    "depot": pc.depot[uid].id,
                }
            if feasible:
                ok = True
                break
        if ok:
            reachable += 1
        else:
            unreachable.append(
                {
                    "task": tid,
                    "payload_class": pc.tasks[tid].payload_class,
                    "job": pc.tasks[tid].job_id,
                    "eligible_uavs": cands,
                    "closest_attempt": best,
                }
            )
    return {
        "task_count": len(pc.task_ids),
        "reachable": reachable,
        "unreachable_count": len(unreachable),
        "unreachable": unreachable[:20],
        "all_reachable": not unreachable,
    }


# --------------------------------------------------------------------------- #
def trim_to_window(
    pc: PlanningContext, assignment: Assignment, objective: str
) -> tuple[Assignment, list[str], list[dict[str, Any]]]:
    """Drop tasks that do not fit the mission window / endurance.

    Returns the trimmed assignment, the dropped task ids and a log. Dropping is
    never silent: the caller reports the coverage shortfall and the
    Recommendation Engine proposes how to restore full coverage.
    """
    sets = pc.sets_from_assignment(assignment)
    dropped: list[str] = []
    log: list[dict[str, Any]] = []

    # 1. tasks that cannot be flown at all by their assigned UAV
    for uid, tids in list(sets.items()):
        keep = []
        for tid in tids:
            _, ok = pc.trip_cost(uid, [tid])
            if ok:
                keep.append(tid)
            else:
                dropped.append(tid)
                log.append({"task": tid, "reason": "single_task_exceeds_endurance", "uav": uid})
        sets[uid] = keep

    guard = 0
    while guard < 5000:
        guard += 1
        current = pc.assignment_from_sets(sets, polish=False)
        ev = pc.evaluate(current, objective)
        if ev.makespan_s <= pc.window_s + 1e-6 and ev.infeasible_sorties == 0:
            return pc.assignment_from_sets(sets, polish=True), dropped, log
        worst = max(ev.per_uav_completion_s, key=lambda u: ev.per_uav_completion_s[u])
        if not sets.get(worst):
            # nothing left to drop on the critical UAV
            others = [u for u in sets if sets[u]]
            if not others:
                break
            worst = max(others, key=lambda u: ev.per_uav_completion_s.get(u, 0.0))
            if not sets.get(worst):
                break
        depot = pc.depot[worst].xy
        victim = max(sets[worst], key=lambda t: math.dist(depot, pc.task_centroid(t)))
        sets[worst].remove(victim)
        dropped.append(victim)
        log.append(
            {
                "task": victim,
                "reason": "mission_window_capacity",
                "uav": worst,
                "makespan_s": round(ev.makespan_s, 1),
                "window_s": round(pc.window_s, 1),
            }
        )
    return pc.assignment_from_sets(sets, polish=True), dropped, log


# --------------------------------------------------------------------------- #
def plan_mission(
    scene: Scene,
    options: PlannerOptions | None = None,
    progress_cb: ProgressCb | None = None,
    coverage: CoverageResult | None = None,
) -> Plan:
    options = options or PlannerOptions()
    t_start = time.time()
    log: list[dict[str, Any]] = []

    def report(stage: str, **extra: Any) -> None:
        entry = {"stage": stage, "t_s": round(time.time() - t_start, 2), **extra}
        log.append(entry)
        if progress_cb:
            progress_cb(entry)

    report("coverage_start")
    coverage = coverage or build_coverage(scene, step_deg=options.sweep_step_deg)
    report(
        "coverage_done",
        tasks=len(coverage.tasks),
        coverage_percent=round(coverage.coverage_percent, 3),
        survey_km=round(sum(t.survey_length_m for t in coverage.tasks.values()) / 1000.0, 1),
    )

    ctx = ResourceContext(scene)
    routers = RouterBank(scene)
    pc = PlanningContext(scene, coverage.tasks, ctx, routers)
    scheduler = MissionScheduler(scene, ctx, routers)

    capacity = capacity_analysis(scene, coverage)
    reach = reachability_analysis(pc)
    report(
        "feasibility_done",
        over_subscribed=capacity["over_subscribed"],
        unreachable_tasks=reach["unreachable_count"],
    )

    if not pc.fleet or not coverage.tasks:
        plan = Plan(scene_id=scene.id, objective=options.objective, tasks=coverage.tasks)
        plan.status = INFEASIBLE
        plan.coverage = coverage.as_dict()
        plan.metrics = {"feasibility": {"capacity": capacity, "reachability": reach}}
        plan.diagnosis = {
            "reason_codes": ["no_usable_fleet" if not pc.fleet else "no_coverage_tasks"],
            "capacity": capacity,
            "reachability": reach,
        }
        plan.solver_log = log
        return plan

    # ---------------- construction: baselines ---------------- #
    report("baselines_start")
    base_assignments = baseline_mod.build_all(pc, options.objective)
    baseline_scores: dict[str, Any] = {}
    best_name, best_assignment, best_eval = None, None, None
    for name, asg in base_assignments.items():
        if not isinstance(asg, Assignment):
            continue
        ev = pc.evaluate(asg, options.objective)
        baseline_scores[name] = ev.as_dict()
        if best_eval is None or ev.objective < best_eval.objective:
            best_name, best_assignment, best_eval = name, asg, ev
    report("baselines_done", scores=baseline_scores, best=best_name)

    # ---------------- CP-SAT assignment ---------------- #
    cpsat_budget = max(5.0, options.time_budget_s * options.cpsat_share)
    report("cpsat_start", time_limit_s=round(cpsat_budget, 1))
    cpsat_assignment, cpsat_info = solve_assignment(
        pc,
        objective=options.objective,
        time_limit_s=cpsat_budget,
        warm_start=best_assignment,
    )
    if cpsat_assignment is not None:
        ev = pc.evaluate(cpsat_assignment, options.objective)
        cpsat_info["evaluation"] = ev.as_dict()
        if best_eval is None or ev.objective < best_eval.objective:
            best_name, best_assignment, best_eval = "cpsat", cpsat_assignment, ev
    report("cpsat_done", **{k: v for k, v in cpsat_info.items() if k != "evaluation"})

    # ---------------- LNS improvement (anytime) ---------------- #
    remaining = max(5.0, options.time_budget_s - (time.time() - t_start))
    lns = LnsSolver(
        pc,
        objective=options.objective,
        seed=options.seed,
        progress_cb=(lambda e: progress_cb({"stage": "lns", **e}) if progress_cb else None),
    )
    report("lns_start", time_budget_s=round(remaining, 1), start_from=best_name)
    lns_result = lns.solve(best_assignment, time_budget_s=remaining)
    report(
        "lns_done",
        iterations=lns_result.iterations,
        improvements=len(lns_result.history) - 1,
        objective=lns_result.evaluation.as_dict(),
        operators=lns_result.operator_stats,
    )
    assignment = lns_result.assignment

    # ---------------- capacity trimming ---------------- #
    assignment, dropped, drop_log = trim_to_window(pc, assignment, options.objective)
    if dropped:
        report("capacity_trim", dropped=len(dropped))

    # ---------------- scheduling ---------------- #
    report("schedule_start")
    plan = scheduler.schedule(assignment, coverage.tasks, objective=options.objective)
    report("schedule_done", **{k: plan.metrics.get(k) for k in ("makespan_min", "total_flight_min", "sortie_count")})

    # ---------------- 4D deconfliction ---------------- #
    deconfliction = None
    if options.deconflict:
        report("deconfliction_start")

        def reoptimize(current: Assignment, conflicts) -> Assignment:
            sub = LnsSolver(pc, objective=options.objective, seed=options.seed + 7)
            return sub.solve(current, time_budget_s=min(8.0, options.time_budget_s * 0.1)).assignment

        deconfliction = resolve_conflicts(
            scene,
            scheduler,
            assignment,
            coverage.tasks,
            options.objective,
            reoptimize=reoptimize,
            step_s=options.detector_step_s,
        )
        plan = deconfliction.plan
        assignment = Assignment(per_uav=plan_to_per_uav(plan))
        report(
            "deconfliction_done",
            initial=len(deconfliction.initial_conflicts),
            final=len(deconfliction.final_conflicts),
            rungs=sorted({a["rung"] for a in deconfliction.actions}),
        )

    # ---------------- validation ---------------- #
    report("validation_start")
    validator = SafetyValidator(scene)
    report_ = validator.validate(plan)
    plan.validation = report_.as_dict()
    plan.status = report_.status
    plan.certificate = issue_certificate(scene, plan, report_)
    report("validation_done", status=report_.status, counters=report_.counters)

    # ---------------- assemble ---------------- #
    coverage_percent = report_.checks["coverage"]["coverage_percent"]
    plan.coverage = coverage.as_dict()
    plan.metrics.update(
        {
            "coverage_percent": coverage_percent,
            "coverage_complete": coverage_percent >= 99.9,
            "dropped_task_count": len(dropped),
            "dropped_tasks": dropped[:40],
            "planner_options": options.as_dict(),
            "solver": {
                "baselines": baseline_scores,
                "baseline_best": best_name,
                "cpsat": cpsat_info,
                "lns": {
                    "iterations": lns_result.iterations,
                    "elapsed_s": lns_result.elapsed_s,
                    "history": lns_result.history,
                    "operators": lns_result.operator_stats,
                },
            },
            "feasibility": {"capacity": capacity, "reachability": reach, "trim_log": drop_log[:40]},
            "wind_excluded_uavs": pc.wind_excluded,
            "solve_time_s": round(time.time() - t_start, 2),
        }
    )
    if deconfliction is not None:
        plan.deconfliction = deconfliction.as_dict()
    plan.solver_log = log

    # ---------------- diagnosis + recommendations ---------------- #
    needs_help = (
        plan.status != SAFE
        or not plan.metrics["coverage_complete"]
        or bool(dropped)
        or reach["unreachable_count"] > 0
    )
    if needs_help:
        from .recommend.engine import diagnose, recommend

        plan.diagnosis = diagnose(scene, plan, report_, capacity, reach, dropped)
        if options.recommend:
            report("recommend_start", reasons=plan.diagnosis["reason_codes"])
            plan.recommendations = recommend(
                scene,
                plan,
                plan.diagnosis,
                options=options,
                top_k=options.max_recommendations,
            )
            report(
                "recommend_done",
                count=len(plan.recommendations),
                verified=len([r for r in plan.recommendations if r.get("verified")]),
            )
    return plan


def plan_to_per_uav(plan: Plan) -> dict[str, list[list[str]]]:
    out: dict[str, list[list[str]]] = {}
    for sortie in sorted(plan.sorties, key=lambda s: (s.uav_id, s.index)):
        out.setdefault(sortie.uav_id, []).append(list(sortie.task_ids))
    return out
