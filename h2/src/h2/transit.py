"""Optional metric scene context; never modifies H1 task coordinates."""

import heapq
import math
from functools import lru_cache
from pathlib import Path

from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union


class NoPath(ValueError):
    """Endpoints cannot be joined through the declared flight area."""


@lru_cache(maxsize=4)
def _read_raster(path, mtime_ns, size):
    import numpy as np
    import rasterio

    with rasterio.open(path) as source:
        if source.crs is None:
            raise ValueError("DEM raster requires a CRS")
        values = source.read(1, masked=True)
        return values.data, np.ma.getmaskarray(values), source.transform, source.crs


def _geometry(value):
    if value.get("type") == "Feature":
        value = value["geometry"]
    result = shape(value)
    if not result.is_valid or result.is_empty or result.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("Scene areas must be nonempty valid Polygon/MultiPolygon")
    return result


def _vertices(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        rings = [geometry.exterior, *geometry.interiors]
        return [tuple(p[:2]) for ring in rings for p in list(ring.coords)[:-1]]
    return [p for part in geometry.geoms for p in _vertices(part)]


class TransitContext:
    """Visibility-graph shortest paths in polygonal free space, plus grid DEM."""

    def __init__(self, scene: dict | None, metric_epsg):
        scene = scene or {}
        if scene:
            if scene.get("schema_version") != "h2.scene.v1":
                raise ValueError("Expected h2.scene.v1")
            crs = scene.get("crs")
            epsg = crs.get("metric_epsg", crs.get("epsg")) if isinstance(crs, dict) else crs
            if str(epsg).removeprefix("EPSG:") != str(metric_epsg).removeprefix("EPSG:"):
                raise ValueError("Scene and H1 metric CRS must match; no reprojection is performed")
        self.allowed = _geometry(scene["allowed"]) if scene.get("allowed") else None
        obstacles = []
        for item in scene.get("forbidden", []):
            margin = item.get("properties", {}).get("buffer_m", 0)
            if not math.isfinite(margin) or margin < 0:
                raise ValueError("Obstacle buffer_m must be finite and nonnegative")
            obstacles.append(_geometry(item).buffer(margin, join_style=2))
        self.forbidden = unary_union(obstacles)
        self.unconstrained = self.allowed is None and self.forbidden.is_empty
        self._cache = {}
        self._spaces = {}
        self.terrain = scene.get("terrain")
        self.has_terrain = self.terrain is not None
        self.sample_step_m = math.inf
        self._raster = self.has_terrain and self.terrain.get("kind") == "raster"
        if self._raster:
            from pyproj import Transformer

            path = Path(self.terrain["path"]).resolve(strict=True)
            stat = path.stat()
            self._values, self._mask, self._transform, raster_crs = _read_raster(str(path), stat.st_mtime_ns, stat.st_size)
            self._inverse_transform = ~self._transform
            self._to_raster = (None if raster_crs == metric_epsg else
                               Transformer.from_crs(metric_epsg, raster_crs, always_xy=True))
            to_metric = Transformer.from_crs(raster_crs, metric_epsg, always_xy=True)
            center = self._transform * (self._values.shape[1]/2, self._values.shape[0]/2)
            neighbors = [self._transform * (self._values.shape[1]/2+dx, self._values.shape[0]/2+dy)
                         for dx, dy in ((1, 0), (0, 1))]
            pixel_sizes = [math.dist(to_metric.transform(*center), to_metric.transform(*p)) for p in neighbors]
            if not all(math.isfinite(s) and s > 0 for s in pixel_sizes):
                raise ValueError("DEM raster has invalid pixel size")
            self.sample_step_m = min(pixel_sizes)/2
        elif self.has_terrain:
            grid = self.terrain
            self._values = [list(row) for row in grid["values"]]
            self._origin = tuple(grid["origin"])
            self._cell = grid["cell_size_m"]
            if (len(self._origin) != 2 or len(self._values) < 2 or len(self._values[0]) < 2
                    or any(len(row) != len(self._values[0]) for row in self._values)
                    or not math.isfinite(self._cell) or self._cell <= 0
                    or not all(math.isfinite(v) for v in self._origin)
                    or not all(math.isfinite(v) for row in self._values for v in row)):
                raise ValueError("DEM requires a finite rectangular grid >= 2x2 and positive cell size")
            self.sample_step_m = self._cell/2

    def elevation(self, x, y):
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("Coordinates must be finite")
        if not self.has_terrain:
            return 0.0
        if self._raster:
            rx, ry = self._to_raster.transform(x, y) if self._to_raster else (x, y)
            col, row = self._inverse_transform * (rx, ry)
            if not all(math.isfinite(v) for v in (col, row)):
                raise ValueError("Coordinate cannot be projected into DEM")
            col, row = math.floor(col), math.floor(row)
            if not 0 <= row < self._values.shape[0] or not 0 <= col < self._values.shape[1]:
                raise ValueError("Coordinate outside DEM extent; no flat extrapolation allowed")
            value = self._values[row, col]
            if self._mask[row, col] or not math.isfinite(float(value)):
                raise ValueError("Coordinate intersects missing DEM values")
            return float(value)
        u, v = (x-self._origin[0])/self._cell, (y-self._origin[1])/self._cell
        rows, cols = len(self._values), len(self._values[0])
        if not 0 <= u <= cols-1 or not 0 <= v <= rows-1:
            raise ValueError("Coordinate outside DEM extent; no flat extrapolation allowed")
        ix, iy = min(int(u), cols-2), min(int(v), rows-2)
        fx, fy = u-ix, v-iy
        a, b = self._values[iy], self._values[iy+1]
        return (1-fy)*(a[ix]*(1-fx)+a[ix+1]*fx) + fy*(b[ix]*(1-fx)+b[ix+1]*fx)

    def _space(self, buffer_m):
        if not math.isfinite(buffer_m) or buffer_m < 0:
            raise ValueError("Flight buffer must be finite and nonnegative")
        if buffer_m in self._spaces:
            return self._spaces[buffer_m]
        # Geometries are immutable; every reachability sample uses this same space.
        blocked = self.forbidden.buffer(buffer_m+1e-6, join_style=2)
        allowed = self.allowed.buffer(-buffer_m, join_style=2) if self.allowed is not None else None
        from shapely import prepare
        if allowed is not None:
            prepare(allowed)
        self._spaces[buffer_m] = (blocked, allowed)
        return blocked, allowed

    @staticmethod
    def _clear(a, b, blocked, allowed):
        segment = Point(a) if a == b else LineString([a, b])
        if allowed is not None and not allowed.covers(segment):
            return False
        if blocked.is_empty:
            return True
        # The boundary of the expanded exclusion is traversable; its interior is not.
        return segment.relate_pattern(blocked, "F********")

    def geometry_clear(self, coords, buffer_m=0):
        points = [tuple(p[:2]) for p in coords]
        if not points or not all(math.isfinite(v) for p in points for v in p):
            return False
        if self.unconstrained and math.isfinite(buffer_m) and buffer_m >= 0:
            return True
        blocked, allowed = self._space(buffer_m)
        geometry = Point(points[0]) if all(p == points[0] for p in points) else LineString(points)
        return ((allowed is None or allowed.covers(geometry)) and
                (blocked.is_empty or geometry.relate_pattern(blocked, "F********")))

    def path(self, a, b, buffer_m=0):
        a, b = tuple(a[:2]), tuple(b[:2])
        if len(a) != 2 or len(b) != 2 or not all(math.isfinite(v) for v in (*a, *b)):
            raise ValueError("Path endpoints must be finite xy pairs")
        if not math.isfinite(buffer_m) or buffer_m < 0:
            raise ValueError("Flight buffer must be finite and nonnegative")
        if self.unconstrained:
            return [a, b] if a != b else [a]
        key = (a, b, buffer_m)
        if key in self._cache:
            return list(self._cache[key])
        blocked, allowed = self._space(buffer_m)
        if self._clear(a, b, blocked, allowed):
            result = [a, b] if a != b else [a]
        else:
            if not self._clear(a, a, blocked, allowed) or not self._clear(b, b, blocked, allowed):
                raise NoPath("Endpoint is outside allowed area or inside buffered obstacle")
            vertices = _vertices(allowed.difference(blocked)) if allowed is not None else _vertices(blocked)
            nodes = list(dict.fromkeys([a, b] + [p for p in vertices if self._clear(p, p, blocked, allowed)]))
            edges = [[] for _ in nodes]
            for i, p in enumerate(nodes):
                for j in range(i):
                    q = nodes[j]
                    if self._clear(p, q, blocked, allowed):
                        distance = math.dist(p, q)
                        edges[i].append((j, distance))
                        edges[j].append((i, distance))
            distance, predecessor = {0: 0.0}, {}
            queue = [(0.0, 0)]
            while queue:
                cost, i = heapq.heappop(queue)
                if cost != distance[i]:
                    continue
                if i == 1:
                    break
                for j, weight in edges[i]:
                    candidate = cost+weight
                    if candidate < distance.get(j, math.inf):
                        distance[j], predecessor[j] = candidate, i
                        heapq.heappush(queue, (candidate, j))
            if 1 not in distance:
                raise NoPath("No connected route through the allowed flight area")
            indices = [1]
            while indices[-1] != 0:
                indices.append(predecessor[indices[-1]])
            result = [nodes[i] for i in reversed(indices)]
        self._cache[key] = tuple(result)
        self._cache[(b, a, buffer_m)] = tuple(reversed(result))
        return list(result)
