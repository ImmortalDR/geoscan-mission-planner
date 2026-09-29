"""M3 TerrainModel — contract h1.m3.terrain.v1

Sample ground elevation in metric CRS. Missing DEM → flat=0 + warning (do not block smoke).
A-05: local AGL under constant geometric altitude (ref + planned AGL).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import rasterio
from pyproj import Transformer

from ..config import MIN_SAFE_AGL_M
from ..geo import CrsPipeline


@dataclass
class TerrainStats:
    min_m: float
    max_m: float
    mean_m: float


class DemSampler:
    def __init__(self, path: str, pipeline: CrsPipeline):
        self.path = path
        self.pipeline = pipeline
        with rasterio.open(path) as ds:
            self.band = ds.read(1).astype("float32")
            self.transform = ds.transform
            self.crs = ds.crs
            self.nodata = ds.nodata
            self.width = ds.width
            self.height = ds.height
        self._same = self.crs is not None and self.crs.to_string() == pipeline.metric_epsg
        self._tf = (
            None
            if self._same
            else Transformer.from_crs(pipeline.metric_epsg, self.crs.to_string(), always_xy=True)
        )
        valid = self.band[np.isfinite(self.band)]
        if self.nodata is not None:
            valid = valid[valid != self.nodata]
        if valid.size:
            self.mean_elevation_m = float(valid.mean())
            self.stats = TerrainStats(float(valid.min()), float(valid.max()), float(valid.mean()))
        else:
            self.mean_elevation_m = 0.0
            self.stats = TerrainStats(0.0, 0.0, 0.0)

    def elevation(self, x: float, y: float) -> float:
        rx, ry = (x, y) if self._tf is None else self._tf.transform(x, y)
        col, row = ~self.transform @ (rx, ry)
        c = min(max(int(round(col)), 0), self.width - 1)
        r = min(max(int(round(row)), 0), self.height - 1)
        v = float(self.band[r, c])
        if self.nodata is not None and v == self.nodata:
            return self.mean_elevation_m
        if not math.isfinite(v):
            return self.mean_elevation_m
        return v


def load_dem(path: str, pipeline: CrsPipeline) -> DemSampler | None:
    try:
        return DemSampler(path, pipeline)
    except Exception:
        return None


def _reference_ground_m(scene) -> float:
    """Prefer start-site elevation; else DEM mean / nominal."""
    if scene.dem.available:
        for site in scene.sites:
            if site.can_start:
                return float(scene.dem.elevation(site.point.x, site.point.y))
        return float(scene.dem.sampler.mean_elevation_m)
    return float(scene.dem.nominal_elevation_m)


def _sample_points(task) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for tr in task.transects:
        pts.append(tr.start)
        pts.append(tr.end)
        if len(tr.coords) >= 3:
            mid = tr.coords[len(tr.coords) // 2]
            pts.append((float(mid[0]), float(mid[1])))
    return pts


def terrain_warnings_for_tasks(
    scene,
    tasks: dict,
    *,
    min_agl_m: float = MIN_SAFE_AGL_M,
    relief_frac: float = 0.25,
    relief_floor_m: float = 15.0,
) -> list[str]:
    """
    A-05: DEM planning checks (not full 3D spline).

    Assume constant geometric altitude = ref_ground + planned AGL.
    Local AGL at (x,y) = z_flight - ground(x,y).
    Warn when local AGL drops below min, relief is large, or local AGL swings hard (GSD drift).
    """
    if not scene.dem.available:
        return ["M3: no DEM — flat terrain assumed; AGL not terrain-checked"]

    ref_z = _reference_ground_m(scene)
    out: list[str] = []

    for task in tasks.values():
        pts = _sample_points(task)
        if not pts:
            continue
        grounds = [scene.dem.elevation(x, y) for x, y in pts]
        relief = max(grounds) - min(grounds)
        z_flight = ref_z + float(task.agl_m)
        local_agls = [z_flight - g for g in grounds]
        min_local = min(local_agls)
        max_local = max(local_agls)

        if min_local + 1e-6 < min_agl_m:
            out.append(
                f"M3: task {task.id} LOCAL_AGL_BELOW_MIN "
                f"min_local_agl={min_local:.1f}m < {min_agl_m:.0f}m "
                f"(ref_z={ref_z:.1f}m, planned_agl={task.agl_m:.0f}m) — raise AGL or follow terrain"
            )
        if relief > max(task.agl_m * relief_frac, relief_floor_m):
            out.append(
                f"M3: task {task.id} ground relief {relief:.1f}m vs AGL {task.agl_m:.0f}m "
                f"(consider terrain-following in autopilot)"
            )
        agl_swing = max_local - min_local
        if agl_swing > max(task.agl_m * 0.2, 10.0):
            out.append(
                f"M3: task {task.id} local AGL swing {agl_swing:.1f}m "
                f"(GSD/overlap will drift on hills)"
            )
    return out
