"""P2 planning hints — additive ``extensions.h1.*`` (no AtomicTask schema change)."""
from __future__ import annotations

import math
from typing import Any

from shapely.geometry import Point

from .feasibility import FeasibilityResult
from .geo import union_all
from .models import Scene
from .coverage.engine import CoverageResult


def build_planning_hints(
    scene: Scene,
    coverage: CoverageResult,
    feas: FeasibilityResult,
) -> tuple[dict[str, Any], list[str]]:
    """
    Returns (extensions_h1_dict, extra_warnings).

    S-04 energy / multi-sortie, S-06 candidate site, S-07 reserve, A-06 terrain meta.
    """
    h1: dict[str, Any] = {}
    warns: list[str] = []

    # --- S-04 multi-sortie energy hints (coarse, not a schedule) ---
    energy: list[dict[str, Any]] = []
    # Per-task (when a single AtomicTask already exceeds one sortie)
    for task in coverage.tasks.values():
        for uav in scene.fleet:
            if uav.ground_speed_ms <= 0:
                continue
            need_s = float(task.survey_length_m) / uav.ground_speed_ms
            usable = max(uav.usable_endurance_s, 1.0)
            sorties = int(math.ceil(need_s / usable))
            if sorties <= 1:
                continue
            energy.append(
                {
                    "scope": "task",
                    "task_id": task.id,
                    "uav_id": uav.id,
                    "survey_length_m": round(task.survey_length_m, 1),
                    "need_flight_s": round(need_s, 1),
                    "usable_endurance_s": round(usable, 1),
                    "sorties_hint": sorties,
                }
            )
            warns.append(
                f"S04: task {task.id} ≈{sorties} sorties on {uav.id} "
                f"(need {need_s / 60:.1f} min vs usable {usable / 60:.1f} min)"
            )
    # Mission / fleet totals (S04 customer case: many small tasks, one long day)
    for uav in scene.fleet:
        if uav.ground_speed_ms <= 0 or not coverage.tasks:
            continue
        total_m = sum(float(t.survey_length_m) for t in coverage.tasks.values())
        need_s = total_m / uav.ground_speed_ms
        usable = max(uav.usable_endurance_s, 1.0)
        sorties = int(math.ceil(need_s / usable))
        if sorties <= 1:
            continue
        energy.append(
            {
                "scope": "mission",
                "uav_id": uav.id,
                "survey_length_m": round(total_m, 1),
                "need_flight_s": round(need_s, 1),
                "usable_endurance_s": round(usable, 1),
                "sorties_hint": sorties,
            }
        )
        warns.append(
            f"S04: mission ≈{sorties} sorties on {uav.id} "
            f"(total survey {total_m / 1000:.1f} km → {need_s / 60:.0f} min vs "
            f"usable {usable / 60:.0f} min) — multi-sortie, not H1 schedule"
        )
    if energy:
        h1["energy_hints"] = energy

    # --- S-06 alternative / candidate site recommendation ---
    survey = union_all([j.effective_geom or j.geom for j in scene.jobs])
    centroid = survey.centroid if not survey.is_empty else Point(0, 0)
    primaries = [s for s in scene.sites if s.can_start and not s.candidate]
    candidates = [s for s in scene.sites if s.candidate]
    if candidates:
        best = min(candidates, key=lambda s: float(s.point.distance(centroid)))
        best_d = float(best.point.distance(centroid))
        primary = primaries[0] if primaries else None
        primary_d = float(primary.point.distance(centroid)) if primary else None
        rec = {
            "recommended_site_id": best.id,
            "distance_to_survey_m": round(best_d, 1),
            "reason": "closest_candidate_to_survey_centroid",
        }
        if primary is not None and primary_d is not None and best_d + 1.0 < primary_d:
            rec["replace_site_id"] = primary.id
            rec["replace_distance_m"] = round(primary_d, 1)
            warns.append(
                f"S06: prefer candidate site {best.id} "
                f"({best_d:.0f}m to survey) over {primary.id} ({primary_d:.0f}m)"
            )
        else:
            warns.append(
                f"S06: candidate site {best.id} available ({best_d:.0f}m to survey centroid)"
            )
        h1["site_recommendation"] = rec

    # --- S-07 reserve landing ---
    reserves = [s for s in scene.sites if s.role == "reserve"]
    landers = [s for s in scene.sites if s.can_land]
    h1["reserve_sites"] = [s.id for s in reserves]
    if not reserves:
        warns.append("S07: no reserve landing site in scene")
    else:
        # Mid-task far from every landing site → reserve gap hint
        for task in coverage.tasks.values():
            if not task.transects:
                continue
            mid = task.transects[len(task.transects) // 2].line().interpolate(0.5, normalized=True)
            nearest = min(float(s.point.distance(mid)) for s in landers) if landers else 1e9
            # Conservative: if mid farther than ~ usable range / 4 of fastest lander
            max_reach = 0.0
            for u in scene.fleet:
                max_reach = max(max_reach, u.ground_speed_ms * u.usable_endurance_s * 0.25)
            if max_reach > 0 and nearest > max_reach:
                warns.append(
                    f"S07: task {task.id} mid-point {nearest:.0f}m from nearest landing "
                    f"(>{max_reach:.0f}m conservative reach) — reserve coverage gap"
                )
                h1.setdefault("reserve_gaps", []).append(
                    {"task_id": task.id, "nearest_landing_m": round(nearest, 1)}
                )

    # --- A-06 terrain meta (no 3D spline) ---
    terr_warns = [w for w in coverage.warnings if w.startswith("M3:")]
    h1["terrain"] = {
        "dem_available": bool(scene.dem.available),
        "warning_count": len(terr_warns),
        "warnings": terr_warns[:20],
        "note": "planning checks only; terrain-following is autopilot/H3",
    }

    # Feasibility summary for energy-rejected pairs
    endurance_rejects = {
        k: v for k, v in feas.ineligible_reasons.items() if v == "task_exceeds_usable_endurance"
    }
    if endurance_rejects:
        h1["endurance_rejects"] = endurance_rejects

    return h1, warns
