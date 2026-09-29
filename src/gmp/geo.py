"""CRS pipeline and geometric helpers.

External contract is WGS-84 (EPSG:4326). All planning math is done in a metric
projected CRS (UTM zone derived from the scene centroid), then results are
converted back to WGS-84 for export.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from pyproj import CRS, Transformer
from shapely import wkb
from shapely.geometry import (
    LineString,
    MultiPolygon,
    Point,
    Polygon,
    box,
)
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform
from shapely.ops import unary_union

WGS84 = "EPSG:4326"


def utm_epsg_for_lonlat(lon: float, lat: float) -> str:
    """Return the EPSG code of the UTM zone containing the given point."""
    zone = int(math.floor((lon + 180.0) / 6.0) % 60) + 1
    return f"EPSG:{32600 + zone if lat >= 0 else 32700 + zone}"


@dataclass(frozen=True)
class CrsPipeline:
    """Bidirectional WGS-84 <-> metric CRS converter."""

    metric_epsg: str
    geographic_epsg: str = WGS84

    @staticmethod
    def for_point(lon: float, lat: float) -> "CrsPipeline":
        return CrsPipeline(metric_epsg=utm_epsg_for_lonlat(lon, lat))

    @property
    def _to_metric(self) -> Transformer:
        return Transformer.from_crs(self.geographic_epsg, self.metric_epsg, always_xy=True)

    @property
    def _to_geographic(self) -> Transformer:
        return Transformer.from_crs(self.metric_epsg, self.geographic_epsg, always_xy=True)

    def to_metric(self, geom: BaseGeometry) -> BaseGeometry:
        t = self._to_metric
        return shapely_transform(lambda x, y, z=None: t.transform(x, y), geom)

    def to_geographic(self, geom: BaseGeometry) -> BaseGeometry:
        t = self._to_geographic
        return shapely_transform(lambda x, y, z=None: t.transform(x, y), geom)

    def xy_to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        lon, lat = self._to_geographic.transform(x, y)
        return lon, lat

    def lonlat_to_xy(self, lon: float, lat: float) -> tuple[float, float]:
        return self._to_metric.transform(lon, lat)

    def describe(self) -> dict:
        crs = CRS.from_user_input(self.metric_epsg)
        return {
            "geographic_crs": self.geographic_epsg,
            "metric_planning_crs": self.metric_epsg,
            "metric_crs_name": crs.name,
            "units": "m",
        }


def make_valid(geom: BaseGeometry) -> BaseGeometry:
    """Repair self-intersections while preserving holes where possible."""
    if geom.is_valid:
        return geom
    fixed = geom.buffer(0)
    if fixed.is_empty and not geom.is_empty:
        return geom
    return fixed


def as_polygons(geom: BaseGeometry) -> list[Polygon]:
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if isinstance(g, Polygon) and not g.is_empty]
    if hasattr(geom, "geoms"):
        out: list[Polygon] = []
        for g in geom.geoms:
            out.extend(as_polygons(g))
        return out
    return []


def union_all(geoms: Iterable[BaseGeometry]) -> BaseGeometry:
    items = [g for g in geoms if g is not None and not g.is_empty]
    if not items:
        return Polygon()
    return make_valid(unary_union(items))


def polygon_hole_count(geom: BaseGeometry) -> int:
    return sum(len(p.interiors) for p in as_polygons(geom))


def rotate_xy(x: float, y: float, angle_deg: float) -> tuple[float, float]:
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    return x * ca + y * sa, -x * sa + y * ca


def rotate_geom(geom: BaseGeometry, angle_deg: float, origin: tuple[float, float]) -> BaseGeometry:
    ox, oy = origin
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)

    def _f(x, y, z=None):
        dx, dy = x - ox, y - oy
        return dx * ca + dy * sa + ox, -dx * sa + dy * ca + oy

    return shapely_transform(_f, geom)


def unrotate_geom(geom: BaseGeometry, angle_deg: float, origin: tuple[float, float]) -> BaseGeometry:
    return rotate_geom(geom, -angle_deg, origin)


def min_width_angle(geom: BaseGeometry, step_deg: float = 5.0) -> float:
    """Sweep direction candidate: angle whose bounding box height is minimal.

    Flying transects along the long axis of the polygon minimises the number of
    turns, which is the dominant cost for fixed-wing platforms.
    """
    best_angle, best_h = 0.0, float("inf")
    c = geom.centroid
    origin = (c.x, c.y)
    angle = 0.0
    while angle < 180.0:
        rot = rotate_geom(geom, angle, origin)
        minx, miny, maxx, maxy = rot.bounds
        h = maxy - miny
        if h < best_h:
            best_h, best_angle = h, angle
        angle += step_deg
    return best_angle


def line_length(coords: Sequence[tuple[float, float]]) -> float:
    return sum(
        math.dist(coords[i], coords[i + 1]) for i in range(len(coords) - 1)
    )


def densify(coords: Sequence[tuple[float, float]], step_m: float) -> list[tuple[float, float]]:
    """Insert intermediate points so that no segment is longer than step_m."""
    if not coords:
        return []
    out: list[tuple[float, float]] = [tuple(coords[0])]
    for a, b in zip(coords, coords[1:]):
        d = math.dist(a, b)
        if d <= step_m or d == 0:
            out.append(tuple(b))
            continue
        n = int(math.ceil(d / step_m))
        for i in range(1, n + 1):
            t = i / n
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def bbox_geom(bounds: tuple[float, float, float, float], pad: float = 0.0) -> Polygon:
    minx, miny, maxx, maxy = bounds
    return box(minx - pad, miny - pad, maxx + pad, maxy + pad)


def segment_point_at(a: tuple[float, float], b: tuple[float, float], t: float) -> tuple[float, float]:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def to_wkb(geom: BaseGeometry) -> bytes:
    return wkb.dumps(geom)


def from_wkb(data: bytes) -> BaseGeometry:
    return wkb.loads(data)


def as_point(obj) -> Point:
    if isinstance(obj, Point):
        return obj
    return Point(obj[0], obj[1])


def heading_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Compass heading (deg, 0 = north, clockwise) of segment a->b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0


def angular_difference(a_deg: float, b_deg: float) -> float:
    """Smallest absolute difference between two directions modulo 180 deg."""
    d = abs((a_deg - b_deg) % 180.0)
    return min(d, 180.0 - d)


def linestring_or_none(coords: Sequence[tuple[float, float]]) -> LineString | None:
    if len(coords) < 2:
        return None
    return LineString(coords)
