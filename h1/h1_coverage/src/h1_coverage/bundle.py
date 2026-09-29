"""M13 BundleExporter — H1H2Bundle gmp.h1_h2.v2 (v1 remains readable)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import MIN_SAFE_AGL_M, SCHEMA_VERSION, CoverageConfig
from .coverage.engine import CoverageResult
from .feasibility import build_feasibility
from .models import AtomicTask, Scene, Transect, Uav
from .planning_hints import build_planning_hints


class BundleError(ValueError):
    pass


def _xy(p: tuple[float, float]) -> list[float]:
    return [float(p[0]), float(p[1])]


def _transect(tr: Transect) -> dict[str, Any]:
    return {"coords": [_xy(c) for c in tr.coords], "length_m": float(tr.length_m), "job_id": tr.job_id}


def _task(task: AtomicTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "job_id": task.job_id,
        "payload_class": task.payload_class,
        "payload_profile_id": task.payload_profile_id,
        "agl_m": float(max(task.agl_m, MIN_SAFE_AGL_M)),
        "transects": [_transect(t) for t in task.transects],
        "survey_length_m": float(task.survey_length_m),
        "turn_count": int(task.turn_count),
        "sweep_angle_deg": float(task.sweep_angle_deg),
        "entry": _xy(task.entry),
        "exit": _xy(task.exit),
        "geom_coords": [_xy(c) for c in task.geom_coords],
        "internal_transition_m": float(task.internal_transition_m),
        "fixed_wing_safe": bool(task.fixed_wing_safe),
        "notes": list(task.notes),
        "route_variants": task.route_variants,
    }


def _uav(u: Uav) -> dict[str, Any]:
    return {
        "id": u.id,
        "model": u.model,
        "uav_class": u.uav_class,
        "ground_speed_ms": float(u.ground_speed_ms),
        "operational_endurance_min": float(u.operational_endurance_min),
        "max_wind_ms": float(u.max_wind_ms),
        "payload_classes": list(u.payload_classes),
        "energy_reserve_fraction": float(u.energy_reserve_fraction),
        "horizontal_separation_m": float(u.horizontal_separation_m),
        "vertical_separation_m": float(u.vertical_separation_m),
        "turnaround_buffer_m": float(u.turnaround_buffer_m),
        "start_site": u.start_site,
        "landing_site": u.landing_site,
        "takeoff_time_s": float(u.takeoff_time_s),
        "landing_time_s": float(u.landing_time_s),
        "service_time_s": float(u.service_time_s),
        "cruise_agl_m": float(max(u.cruise_agl_m, MIN_SAFE_AGL_M)),
    }


def validate_bundle(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") not in ("gmp.h1_h2.v1", SCHEMA_VERSION, "gmp.h1_h2.v3"):
        errors.append("G7 schema_version")
    tasks = data.get("tasks") or []
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        errors.append("G6 duplicate ids")
    feas = (data.get("feasibility") or {}).get("eligible_uav_ids_by_task") or {}
    fleet = {u["id"]: u for u in data.get("fleet") or []}
    for t in tasks:
        tid = t["id"]
        if data.get("schema_version") in (SCHEMA_VERSION, "gmp.h1_h2.v3"):
            try:
                from .route_variants import build_route_variants
                from types import SimpleNamespace
                variants = t["route_variants"]
                trs_model = [SimpleNamespace(coords=tr["coords"]) for tr in t["transects"]]
                expected = build_route_variants(trs_model, t["fixed_wing_safe"],
                                                variants[-1]["fixed_wing_safe"])
                if variants != expected:
                    errors.append(f"G2 {tid} route_variants")
            except (KeyError, IndexError, TypeError):
                errors.append(f"G2 {tid} route_variants")
        trs = t.get("transects") or []
        if not trs:
            errors.append(f"G3 {tid} no transects")
            continue
        if t["entry"] != trs[0]["coords"][0] or t["exit"] != trs[-1]["coords"][-1]:
            errors.append(f"G2 {tid} entry/exit")
        if float(t["survey_length_m"]) <= 0:
            errors.append(f"G3 {tid} length")
        if float(t["agl_m"]) < MIN_SAFE_AGL_M:
            errors.append(f"G4 {tid} agl")
        if tid not in feas:
            errors.append(f"feasibility missing {tid}")
        elif not t.get("fixed_wing_safe", True):
            for uid in feas[tid]:
                if fleet.get(uid, {}).get("uav_class") == "fixed_wing":
                    errors.append(f"G5 {tid} FW eligible")
    return errors


@dataclass
class H1H2Bundle:
    data: dict[str, Any]

    def write_json(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        errs = validate_bundle(self.data)
        if errs:
            raise BundleError("; ".join(errs))
        path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path


def export_bundle(
    scene: Scene,
    coverage: CoverageResult,
    *,
    source: str = "coverage",
    cfg: CoverageConfig | None = None,
) -> H1H2Bundle:
    cfg = cfg or CoverageConfig()
    if source == "coverage" and scene.mission.require_complete_coverage:
        below = coverage.coverage_percent + 1e-6 < cfg.coverage_pass_percent
        if below and not coverage.tasks:
            raise BundleError("Gate B: empty coverage under require_complete_coverage")
        if below and cfg.strict_coverage:
            raise BundleError(
                f"Gate B: coverage_percent={coverage.coverage_percent:.2f} "
                f"< required {cfg.coverage_pass_percent} (strict)"
            )

    feas = build_feasibility(scene, coverage.tasks)
    start = next((s.id for s in scene.sites if s.can_start), None)
    land = next((s.id for s in scene.sites if s.can_land), start)
    fleet = []
    for u in scene.fleet:
        w = _uav(u)
        w["start_site"] = w["start_site"] or start
        w["landing_site"] = w["landing_site"] or land
        fleet.append(w)

    warnings = list(coverage.warnings)
    if not scene.dem.available:
        warnings.append("DEM unavailable or flat fallback")

    ext_h1, hint_warns = build_planning_hints(scene, coverage, feas)
    warnings.extend(hint_warns)

    data = {
        "schema_version": SCHEMA_VERSION,
        "scene_id": scene.id,
        "crs": {
            "metric_epsg": scene.crs.epsg_int,
            "axis": "ENU_metres",
            "note": "Coordinates in metres, not lon/lat",
        },
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "fleet": fleet,
        "sites": [
            {"id": s.id, "x": float(s.point.x), "y": float(s.point.y), "role": s.role, "candidate": s.candidate}
            for s in scene.sites
        ],
        "mission": {
            "objectives": [o for o in scene.mission.objectives if o in ("makespan", "total_flight")]
            or ["makespan", "total_flight"],
            "window_start": scene.mission.window_start.isoformat() if scene.mission.window_start else None,
            "window_end": scene.mission.window_end.isoformat() if scene.mission.window_end else None,
            "wind": {
                "speed_ms": float(scene.mission.wind.speed_ms),
                "direction_deg_from": float(scene.mission.wind.direction_deg_from),
            },
            "allow_different_start_end": scene.mission.allow_different_start_end,
            "require_complete_coverage": scene.mission.require_complete_coverage,
            "require_schedule": scene.mission.require_schedule,
        },
        "tasks": [_task(t) for t in sorted(coverage.tasks.values(), key=lambda x: x.id)],
        "feasibility": feas.as_contract(),
        "coverage_meta": {
            "coverage_percent": round(coverage.coverage_percent, 4),
            "task_count": len(coverage.tasks),
            "per_job": coverage.per_job,
            "sweep_candidates": coverage.candidates,
        },
        "extensions": {"h1": ext_h1},
        "warnings": warnings,
    }
    errs = validate_bundle(data)
    if errs:
        raise BundleError("; ".join(errs))
    return H1H2Bundle(data=data)
