"""DEM (GeoTIFF) sampling in the scene metric CRS."""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.errors import RasterioIOError
from shapely.geometry.base import BaseGeometry

from ..geo import CrsPipeline, densify


class DemSampler:
    """Nearest-neighbour terrain sampler with a cached in-memory band."""

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
            self.res = ds.res
        self._same_crs = self.crs is not None and self.crs.to_string() == pipeline.metric_epsg
        self._tf = (
            None
            if self._same_crs
            else Transformer.from_crs(pipeline.metric_epsg, self.crs.to_string(), always_xy=True)
        )
        valid = self.band[np.isfinite(self.band)]
        if self.nodata is not None:
            valid = valid[valid != self.nodata]
        self.min_elevation_m = float(valid.min()) if valid.size else 0.0
        self.max_elevation_m = float(valid.max()) if valid.size else 0.0
        self.mean_elevation_m = float(valid.mean()) if valid.size else 0.0

    # ------------------------------------------------------------------ #
    def _to_raster_xy(self, x: float, y: float) -> tuple[float, float]:
        if self._tf is None:
            return x, y
        return self._tf.transform(x, y)

    def elevation(self, x: float, y: float) -> float:
        rx, ry = self._to_raster_xy(x, y)
        col, row = ~self.transform * (rx, ry)
        c, r = int(round(col)), int(round(row))
        c = min(max(c, 0), self.width - 1)
        r = min(max(r, 0), self.height - 1)
        v = float(self.band[r, c])
        if self.nodata is not None and v == self.nodata:
            return self.mean_elevation_m
        if not math.isfinite(v):
            return self.mean_elevation_m
        return v

    def elevations(self, points: Sequence[tuple[float, float]]) -> list[float]:
        return [self.elevation(px, py) for px, py in points]

    def max_elevation(self, geom: BaseGeometry, step_m: float = 30.0) -> float:
        """Maximum terrain elevation sampled over a geometry."""
        if geom.is_empty:
            return self.mean_elevation_m
        if geom.geom_type in ("LineString", "LinearRing"):
            pts = densify(list(geom.coords), step_m)
        elif geom.geom_type == "Point":
            pts = [(geom.x, geom.y)]
        else:
            minx, miny, maxx, maxy = geom.bounds
            pts = []
            nx = max(2, int((maxx - minx) / step_m) + 1)
            ny = max(2, int((maxy - miny) / step_m) + 1)
            nx, ny = min(nx, 200), min(ny, 200)
            for i in range(nx):
                for j in range(ny):
                    pts.append(
                        (
                            minx + (maxx - minx) * i / max(nx - 1, 1),
                            miny + (maxy - miny) * j / max(ny - 1, 1),
                        )
                    )
        return max(self.elevations(pts)) if pts else self.mean_elevation_m

    def profile(self, coords: Sequence[tuple[float, float]], step_m: float = 30.0) -> list[float]:
        return self.elevations(densify(coords, step_m))

    def describe(self) -> dict:
        return {
            "path": self.path,
            "crs": self.crs.to_string() if self.crs else None,
            "resolution_m": [float(self.res[0]), float(self.res[1])],
            "size": [self.width, self.height],
            "elevation_min_m": round(self.min_elevation_m, 2),
            "elevation_max_m": round(self.max_elevation_m, 2),
            "elevation_mean_m": round(self.mean_elevation_m, 2),
        }


def load_dem(path: str, pipeline: CrsPipeline) -> DemSampler | None:
    try:
        return DemSampler(path, pipeline)
    except (RasterioIOError, FileNotFoundError):
        return None
