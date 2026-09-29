"""Independent Safety Validator (TS §17).

This module deliberately does not reuse the optimizer's internal state: it takes
the scene and the produced plan and re-derives everything it checks from the
plan geometry and timing. Its verdict (SAFE / UNSAFE / INFEASIBLE) is the gate
for the Safety Certificate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from ..coverage.engine import coverage_half_width_m
from ..deconfliction.detector import detect_conflicts
from ..geo import densify, union_all
from ..models import Plan, Scene, Sortie, Uav

SAFE = "SAFE"
UNSAFE = "UNSAFE"
INFEASIBLE = "INFEASIBLE"

#: Sampling step for point-wise checks along the route.
SAMPLE_STEP_M = 100.0
#: Extra time margin required on top of the reserve for a diversion.
DIVERSION_MARGIN_S = 60.0
#: Minimum acceptable coverage of the effective survey area.
COVERAGE_TOLERANCE = 0.999


@dataclass
class Violation:
    code: str
    severity: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class ValidationReport:
    scene_id: str
    status: str
    checks: dict[str, Any] = field(default_factory=dict)
    violations: list[Violation] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "status": self.status,
            "counters": self.counters,
            "checks": self.checks,
            "violations": [v.as_dict() for v in self.violations],
            "metrics": self.metrics,
            "reasons": self.reasons,
            "validated_at": datetime.now().astimezone().isoformat(),
        }


class SafetyValidator:
    def __init__(self, scene: Scene, sample_step_m: float = SAMPLE_STEP_M):
        self.scene = scene
        self.step = sample_step_m
        self.hard_nfz = union_all([z.geom for z in scene.no_fly_zones if z.hard])
        self.allowed = union_all([z.geom for z in scene.allowed_airspace])

    # ------------------------------------------------------------------ #
    def _route_lines(self, plan: Plan) -> list[tuple[Sortie, LineString]]:
        out = []
        for s in plan.sorties:
            if len(s.waypoints) < 2:
                continue
            out.append((s, LineString([(w.x, w.y) for w in s.waypoints])))
        return out

    def _terrain(self, x: float, y: float) -> float:
        return self.scene.dem.elevation(x, y) if self.scene.dem.available else 0.0

    # ------------------------------------------------------------------ #
    def validate(self, plan: Plan) -> ValidationReport:
        rep = ValidationReport(scene_id=self.scene.id, status=SAFE)
        counters = {
            "nfz_violations": 0,
            "allowed_airspace_violations": 0,
            "obstacle_violations": 0,
            "airspace_time_violations": 0,
            "vehicle_conflicts": 0,
            "resource_violations": 0,
            "reserve_landing_violations": 0,
            "payload_compatibility_violations": 0,
            "fixed_wing_maneuver_violations": 0,
            "site_feasibility_violations": 0,
            "window_violations": 0,
            "wind_violations": 0,
            "unassigned_tasks": 0,
        }
        routes = self._route_lines(plan)

        self._check_coverage(plan, rep)
        self._check_assignment(plan, rep, counters)
        self._check_nfz_and_airspace(routes, rep, counters)
        self._check_obstacles(plan, rep, counters)
        self._check_temporal_airspace(plan, rep, counters)
        self._check_separation(plan, rep, counters)
        self._check_resource(plan, rep, counters)
        self._check_reserve_landing(plan, rep, counters)
        self._check_sites_and_window(plan, rep, counters)
        self._check_fixed_wing_maneuver(plan, rep, counters)
        self._check_wind(plan, rep, counters)

        rep.counters = counters
        hard = [v for v in rep.violations if v.severity == "hard"]
        blocking = [v for v in rep.violations if v.severity == "infeasible"]
        # A failed candidate is not a proof that no feasible mission exists.
        if blocking or hard:
            rep.status = UNSAFE
        else:
            rep.status = SAFE
        rep.reasons = [v.code for v in rep.violations if v.severity in ("hard", "infeasible")]
        rep.metrics = dict(plan.metrics)
        from .model_adapter import validate_model_plan

        independent = validate_model_plan(self.scene, plan)
        rep.checks["independent_h3"] = independent
        if not independent["passed"]:
            rep.status = UNSAFE
            for issue in independent["violations"]:
                rep.violations.append(Violation(issue["code"], "hard", issue["message"], issue))
            rep.reasons.extend(issue["code"] for issue in independent["violations"])
        return rep

    # ------------------------------------------------------------------ #
    def _check_coverage(self, plan: Plan, rep: ValidationReport) -> None:
        """Re-derive coverage from the plan's survey waypoints only."""
        per_job: list[dict[str, Any]] = []
        total_target = 0.0
        total_covered = 0.0
        swaths: dict[str, list[BaseGeometry]] = {}
        for sortie in plan.sorties:
            cur: list[tuple[float, float]] = []
            cur_task = None
            for wp in sortie.waypoints + [None]:
                tid = wp.task_id if (wp and wp.phase == "survey") else None
                if tid != cur_task:
                    if cur_task is not None and len(cur) >= 2:
                        task = plan.tasks.get(cur_task)
                        if task is not None:
                            payload = self.scene.payloads.get(task.payload_profile_id)
                            half = coverage_half_width_m(payload) if payload else 25.0
                            swaths.setdefault(task.job_id, []).append(
                                LineString(cur).buffer(half, cap_style=1)
                            )
                    cur = []
                    cur_task = tid
                if tid is not None and wp is not None:
                    cur.append((wp.x, wp.y))

        for job in self.scene.jobs:
            target = job.effective_geom if job.effective_geom is not None else job.geom
            if target is None or target.is_empty:
                continue
            covered_geom = union_all(swaths.get(job.id, []))
            covered = target.intersection(covered_geom).area if not covered_geom.is_empty else 0.0
            total_target += target.area
            total_covered += covered
            pct = 100.0 * covered / max(target.area, 1e-9)
            per_job.append(
                {
                    "job_id": job.id,
                    "survey_type": job.survey_type,
                    "effective_area_km2": round(target.area / 1e6, 3),
                    "covered_km2": round(covered / 1e6, 3),
                    "coverage_percent": round(pct, 3),
                    "input_area_km2": round(job.geom.area / 1e6, 3),
                    "excluded_km2": round(
                        (job.excluded_geom.area / 1e6) if job.excluded_geom is not None else 0.0, 3
                    ),
                    "exclusion_reasons": job.exclusion_reasons,
                }
            )
            if pct < COVERAGE_TOLERANCE * 100.0:
                rep.violations.append(
                    Violation(
                        code="incomplete_coverage",
                        severity="infeasible" if self.scene.mission.require_complete_coverage else "warning",
                        message=f"job {job.id}: coverage {pct:.2f}% of the effective survey area",
                        detail={"job_id": job.id, "coverage_percent": round(pct, 3)},
                    )
                )
        overall = 100.0 * total_covered / total_target if total_target > 0 else 0.0
        rep.checks["coverage"] = {
            "coverage_percent": round(overall, 3),
            "required_percent": 100.0,
            "per_job": per_job,
            "definition": (
                "coverage is measured against the effective survey area: input area clipped to "
                "allowed airspace minus hard NFZ, obstacle protection zones and permanently "
                "altitude-capped regions"
            ),
        }

    def _check_assignment(self, plan: Plan, rep: ValidationReport, counters: dict[str, int]) -> None:
        assigned: dict[str, int] = {}
        for sortie in plan.sorties:
            uav = self.scene.uav(sortie.uav_id)
            for tid in sortie.task_ids:
                assigned[tid] = assigned.get(tid, 0) + 1
                task = plan.tasks.get(tid)
                if task is None or uav is None:
                    continue
                if not uav.supports(task.payload_class):
                    counters["payload_compatibility_violations"] += 1
                    rep.violations.append(
                        Violation(
                            code="payload_incompatible",
                            severity="hard",
                            message=(
                                f"{uav.id} ({', '.join(uav.payload_classes)}) cannot fly task {tid} "
                                f"requiring {task.payload_class}"
                            ),
                            detail={"uav": uav.id, "task": tid, "required": task.payload_class},
                        )
                    )
        missing = [t for t in plan.tasks if t not in assigned]
        duplicated = {t: n for t, n in assigned.items() if n > 1}
        counters["unassigned_tasks"] = len(missing)
        if missing:
            rep.violations.append(
                Violation(
                    code="unassigned_tasks",
                    severity="infeasible",
                    message=f"{len(missing)} atomic task(s) are not assigned to any sortie",
                    detail={"tasks": missing[:20]},
                )
            )
        if duplicated:
            rep.violations.append(
                Violation(
                    code="duplicated_tasks",
                    severity="hard",
                    message=f"{len(duplicated)} atomic task(s) are assigned more than once",
                    detail={"tasks": list(duplicated)[:20]},
                )
            )
        rep.checks["assignment"] = {
            "task_count": len(plan.tasks),
            "assigned_exactly_once": not missing and not duplicated,
            "unassigned": len(missing),
            "duplicated": len(duplicated),
        }

    def _check_nfz_and_airspace(
        self, routes: list[tuple[Sortie, LineString]], rep: ValidationReport, counters: dict[str, int]
    ) -> None:
        details: list[dict[str, Any]] = []
        for sortie, line in routes:
            if not self.hard_nfz.is_empty and line.intersects(self.hard_nfz):
                inter = line.intersection(self.hard_nfz)
                counters["nfz_violations"] += 1
                details.append(
                    {"sortie": sortie.id, "type": "nfz", "length_m": round(inter.length, 1)}
                )
                rep.violations.append(
                    Violation(
                        code="nfz_intersection",
                        severity="hard",
                        message=f"sortie {sortie.id} enters a hard no-fly zone ({inter.length:.0f} m inside)",
                        detail={"sortie": sortie.id, "inside_length_m": round(inter.length, 1)},
                    )
                )
            if not self.allowed.is_empty and not self.allowed.buffer(1.5).covers(line):
                outside = line.difference(self.allowed.buffer(1.5))
                counters["allowed_airspace_violations"] += 1
                details.append(
                    {"sortie": sortie.id, "type": "outside_allowed", "length_m": round(outside.length, 1)}
                )
                rep.violations.append(
                    Violation(
                        code="outside_allowed_airspace",
                        severity="hard",
                        message=(
                            f"sortie {sortie.id} leaves the allowed airspace "
                            f"({outside.length:.0f} m outside)"
                        ),
                        detail={"sortie": sortie.id, "outside_length_m": round(outside.length, 1)},
                    )
                )
        rep.checks["airspace_geometry"] = {
            "hard_nfz_count": len([z for z in self.scene.no_fly_zones if z.hard]),
            "violations": details,
        }

    def _check_obstacles(self, plan: Plan, rep: ValidationReport, counters: dict[str, int]) -> None:
        if not self.scene.obstacles:
            rep.checks["obstacles"] = {"obstacle_count": 0, "violations": []}
            return
        details: list[dict[str, Any]] = []
        for obs in self.scene.obstacles:
            footprint = obs.protected_footprint()
            top_amsl = self._terrain(obs.geom.centroid.x, obs.geom.centroid.y) + obs.protected_top_m
            for sortie in plan.sorties:
                for wp in sortie.waypoints:
                    if wp.amsl_m >= top_amsl:
                        continue
                    if footprint.contains(Point(wp.x, wp.y)):
                        counters["obstacle_violations"] += 1
                        details.append(
                            {
                                "sortie": sortie.id,
                                "obstacle": obs.id,
                                "at": wp.t.isoformat(),
                                "altitude_amsl_m": round(wp.amsl_m, 1),
                                "protected_top_amsl_m": round(top_amsl, 1),
                            }
                        )
                        rep.violations.append(
                            Violation(
                                code="obstacle_clearance",
                                severity="hard",
                                message=(
                                    f"sortie {sortie.id} passes obstacle {obs.id} at "
                                    f"{wp.amsl_m:.0f} m AMSL, protected top {top_amsl:.0f} m"
                                ),
                                detail=details[-1],
                            )
                        )
                        break
        rep.checks["obstacles"] = {
            "obstacle_count": len(self.scene.obstacles),
            "violations": details[:20],
        }

    def _check_temporal_airspace(
        self, plan: Plan, rep: ValidationReport, counters: dict[str, int]
    ) -> None:
        zones = self.scene.airspace_constraints
        if not zones:
            rep.checks["temporal_airspace"] = {"zone_count": 0, "violations": []}
            return
        details: list[dict[str, Any]] = []
        for zone in zones:
            for sortie in plan.sorties:
                for wp in sortie.waypoints:
                    if zone.active_from and wp.t < zone.active_from:
                        continue
                    if zone.active_to and wp.t > zone.active_to:
                        continue
                    if not zone.blocks_altitude(wp.amsl_m):
                        continue
                    if zone.geom.contains(Point(wp.x, wp.y)):
                        counters["airspace_time_violations"] += 1
                        details.append(
                            {
                                "sortie": sortie.id,
                                "zone": zone.id,
                                "at": wp.t.isoformat(),
                                "altitude_amsl_m": round(wp.amsl_m, 1),
                                "window": [
                                    zone.active_from.isoformat() if zone.active_from else None,
                                    zone.active_to.isoformat() if zone.active_to else None,
                                ],
                                "altitude_band_m": [zone.min_alt_m, zone.max_alt_m],
                            }
                        )
                        rep.violations.append(
                            Violation(
                                code="airspace_time_violation",
                                severity="hard",
                                message=(
                                    f"sortie {sortie.id} is inside restriction {zone.id} at "
                                    f"{wp.t.strftime('%H:%M:%S')} ({wp.amsl_m:.0f} m AMSL)"
                                ),
                                detail=details[-1],
                            )
                        )
                        break
        rep.checks["temporal_airspace"] = {
            "zone_count": len(zones),
            "violations": details[:20],
        }

    def _check_separation(self, plan: Plan, rep: ValidationReport, counters: dict[str, int]) -> None:
        conflicts, stats = detect_conflicts(self.scene, plan, step_s=5.0)
        counters["vehicle_conflicts"] = len(conflicts)
        for c in conflicts[:20]:
            rep.violations.append(
                Violation(
                    code="separation_violation",
                    severity="hard",
                    message=(
                        f"{c.uav_a}/{c.uav_b}: horizontal {c.min_h_m:.0f} m < required "
                        f"{c.required_h_m:.0f} m with vertical {c.v_at_min_h_m:.0f} m"
                    ),
                    detail=c.as_dict(),
                )
            )
        rep.checks["separation"] = {
            "conflicts": len(conflicts),
            "detector": stats,
            "examples": [c.as_dict() for c in conflicts[:5]],
        }

    def _check_resource(self, plan: Plan, rep: ValidationReport, counters: dict[str, int]) -> None:
        rows: list[dict[str, Any]] = []
        for sortie in plan.sorties:
            uav = self.scene.uav(sortie.uav_id)
            if uav is None:
                continue
            usable = uav.usable_endurance_s
            ok = sortie.flight_time_s <= usable + 1e-6
            rows.append(
                {
                    "sortie": sortie.id,
                    "uav": uav.id,
                    "flight_time_min": round(sortie.flight_time_s / 60.0, 2),
                    "usable_endurance_min": round(usable / 60.0, 2),
                    "endurance_min": round(uav.endurance_s / 60.0, 2),
                    "reserve_fraction": uav.energy_reserve_fraction,
                    "margin_min": round((usable - sortie.flight_time_s) / 60.0, 2),
                    "margin_percent": round(100.0 * (usable - sortie.flight_time_s) / max(usable, 1e-9), 2),
                    "feasible": ok,
                }
            )
            if not ok:
                counters["resource_violations"] += 1
                rep.violations.append(
                    Violation(
                        code="sortie_resource_exceeded",
                        severity="hard",
                        message=(
                            f"sortie {sortie.id}: {sortie.flight_time_s / 60:.1f} min exceeds usable "
                            f"endurance {usable / 60:.1f} min (reserve {uav.energy_reserve_fraction:.0%})"
                        ),
                        detail=rows[-1],
                    )
                )
        rep.checks["resource"] = {
            "per_sortie": rows,
            "min_margin_percent": round(min([r["margin_percent"] for r in rows], default=0.0), 2),
        }

    def _check_reserve_landing(
        self, plan: Plan, rep: ValidationReport, counters: dict[str, int]
    ) -> None:
        """Hard constraint: a permitted landing site must stay reachable."""
        sites = [s for s in self.scene.sites if s.can_land]
        worst = {"margin_s": math.inf, "sortie": None, "at": None}
        details: list[dict[str, Any]] = []
        if not sites:
            counters["reserve_landing_violations"] += 1
            rep.violations.append(
                Violation(
                    code="reserve_landing_reachability",
                    severity="infeasible",
                    message="scene has no permitted landing site",
                )
            )
            rep.checks["reserve_landing"] = {"reachable": False, "sites": []}
            return

        for sortie in plan.sorties:
            uav = self.scene.uav(sortie.uav_id)
            if uav is None:
                continue
            failed_for_sortie = False
            for wp in sortie.waypoints:
                if wp.phase in ("takeoff", "landing"):
                    continue
                remaining = wp.remaining_endurance_s
                best = math.inf
                best_site = None
                for site in sites:
                    d = math.dist((wp.x, wp.y), site.xy)
                    t = d / max(uav.ground_speed_ms, 0.1) + uav.landing_time_s + DIVERSION_MARGIN_S
                    if t < best:
                        best, best_site = t, site.id
                margin = remaining - best
                if margin < worst["margin_s"]:
                    worst = {
                        "margin_s": margin,
                        "sortie": sortie.id,
                        "at": wp.t.isoformat(),
                        "site": best_site,
                        "remaining_s": round(remaining, 1),
                        "needed_s": round(best, 1),
                    }
                if margin < 0 and not failed_for_sortie:
                    failed_for_sortie = True
                    counters["reserve_landing_violations"] += 1
                    details.append(
                        {
                            "sortie": sortie.id,
                            "at": wp.t.isoformat(),
                            "position": [round(wp.x, 1), round(wp.y, 1)],
                            "remaining_endurance_s": round(remaining, 1),
                            "nearest_site": best_site,
                            "time_to_site_s": round(best, 1),
                        }
                    )
                    rep.violations.append(
                        Violation(
                            code="reserve_landing_reachability",
                            severity="hard",
                            message=(
                                f"sortie {sortie.id}: from {wp.t.strftime('%H:%M:%S')} no permitted "
                                f"landing site is reachable with the remaining resource "
                                f"({remaining / 60:.1f} min left, {best / 60:.1f} min needed to {best_site})"
                            ),
                            detail=details[-1],
                        )
                    )
        rep.checks["reserve_landing"] = {
            "reachable": counters["reserve_landing_violations"] == 0,
            "sites": [s.id for s in sites],
            "worst_case": worst if worst["sortie"] else None,
            "violations": details[:20],
            "diversion_margin_s": DIVERSION_MARGIN_S,
        }

    def _check_sites_and_window(
        self, plan: Plan, rep: ValidationReport, counters: dict[str, int]
    ) -> None:
        mission = self.scene.mission
        win_start, win_end = mission.effective_start(), mission.effective_end()
        rows: list[dict[str, Any]] = []
        per_uav_last: dict[str, tuple[str, datetime]] = {}
        for sortie in sorted(plan.sorties, key=lambda s: (s.uav_id, s.index)):
            start_site = self.scene.site(sortie.start_site_id)
            land_site = self.scene.site(sortie.landing_site_id)
            problems: list[str] = []
            if start_site is None or not start_site.can_start:
                problems.append("start site not permitted for take-off")
            if land_site is None or not land_site.can_land:
                problems.append("landing site not permitted for landing")
            prev = per_uav_last.get(sortie.uav_id)
            if prev and prev[0] != sortie.start_site_id:
                prev_site = self.scene.site(prev[0])
                if prev_site is not None and prev_site.can_start:
                    problems.append(f"sortie starts at {sortie.start_site_id} but previous landed at {prev[0]}")
            if prev and sortie.t_start and sortie.t_start < prev[1]:
                problems.append("sortie starts before the previous sortie of the same UAV ends")
            if sortie.t_start and sortie.t_start < win_start:
                problems.append("take-off before the mission/daylight window")
            if sortie.t_end and sortie.t_end > win_end:
                problems.append("landing after the mission/daylight window")
            if problems:
                counters["site_feasibility_violations"] += len(
                    [p for p in problems if "window" not in p]
                )
                counters["window_violations"] += len([p for p in problems if "window" in p])
                for p in problems:
                    rep.violations.append(
                        Violation(
                            code="schedule_or_site_infeasible",
                            severity="hard",
                            message=f"sortie {sortie.id}: {p}",
                            detail={"sortie": sortie.id, "problem": p},
                        )
                    )
            rows.append(
                {
                    "sortie": sortie.id,
                    "uav": sortie.uav_id,
                    "start_site": sortie.start_site_id,
                    "landing_site": sortie.landing_site_id,
                    "different_start_end": sortie.start_site_id != sortie.landing_site_id,
                    "t_start": sortie.t_start.isoformat() if sortie.t_start else None,
                    "t_end": sortie.t_end.isoformat() if sortie.t_end else None,
                    "problems": problems,
                }
            )
            if sortie.t_end:
                per_uav_last[sortie.uav_id] = (sortie.landing_site_id, sortie.t_end)
        rep.checks["schedule"] = {
            "mission_window": [win_start.isoformat(), win_end.isoformat()],
            "daylight_window": (
                [mission.daylight_start.isoformat(), mission.daylight_end.isoformat()]
                if mission.daylight_start and mission.daylight_end
                else None
            ),
            "sorties": rows,
        }

    def _check_fixed_wing_maneuver(
        self, plan: Plan, rep: ValidationReport, counters: dict[str, int]
    ) -> None:
        details: list[dict[str, Any]] = []
        for sortie in plan.sorties:
            uav = self.scene.uav(sortie.uav_id)
            if uav is None or not uav.is_fixed_wing:
                continue
            radius = uav.turnaround_buffer_m
            if radius <= 0:
                continue
            for tid in sortie.task_ids:
                task = plan.tasks.get(tid)
                if task is None:
                    continue
                for t in task.transects:
                    for pt in (t.start, t.end):
                        zone = Point(pt).buffer(radius)
                        bad = None
                        if not self.hard_nfz.is_empty and zone.intersects(self.hard_nfz):
                            bad = "maneuver zone overlaps a hard NFZ"
                        elif not self.allowed.is_empty and not self.allowed.covers(zone):
                            bad = "maneuver zone leaves the allowed airspace"
                        if bad:
                            counters["fixed_wing_maneuver_violations"] += 1
                            details.append(
                                {
                                    "sortie": sortie.id,
                                    "uav": uav.id,
                                    "task": tid,
                                    "radius_m": radius,
                                    "problem": bad,
                                    "position": [round(pt[0], 1), round(pt[1], 1)],
                                }
                            )
                            rep.violations.append(
                                Violation(
                                    code="fixed_wing_maneuver_zone",
                                    severity="infeasible",
                                    message=f"{uav.id} task {tid}: {bad} (R={radius:.0f} m)",
                                    detail=details[-1],
                                )
                            )
                            break
        rep.checks["fixed_wing_maneuver"] = {
            "checked_uavs": [u.id for u in self.scene.fleet if u.is_fixed_wing],
            "violations": details[:20],
        }

    def _check_wind(self, plan: Plan, rep: ValidationReport, counters: dict[str, int]) -> None:
        wind = self.scene.mission.wind
        rows = []
        for uav_id in plan.used_uavs:
            uav = self.scene.uav(uav_id)
            if uav is None:
                continue
            ok = wind.speed_ms <= uav.max_wind_ms
            rows.append(
                {
                    "uav": uav_id,
                    "max_wind_ms": round(uav.max_wind_ms, 2),
                    "mission_wind_ms": wind.speed_ms,
                    "ok": ok,
                }
            )
            if not ok:
                counters["wind_violations"] += 1
                rep.violations.append(
                    Violation(
                        code="wind_limit_exceeded",
                        severity="hard",
                        message=(
                            f"{uav_id} is used at wind {wind.speed_ms} m/s above its operational "
                            f"limit {uav.max_wind_ms:.1f} m/s"
                        ),
                        detail=rows[-1],
                    )
                )
        excluded = [
            {"uav": u.id, "max_wind_ms": round(u.max_wind_ms, 2)}
            for u in self.scene.fleet
            if wind.speed_ms > u.max_wind_ms
        ]
        rep.checks["wind"] = {
            "wind_speed_ms": wind.speed_ms,
            "wind_direction_deg_from": wind.direction_deg_from,
            "used_uavs": rows,
            "excluded_by_wind": excluded,
            "energy_model_affected_by_wind": self.scene.mission.wind_energy_model_enabled,
        }


def validate_plan(scene: Scene, plan: Plan) -> ValidationReport:
    return SafetyValidator(scene).validate(plan)
