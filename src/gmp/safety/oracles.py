"""Evaluate a planned mission against a scenario's expected_assertions.json."""

from __future__ import annotations

from typing import Any

from gmp.models import Plan
from gmp.safety.validator import INFEASIBLE, SAFE, UNSAFE


def evaluate_assertions(plan: Plan, assertions: dict[str, Any], scene=None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    counters = (plan.validation or {}).get("counters") or {}
    status = plan.status

    def add(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    expected = assertions.get("expected_status")
    if expected == "SAFE":
        add("status_SAFE", status == SAFE, status)
    elif expected == "SAFE_AFTER_OPTIMIZATION":
        add("status_SAFE_AFTER_OPTIMIZATION", status == SAFE, status)
    elif expected == "SAFE_AFTER_DECONFLICTION":
        add("status_SAFE_AFTER_DECONFLICTION", status == SAFE, status)
        if assertions.get("deconfliction_action_required"):
            dc = plan.deconfliction or {}
            add(
                "deconfliction_action_required",
                (dc.get("initial_conflict_count") or 0) > 0 and (dc.get("final_conflict_count") == 0),
                dc,
            )
    elif expected == "SAFE_OR_EXPLAINED_INFEASIBLE":
        add(
            "status_SAFE_OR_EXPLAINED_INFEASIBLE",
            status in (SAFE, INFEASIBLE) and (status == SAFE or bool(plan.diagnosis)),
            status,
        )
    elif expected == "SAFE_IF_COMPATIBLE_AIRCRAFT_AVAILABLE":
        add("status_wind_feasible_fleet", status in (SAFE, INFEASIBLE), status)
    elif expected == "INFEASIBLE":
        add("status_INFEASIBLE", status == INFEASIBLE, status)

    if "expected_initial_status" in assertions:
        add(
            "expected_initial_status",
            status == assertions["expected_initial_status"],
            status,
        )
    if assertions.get("coverage_required") is not None:
        cov = (plan.metrics or {}).get("coverage_percent") or 0.0
        add("coverage_required", cov >= 99.9 * assertions["coverage_required"], cov)
    for key in (
        "nfz_violations",
        "obstacle_violations",
        "vehicle_conflicts",
        "airspace_time_violations",
        "payload_compatibility_violations",
    ):
        if key in assertions:
            add(key, key in counters and counters[key] == assertions[key], counters.get(key))
    if "vehicle_conflicts_final" in assertions:
        final = (plan.deconfliction or {}).get("final_conflict_count", counters.get("vehicle_conflicts"))
        add("vehicle_conflicts_final", final == assertions["vehicle_conflicts_final"], final)
    if assertions.get("reserve_landing_reachability") is True:
        add(
            "reserve_landing_reachability",
            counters.get("reserve_landing_violations") == 0,
            counters.get("reserve_landing_violations"),
        )
    if assertions.get("minimum_sorties"):
        add(
            "minimum_sorties",
            (plan.metrics or {}).get("sortie_count", 0) >= assertions["minimum_sorties"],
            plan.metrics.get("sortie_count"),
        )
    if assertions.get("each_sortie_resource_feasible"):
        add("each_sortie_resource_feasible", counters.get("resource_violations") == 0)
    if assertions.get("reason_contains"):
        codes = (plan.diagnosis or {}).get("reason_codes") or []
        add("reason_contains", assertions["reason_contains"] in codes, codes)
    if assertions.get("expected_recommendation_type"):
        types = [r.get("type") for r in plan.recommendations]
        add(
            "expected_recommendation_type",
            assertions["expected_recommendation_type"] in types,
            types,
        )
    if assertions.get("expected_candidate"):
        cand = assertions["expected_candidate"]
        hit = any(
            r.get("candidate") == cand or cand in str(r.get("changes"))
            for r in plan.recommendations
        )
        add("expected_candidate", hit, cand)
    if assertions.get("recommended_replan_must_be_safe"):
        verified = [r for r in plan.recommendations if r.get("verified")]
        add("recommended_replan_must_be_safe", bool(verified), [r.get("type") for r in verified])
    if assertions.get("start_site") or assertions.get("landing_site"):
        if plan.sorties:
            if assertions.get("start_site"):
                add(
                    "start_site",
                    all(s.start_site_id == assertions["start_site"] for s in plan.sorties),
                    [s.start_site_id for s in plan.sorties],
                )
            if assertions.get("landing_site"):
                add(
                    "landing_site",
                    all(s.landing_site_id == assertions["landing_site"] for s in plan.sorties),
                    [s.landing_site_id for s in plan.sorties],
                )
    if assertions.get("all_jobs_assigned_exactly_once"):
        add(
            "all_jobs_assigned_exactly_once",
            (plan.validation or {}).get("checks", {}).get("assignment", {}).get("assigned_exactly_once", False),
        )
    if assertions.get("wind_limit_must_filter_fleet"):
        excluded = (plan.metrics or {}).get("wind_excluded_uavs") or []
        eligible = {u.id for u in scene.fleet if scene.mission.wind.speed_ms <= u.max_wind_ms} if scene else set()
        add("wind_limit_must_filter_fleet", scene is not None and all(s.uav_id in eligible for s in plan.sorties), excluded)
    if assertions.get("wind_must_not_modify_energy_model"):
        add("wind_must_not_modify_energy_model", scene is not None and scene.mission.wind_energy_model_enabled is False)
    if assertions.get("fixed_wing_maneuver_buffer_must_be_checked"):
        add(
            "fixed_wing_maneuver_buffer_must_be_checked",
            "fixed_wing_maneuver" in ((plan.validation or {}).get("checks") or {}),
        )
    if assertions.get("preserve_holes") or assertions.get("preserve_multipolygon"):
        independent = (plan.validation or {}).get("checks", {}).get("independent_h3", {})
        per_job = independent.get("metrics", {}).get("per_job", {})
        preserved = scene is not None and bool(per_job) and all(
            j.id in per_job and abs(per_job[j.id]["required_area_m2"] - j.geom.area) <= max(0.01, j.geom.area * 1e-8)
            for j in scene.jobs)
        add("geometry_preserved", preserved, "Original polygon topology is used by the independent checker")
    if assertions.get("schedule_required"):
        add("schedule_required", all(s.t_start and s.t_end for s in plan.sorties) if plan.sorties else status != SAFE)

    passed = all(c["ok"] for c in checks)
    return {
        "passed": passed,
        "status": status,
        "checks": checks,
        "failed": [c["name"] for c in checks if not c["ok"]],
        "metrics": {
            "coverage_percent": (plan.metrics or {}).get("coverage_percent"),
            "makespan_min": (plan.metrics or {}).get("makespan_min"),
            "total_flight_min": (plan.metrics or {}).get("total_flight_min"),
            "sortie_count": (plan.metrics or {}).get("sortie_count"),
            "used_uav_count": (plan.metrics or {}).get("used_uav_count"),
            "solve_time_s": (plan.metrics or {}).get("solve_time_s"),
            "certificate": bool(plan.certificate),
        },
    }
