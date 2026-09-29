"""M2 GeoProjector — contract h1.m2.geo_projector.v1

WGS-84 ↔ metric UTM for the scene. All task lengths/buffers are in metres.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from pyproj import Transformer
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform
from shapely.ops import unary_union

WGS84 = "EPSG:4326"


def utm_epsg_for_lonlat(lon: float, lat: float) -> str:
    zone = int(math.floor((lon + 180.0) / 6.0) % 60) + 1
    return f"EPSG:{32600 + zone if lat >= 0 else 32700 + zone}"


def looks_like_lonlat(x: float, y: float) -> bool:
    """Contract heuristic: metric task coords must not look like geographic degrees."""
    return abs(x) <= 180.0 and abs(y) <= 90.0


def require_metric_xy(x: float, y: float, *, context: str = "") -> None:
    if looks_like_lonlat(x, y):
        raise ValueError(
            f"M2: coordinate looks like lon/lat, not metres{': ' + context if context else ''}"
        )


@dataclass(frozen=True)
class CrsPipeline:
    metric_epsg: str
    geographic_epsg: str = WGS84

    @staticmethod
    def for_point(lon: float, lat: float) -> "CrsPipeline":
        return CrsPipeline(metric_epsg=utm_epsg_for_lonlat(lon, lat))

    def _fwd(self) -> Transformer:
        return Transformer.from_crs(self.geographic_epsg, self.metric_epsg, always_xy=True)

    def _inv(self) -> Transformer:
        return Transformer.from_crs(self.metric_epsg, self.geographic_epsg, always_xy=True)

    def to_metric(self, geom: BaseGeometry) -> BaseGeometry:
        t = self._fwd()
        return shapely_transform(lambda x, y, z=None: t.transform(x, y), geom)

    def to_geographic(self, geom: BaseGeometry) -> BaseGeometry:
        t = self._inv()
        return shapely_transform(lambda x, y, z=None: t.transform(x, y), geom)

    def lonlat_to_xy(self, lon: float, lat: float) -> tuple[float, float]:
        return self._fwd().transform(lon, lat)

    def xy_to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        return self._inv().transform(x, y)

    def roundtrip_error_m(self, lon: float, lat: float) -> float:
        """Architecture: round-trip error ≪ 1 m."""
        x, y = self.lonlat_to_xy(lon, lat)
        lon2, lat2 = self.xy_to_lonlat(x, y)
        x2, y2 = self.lonlat_to_xy(lon2, lat2)
        return math.hypot(x2 - x, y2 - y)

    @property
    def epsg_int(self) -> int:
        return int(self.metric_epsg.split(":")[-1])


def ensure_2d(geom: BaseGeometry) -> BaseGeometry:
    """Drop Z/M — coverage is planar metres; AGL/DEM carry the vertical axis."""
    if geom is None or geom.is_empty:
        return geom
    if getattr(geom, "has_z", False):
        from shapely import force_2d

        return force_2d(geom)
    return geom


def make_valid(geom: BaseGeometry) -> BaseGeometry:
    geom = ensure_2d(geom)
    if geom.is_valid:
        return geom
    return ensure_2d(geom.buffer(0))


def as_polygons(geom: BaseGeometry) -> list[Polygon]:
    g = make_valid(geom)
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    if hasattr(g, "geoms"):
        return [p for p in g.geoms if isinstance(p, Polygon)]
    return []


def union_all(geoms: list[BaseGeometry]) -> BaseGeometry:
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return Polygon()
    return unary_union(geoms)


def line_length(coords: list[tuple[float, float]]) -> float:
    return sum(math.dist(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def angular_difference(a: float, b: float) -> float:
    d = abs((a - b) % 180.0)
    return min(d, 180.0 - d)


def rotate_geom(geom: BaseGeometry, angle_deg: float, origin: tuple[float, float]) -> BaseGeometry:
    from shapely import affinity

    return affinity.rotate(geom, angle_deg, origin=origin)


def unrotate_geom(geom: BaseGeometry, angle_deg: float, origin: tuple[float, float]) -> BaseGeometry:
    return rotate_geom(geom, -angle_deg, origin)
