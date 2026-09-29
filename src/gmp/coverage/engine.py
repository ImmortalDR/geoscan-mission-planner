"""H1 coverage bridge — canonical implementation lives in ``h1_coverage``.

This package must not re-implement lawnmower / NFZ / feasibility. Planner and
API call ``build_coverage`` here; we delegate to ``h1/h1_coverage``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shapely.geometry import LineString

from ..models import AtomicTask, Scene, Transect


@dataclass
class CoverageResult:
    tasks: dict[str, AtomicTask]
    per_job: list[dict[str, Any]] = field(default_factory=list)
    exclusions: dict[str, Any] = field(default_factory=dict)
    payloads: list[dict[str, Any]] = field(default_factory=list)
    candidates: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    coverage_percent: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "coverage_percent": round(self.coverage_percent, 4),
            "task_count": len(self.tasks),
            "total_survey_length_m": round(sum(t.survey_length_m for t in self.tasks.values()), 1),
            "per_job": self.per_job,
            "exclusions": self.exclusions,
            "payload_geometry": self.payloads,
            "sweep_candidates": self.candidates,
            "warnings": self.warnings,
            "h1_backend": "h1_coverage",
        }


def _convert_task(t: Any) -> AtomicTask:
    trs = [
        Transect(coords=list(tr.coords), length_m=float(tr.length_m), job_id=tr.job_id)
        for tr in t.transects
    ]
    geom = LineString(t.geom_coords) if getattr(t, "geom_coords", None) else LineString(trs[0].coords)
    return AtomicTask(
        id=t.id,
        job_id=t.job_id,
        payload_class=t.payload_class,
        payload_profile_id=t.payload_profile_id,
        agl_m=float(t.agl_m),
        transects=trs,
        survey_length_m=float(t.survey_length_m),
        turn_count=int(t.turn_count),
        sweep_angle_deg=float(t.sweep_angle_deg),
        entry=tuple(t.entry),
        exit=tuple(t.exit),
        geom=geom,
        internal_transition_m=float(t.internal_transition_m),
        fixed_wing_safe=bool(t.fixed_wing_safe),
        notes=list(t.notes or []),
        route_variants=t.route_variants,
    )


def build_coverage(
    scene: Scene,
    step_deg: float = 15.0,
    keep_candidates: int = 4,
    crosswind_weight: float = 1.0,
) -> CoverageResult:
    """Run canonical H1 (`h1_coverage`) and map tasks into gmp models."""
    if not scene.source_dir:
        raise RuntimeError(
            "H1 coverage was moved to package h1_coverage; "
            "Scene.source_dir is required so the bridge can reload the scene. "
            "Or pass an explicit CoverageResult into plan_mission()."
        )
    try:
        from h1_coverage.config import CoverageConfig
        from h1_coverage.pipeline import run_h1
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Install canonical H1: pip install -e ../h1/h1_coverage"
        ) from exc

    cfg = CoverageConfig(
        angle_step_deg=step_deg,
        keep_candidates=keep_candidates,
        crosswind_weight=crosswind_weight,
        strict_coverage=False,
    )
    result = run_h1(scene.source_dir, cfg)
    # Copy effective geoms / warnings onto the caller's scene for downstream H2
    by_id = {j.id: j for j in result.scene.jobs}
    for job in scene.jobs:
        src = by_id.get(job.id)
        if src is not None and src.effective_geom is not None:
            job.effective_geom = src.effective_geom
    tasks = {tid: _convert_task(t) for tid, t in result.coverage.tasks.items()}
    return CoverageResult(
        tasks=tasks,
        per_job=list(result.coverage.per_job),
        exclusions=dict(result.coverage.exclusions or {}),
        candidates=dict(result.coverage.candidates or {}),
        coverage_percent=float(result.coverage.coverage_percent),
        warnings=list(result.coverage.warnings) + ["H1 via h1_coverage bridge (no local gmp.coverage algo)"],
    )


def coverage_half_width_m(payload) -> float:
    """Swath half-width for H3 safety checks (uses payload spacing/footprint)."""
    across = getattr(payload, "footprint_across_m", None) or getattr(payload, "swath_spacing_m", 40.0)
    spacing = getattr(payload, "swath_spacing_m", None) or across
    return max(float(across) / 2.0, float(spacing) / 2.0) * 1.06


def coverage_geometry(scene: Scene) -> dict[str, Any]:
    """Compatibility stub — full geometry comes from h1_coverage meta."""
    return {"note": "use build_coverage().as_dict()", "h1_backend": "h1_coverage"}


# Legacy names imported by old tests — keep importable no-ops that redirect.
def _eligible_uavs(scene: Scene, payload_class: str) -> list:
    wind = scene.mission.wind.speed_ms
    return [u for u in scene.fleet if u.supports(payload_class) and wind <= u.max_wind_ms]
