"""Coverage engine — orchestrates M5–M11."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

from shapely.geometry import LineString

from ..config import MANEUVER_VIOLATION_PENALTY, CoverageConfig
from ..feasibility import eligible_uavs_for_payload
from ..geo import union_all
from ..io.dem import terrain_warnings_for_tasks
from ..models import AtomicTask, Scene, Transect
from .atomic import build_atomic_tasks, chunk_transects
from .candidates import build_candidates, transition_length, endpoint_base_proximity
from .corridor import is_corridor_like, preferred_corridor_angles
from .exclusions import apply_exclusions, assert_g1_transects_clear
from .fixed_wing import maneuver_violations
from .survey import derive_all
from .complete import physical_coverage, complete_parallel_sweep


@dataclass
class CoverageResult:
    tasks: dict[str, AtomicTask]
    coverage_percent: float
    per_job: list[dict[str, Any]] = field(default_factory=list)
    candidates: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    exclusions: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _half_width(spacing: float, footprint_across: float | None) -> float:
    across = footprint_across or spacing
    return max(across / 2.0, spacing / 2.0)


def weighted_start_bases(scene: Scene, eligible) -> list[tuple[str, tuple[float, float], int]]:
    """One vote per compatible aircraft per permitted initial launch site.

    A pinned aircraft votes only for its base; an unpinned aircraft can vote at
    each active launch site. No aircraft is assigned to a job by this heuristic.
    """
    bases = []
    for site in sorted(scene.sites, key=lambda s: s.id):
        if site.candidate or not site.can_start:
            continue
        weight = sum(u.start_site is None or u.start_site == site.id for u in eligible)
        if weight:
            bases.append((site.id, (float(site.point.x), float(site.point.y)), weight))
    return bases


def build_coverage(scene: Scene, cfg: CoverageConfig | None = None) -> CoverageResult:
    cfg = cfg or CoverageConfig()
    warnings: list[str] = list(scene.warnings)
    warnings.extend(derive_all(scene.payloads))
    exclusions = apply_exclusions(scene, cfg)

    hard_nfz = union_all([z.geom for z in scene.no_fly_zones if z.hard])
    allowed = union_all([z.geom for z in scene.allowed_airspace])

    tasks: dict[str, AtomicTask] = {}
    per_job: list[dict[str, Any]] = []
    cand_report: dict[str, list[dict[str, Any]]] = {}
    covered_total = 0.0
    target_total = 0.0

    for job in scene.jobs:
        payload = scene.payloads.get(job.payload_profile_id)
        if payload is None:
            warnings.append(f"job {job.id}: missing payload profile, skipped")
            continue
        area = job.effective_geom if job.effective_geom is not None else job.geom
        if area.is_empty:
            warnings.append(f"job {job.id}: empty effective area")
            continue

        eligible = eligible_uavs_for_payload(scene, job.survey_type)
        weighted_bases = weighted_start_bases(scene, eligible)
        turn_radius = max((u.turnaround_buffer_m for u in eligible if u.is_fixed_wing), default=0.0)
        # Use FW turn radius for scoring only when fleet for this job is FW-only
        fw_only = bool(eligible) and all(u.is_fixed_wing for u in eligible)
        tr_use = turn_radius if fw_only else 0.0
        turn_cost = (math.pi * max(turn_radius, 1.0) / 2.0) if any(u.is_fixed_wing for u in eligible) else 40.0

        half_w = _half_width(payload.swath_spacing_m, payload.footprint_across_m)
        # DIY headland: slight inset so lines don't hug the boundary (F2C-like)
        plan_area = area
        if cfg.headland_m > 0 and area.area > 1.0:
            inset = area.buffer(-cfg.headland_m)
            if not inset.is_empty and inset.area > 1.0:
                plan_area = inset
        pref = None
        if getattr(job, "mode", None) == "corridor" or is_corridor_like(plan_area):
            pref = preferred_corridor_angles(plan_area)
            warnings.append(f"job {job.id}: corridor mode — prefer along/across angles")
        cands = build_candidates(
            area=plan_area,
            spacing=payload.swath_spacing_m,
            job_id=job.id,
            wind_dir_from=scene.mission.wind.direction_deg_from,
            wind_speed_ms=scene.mission.wind.speed_ms,
            turn_cost_m=turn_cost,
            turn_radius_m=tr_use,
            hard_nfz=hard_nfz,
            allowed=allowed,
            cfg=cfg,
            overshoot_m=min(max(half_w * 0.4, 8.0), 25.0),
            preferred_angles=pref,
            limit_candidates=False,
            weighted_bases=weighted_bases,
        )
        if not cands:
            warnings.append(f"job {job.id}: no sweep candidates")
            continue
        zero = [c for c in cands if c.maneuver_violations == 0]
        ranked = zero or cands
        best = ranked[0]
        covered = -1.0
        # Cost ranking alone rewards missing strips. Prefer the first complete
        # candidate, or report the best attainable coverage without claiming it complete.
        physical_complete = scene.mission.require_complete_coverage and cfg.coverage_pass_percent >= 100.
        for candidate in ranked:
            swath = physical_coverage(candidate.transects,payload) if physical_complete else union_all([
                LineString(t.coords).buffer(half_w, cap_style=1)
                for t in candidate.transects
            ])
            candidate_covered = area.intersection(swath).area
            if candidate_covered > covered:
                best, covered = candidate, candidate_covered
            if 100.0 * candidate_covered / area.area + 1e-6 >= cfg.coverage_pass_percent:
                best, covered = candidate, candidate_covered
                break
        if physical_complete and area.area-covered>1e-3:
            flight_space=allowed.buffer(-cfg.allowed_inset_m) if not allowed.is_empty else area.envelope.buffer(payload.swath_spacing_m)
            if not hard_nfz.is_empty:flight_space=flight_space.difference(hard_nfz.buffer(cfg.nfz_buffer_m))
            soft=union_all([z.geom for z in scene.no_fly_zones if not z.hard]+[z.geom for z in scene.obstacles])
            if not soft.is_empty:flight_space=flight_space.difference(soft.buffer(cfg.nfz_buffer_m*.5))
            completed=complete_parallel_sweep(area,payload.swath_spacing_m,best.angle_deg,job.id,flight_space)
            completed_area=area.intersection(physical_coverage(completed,payload)).area
            if completed_area>covered:
                survey = sum(t.length_m for t in completed)
                transitions = transition_length(completed)
                turns = max(0, len(completed)-1)
                violations = maneuver_violations(completed, tr_use, hard_nfz, allowed)
                score = (survey + transitions + turns * turn_cost
                         + cfg.crosswind_weight * best.crosswind_factor * scene.mission.wind.speed_ms * survey / 100.0
                         + violations * MANEUVER_VIOLATION_PENALTY)
                best = replace(best, transects=completed, survey_length_m=survey,
                               transition_length_m=transitions, turn_count=turns,
                               maneuver_violations=violations, score=score,
                               base_proximity_score=endpoint_base_proximity(completed, weighted_bases),
                               label=best.label+'_physical_complete')
                covered=completed_area
                warnings.append(f'job {job.id}: boundary-complete physical survey strips inside allowed airspace')
        report_candidates = cands[: max(1, cfg.keep_candidates)]
        if not any(candidate is best for candidate in report_candidates):
            report_candidates[-1] = best
        cand_report[job.id] = [
            {
                "label": c.label,
                "angle_deg": round(c.angle_deg, 1),
                "survey_length_m": round(c.survey_length_m, 1),
                "turn_count": c.turn_count,
                "score": round(c.score, 1),
                "maneuver_violations": c.maneuver_violations,
                "selected": c is best,
                "base_proximity_score": c.base_proximity_score,
            }
            for c in report_candidates
        ]

        if eligible:
            caps = [u.usable_endurance_s * u.ground_speed_ms * 0.85 for u in eligible]
            min_cap = min(caps)
        else:
            min_cap = best.survey_length_m
            warnings.append(f"job {job.id}: no eligible UAV for payload {job.survey_type}")

        chunk_len = max(min_cap * 0.30, best.survey_length_m / max(cfg.max_tasks_per_job, 1), 300.0)
        # Keep hole / inter-cell hops out of geom (H2 flies geom as continuous survey).
        max_hop_m = max(2.5 * payload.swath_spacing_m, 100.0)
        chunks = chunk_transects(
            best.transects,
            chunk_len,
            cfg.max_tasks_per_job,
            max_hop_m=max_hop_m,
        )
        forced = getattr(chunk_transects, "last_forced_long_joins", 0)
        if forced:
            warnings.append(
                f"job {job.id}: forced {forced} long-hop merge(s) to respect max_tasks_per_job"
            )
        # per-chunk FW safety using full turn radius if any FW in fleet
        flags: list[bool] = []
        r_check = max((u.turnaround_buffer_m for u in scene.fleet if u.is_fixed_wing), default=0.0)
        for ch in chunks:
            viol = maneuver_violations(ch, r_check, hard_nfz, allowed) if r_check > 0 else 0
            flags.append(viol == 0)

        built = build_atomic_tasks(
            chunks,
            job_id=job.id,
            payload_class=job.survey_type,
            payload_profile_id=payload.id,
            agl_m=payload.agl_m,
            sweep_angle_deg=best.angle_deg,
            fixed_wing_safe_flags=flags,
        )
        for t in built:
            alternate = [Transect(coords=list(reversed(tr.coords)), length_m=tr.length_m,
                                  job_id=tr.job_id) for tr in t.transects]
            t.alternate_fixed_wing_safe = (
                maneuver_violations(alternate, r_check, hard_nfz, allowed) == 0
                if r_check > 0 else True
            )
            tasks[t.id] = t

        # G1 belt: lines from effective sweep must not hit hard NFZ core
        assert_g1_transects_clear(best.transects, hard_nfz)

        covered_total += covered
        target_total += area.area
        per_job.append(
            {
                "job_id": job.id,
                "task_count": len(built),
                "sweep_angle_deg": round(best.angle_deg, 1),
                "coverage_percent": round(100.0 * covered / max(area.area, 1e-9), 3),
                "eligible_uavs": [u.id for u in eligible],
                "base_weights": {sid: weight for sid, _, weight in weighted_bases},
            }
        )

    pct = 100.0 * covered_total / target_total if target_total > 0 else 0.0
    # T-01 Gate B: always warn when below pass threshold (never silent under-cover)
    if pct + 1e-6 < cfg.coverage_pass_percent:
        warnings.append(
            f"Gate B: coverage_percent={pct:.2f} < required {cfg.coverage_pass_percent}"
        )

    result = CoverageResult(
        tasks=tasks,
        coverage_percent=pct,
        per_job=per_job,
        candidates=cand_report,
        exclusions=exclusions,
        warnings=warnings,
    )
    result.warnings.extend(terrain_warnings_for_tasks(scene, tasks))
    if cfg.terrain_strict and any("LOCAL_AGL_BELOW_MIN" in w for w in result.warnings):
        result.warnings.append("M3: terrain_strict — local AGL below minimum (INFEASIBLE for planning)")
    return result
