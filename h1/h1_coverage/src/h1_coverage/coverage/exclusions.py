"""M8 NFZManager — contract h1.m8.nfz.v1 (guarantee G1).

Hard NFZ: buffer + difference from survey → effective area.
Holes in survey geom = «don't survey»; NFZ = «don't fly» — kept separate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from ..config import CoverageConfig
from ..geo import make_valid, union_all
from ..models import PayloadProfile, Scene, SurveyJob, Transect


@dataclass
class ExclusionResult:
    job_id: str
    effective: BaseGeometry
    reasons: list[dict[str, Any]] = field(default_factory=list)


def compute_effective_area(
    scene: Scene,
    job: SurveyJob,
    payload: PayloadProfile,
    cfg: CoverageConfig | None = None,
) -> ExclusionResult:
    cfg = cfg or CoverageConfig()
    geom = make_valid(job.geom)
    reasons: list[dict[str, Any]] = []

    allowed = union_all([z.geom for z in scene.allowed_airspace])
    if not allowed.is_empty:
        inset = allowed.buffer(-cfg.allowed_inset_m) if cfg.allowed_inset_m > 0 else allowed
        if inset.is_empty:
            inset = allowed
        clipped = make_valid(geom.intersection(inset))
        lost = geom.area - clipped.area
        if lost > 1.0:
            reasons.append({"reason": "outside_allowed_airspace", "area_m2": round(lost, 1)})
        geom = clipped

    hard = [z for z in scene.no_fly_zones if z.hard]
    if hard:
        nfz_u = union_all([z.geom.buffer(cfg.nfz_buffer_m) for z in hard])
        clipped = make_valid(geom.difference(nfz_u))
        lost = geom.area - clipped.area
        if lost > 1.0:
            reasons.append(
                {
                    "reason": "hard_no_fly_zone",
                    "area_m2": round(lost, 1),
                    "buffer_m": cfg.nfz_buffer_m,
                }
            )
        geom = clipped

    soft = [z for z in scene.no_fly_zones if not z.hard] + list(scene.obstacles)
    if soft:
        soft_u = union_all([z.geom.buffer(cfg.nfz_buffer_m * 0.5) for z in soft])
        clipped = make_valid(geom.difference(soft_u))
        lost = geom.area - clipped.area
        if lost > 1.0:
            reasons.append(
                {
                    "reason": "soft_exclusion_obstacle",
                    "area_m2": round(lost, 1),
                    "zones": len(soft),
                }
            )
        geom = clipped

    if scene.temporal_airspace:
        # Geometry kept for H2/H3; H1 only notes presence (no schedule).
        reasons.append(
            {
                "reason": "temporal_airspace_present",
                "zones": len(scene.temporal_airspace),
                "note": "not applied to effective area in H1 MVP",
            }
        )

    return ExclusionResult(job_id=job.id, effective=geom, reasons=reasons)


def assert_g1_transects_clear(transects: list[Transect], hard_nfz: BaseGeometry) -> None:
    """G1: survey lines must not intersect hard NFZ core."""
    if hard_nfz.is_empty:
        return
    for t in transects:
        if LineString(t.coords).intersects(hard_nfz):
            raise ValueError(f"G1 violated: transect intersects hard NFZ (job={t.job_id})")


def apply_exclusions(scene: Scene, cfg: CoverageConfig | None = None) -> dict[str, Any]:
    cfg = cfg or CoverageConfig()
    report: dict[str, Any] = {"jobs": []}
    for job in scene.jobs:
        payload = scene.payloads.get(job.payload_profile_id)
        if payload is None:
            payload = PayloadProfile(id="tmp", type=job.survey_type, nominal_agl_m=120)
        er = compute_effective_area(scene, job, payload, cfg)
        job.effective_geom = er.effective
        report["jobs"].append(
            {
                "job_id": job.id,
                "effective_area_m2": round(er.effective.area, 1),
                "reasons": er.reasons,
            }
        )
    return report
