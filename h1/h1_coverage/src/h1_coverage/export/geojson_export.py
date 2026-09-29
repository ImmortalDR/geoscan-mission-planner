"""Export helpers — GeoJSON transects / sites (W-01)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from shapely.geometry import LineString, mapping

from ..coverage.engine import CoverageResult
from ..io.geojson import write_feature_collection
from ..models import Scene


def export_transects_geojson(scene: Scene, coverage: CoverageResult, path: str | Path) -> Path:
    feats: list[dict[str, Any]] = []
    for task in coverage.tasks.values():
        for i, tr in enumerate(task.transects):
            feats.append(
                {
                    "type": "Feature",
                    "geometry": mapping(LineString(tr.coords)),
                    "properties": {
                        "task_id": task.id,
                        "job_id": task.job_id,
                        "transect_i": i,
                        "length_m": tr.length_m,
                        "agl_m": task.agl_m,
                        "sweep_angle_deg": task.sweep_angle_deg,
                        "fixed_wing_safe": task.fixed_wing_safe,
                        "payload_class": task.payload_class,
                    },
                }
            )
    for s in scene.sites:
        feats.append(
            {
                "type": "Feature",
                "geometry": mapping(s.point),
                "properties": {"site_id": s.id, "role": s.role, "kind": "site"},
            }
        )
    for z in scene.no_fly_zones:
        feats.append(
            {
                "type": "Feature",
                "geometry": mapping(z.geom),
                "properties": {"zone_id": z.id, "kind": "nfz", "hard": z.hard},
            }
        )
    for job in scene.jobs:
        g = job.effective_geom or job.geom
        feats.append(
            {
                "type": "Feature",
                "geometry": mapping(g),
                "properties": {"job_id": job.id, "kind": "survey_effective"},
            }
        )
    path = Path(path)
    write_feature_collection(path, feats)
    return path
