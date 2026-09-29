"""M10 Coverage candidates — multi-angle ranking with wind."""
from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import groupby

from shapely.geometry.base import BaseGeometry

from ..config import MANEUVER_VIOLATION_PENALTY, CoverageConfig
from ..geo import angular_difference
from ..models import Transect
from .backend import get_sweep_backend
from .fixed_wing import maneuver_violations


@dataclass
class SweepCandidate:
    angle_deg: float
    transects: list[Transect]
    survey_length_m: float
    transition_length_m: float
    turn_count: int
    crosswind_factor: float
    maneuver_violations: int
    score: float
    label: str
    base_proximity_score: float = 0.0


# Primary cost is in equivalent metres. Base attraction only breaks numerical
# ties with equal transect counts; it must not pay for longer flights or a real
# crosswind disadvantage, or reorder candidates with different counts.
SCORE_REL_TOL = 1e-9
SCORE_ABS_TOL_M = 1e-6
PROXIMITY_EPSILON_M = 1.0


def endpoint_base_proximity(transects, weighted_bases):
    """Sum w_b / (distance(endpoint, base)^2 + 1 m^2); larger is better."""
    return math.fsum(weight / (math.dist(endpoint, xy)**2 + PROXIMITY_EPSILON_M**2)
                     for tr in transects for endpoint in (tr.start, tr.end)
                     for _, xy, weight in weighted_bases)


def rank_candidates(candidates):
    """Break cost ties only within consecutive runs of equal transect counts.

    Anchoring cost groups at their minimum avoids non-transitive isclose sorts.
    Keeping count runs in place preserves the primary ordering between every
    pair of candidates with different counts, including in multi-angle searches.
    """
    ordered = sorted(candidates, key=lambda c: (c.score, c.angle_deg, c.label))
    result = []
    while ordered:
        anchor = ordered[0].score
        end = 1
        while end < len(ordered) and math.isclose(ordered[end].score, anchor,
                                                rel_tol=SCORE_REL_TOL, abs_tol=SCORE_ABS_TOL_M):
            end += 1
        for _, same_count in groupby(ordered[:end], key=lambda c: len(c.transects)):
            result.extend(sorted(same_count, key=lambda c: (
                -c.base_proximity_score, c.score, c.angle_deg, c.label)))
        ordered = ordered[end:]
    return result


def wind_axis_angle(direction_deg_from: float) -> float:
    return (90.0 - direction_deg_from) % 180.0


def transition_length(transects: list[Transect]) -> float:
    import math as m

    return sum(m.dist(transects[i].end, transects[i + 1].start) for i in range(len(transects) - 1))


def build_candidates(
    area: BaseGeometry,
    spacing: float,
    job_id: str,
    wind_dir_from: float,
    wind_speed_ms: float,
    turn_cost_m: float,
    turn_radius_m: float,
    hard_nfz: BaseGeometry,
    allowed: BaseGeometry,
    cfg: CoverageConfig | None = None,
    overshoot_m: float = 12.0,
    preferred_angles: list[tuple[float, str]] | None = None,
    limit_candidates: bool = True,
    weighted_bases: list[tuple[str, tuple[float, float], int]] | None = None,
) -> list[SweepCandidate]:
    cfg = cfg or CoverageConfig()
    angles: list[tuple[float, str]] = list(preferred_angles or [])
    angles.extend(
        [
            (wind_axis_angle(wind_dir_from), "wind_aligned"),
            ((wind_axis_angle(wind_dir_from) + 90.0) % 180.0, "wind_perpendicular"),
        ]
    )
    a = 0.0
    while a < 180.0:
        angles.append((a, f"grid_{int(a)}"))
        a += cfg.angle_step_deg
    # preferred labels win on collision
    seen: dict[int, tuple[float, str]] = {}
    for ang, label in angles:
        key = int(round(ang * 2))
        if key not in seen or label.startswith("corridor"):
            seen[key] = (ang, label)

    out: list[SweepCandidate] = []
    for ang, label in seen.values():
        trs = get_sweep_backend().generate(area, spacing, ang, job_id, overshoot_m=overshoot_m)
        if not trs:
            continue
        survey = sum(t.length_m for t in trs)
        trans = transition_length(trs)
        turns = max(len(trs) - 1, 0)
        wind_axis = wind_axis_angle(wind_dir_from)
        cross = math.sin(math.radians(angular_difference(ang, wind_axis)))
        viol = maneuver_violations(trs, turn_radius_m, hard_nfz, allowed)
        score = (
            survey
            + trans
            + turns * turn_cost_m
            + cfg.crosswind_weight * cross * wind_speed_ms * survey / 100.0
            + viol * MANEUVER_VIOLATION_PENALTY
        )
        out.append(
            SweepCandidate(
                angle_deg=ang,
                transects=trs,
                survey_length_m=survey,
                transition_length_m=trans,
                turn_count=turns,
                crosswind_factor=cross,
                maneuver_violations=viol,
                score=score,
                label=label,
                base_proximity_score=endpoint_base_proximity(trs, weighted_bases or []),
            )
        )
    out = rank_candidates(out)
    return out[: cfg.keep_candidates] if limit_candidates else out
