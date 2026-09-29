"""Inspect / validate a scene directory before running coverage."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .manifest import SCENE_INPUTS, SURVEY_GEOMETRY_KINDS
from .scene import LAYER, SceneError, load_scene


def inspect_scene_dir(directory: str | Path, *, try_load: bool = True) -> dict[str, Any]:
    """
    Report which H1 inputs are present and whether ``load_scene`` succeeds.

    Does not invent missing files — only describes what the loader will use.
    """
    d = Path(directory)
    report: dict[str, Any] = {
        "path": str(d.resolve()) if d.exists() else str(d),
        "is_dir": d.is_dir(),
        "files": {},
        "gaps": [],
        "survey_geometry_kinds": list(SURVEY_GEOMETRY_KINDS),
        "load_ok": None,
        "scene_id": None,
        "warnings": [],
        "summary": {},
    }
    if not d.is_dir():
        report["gaps"].append("not a directory")
        report["load_ok"] = False
        return report

    for spec in SCENE_INPUTS:
        path = d / spec.filename
        report["files"][spec.key] = {
            "filename": spec.filename,
            "present": path.is_file(),
            "presence": spec.presence,
            "role": spec.role,
            "notes": spec.notes,
            "bytes": path.stat().st_size if path.is_file() else 0,
        }

    survey_gj = report["files"]["survey_areas"]["present"]
    survey_kml = report["files"]["scene_kml"]["present"]
    sites_gj = report["files"]["landing_sites"]["present"]
    if not survey_gj and not survey_kml:
        report["gaps"].append("need survey_areas.geojson or scene.kml (survey)")
    if not sites_gj and not survey_kml:
        report["gaps"].append("need landing_sites.geojson or scene.kml (sites)")

    if try_load and not report["gaps"]:
        try:
            scene = load_scene(d)
            report["load_ok"] = True
            report["scene_id"] = scene.id
            report["warnings"] = list(scene.warnings)
            report["summary"] = {
                "jobs": len(scene.jobs),
                "job_modes": sorted({j.mode for j in scene.jobs}),
                "sites": len(scene.sites),
                "fleet": len(scene.fleet),
                "payloads": len(scene.payloads),
                "nfz": len(scene.no_fly_zones),
                "allowed_airspace": len(scene.allowed_airspace),
                "obstacles": len(scene.obstacles),
                "temporal_airspace": len(scene.temporal_airspace),
                "dem": scene.dem.available,
                "metric_epsg": scene.crs.metric_epsg,
            }
        except SceneError as exc:
            report["load_ok"] = False
            report["gaps"].append(str(exc))
        except Exception as exc:  # pragma: no cover - unexpected
            report["load_ok"] = False
            report["gaps"].append(f"{type(exc).__name__}: {exc}")
    elif report["gaps"]:
        report["load_ok"] = False

    report["layer_map"] = dict(LAYER)
    return report
