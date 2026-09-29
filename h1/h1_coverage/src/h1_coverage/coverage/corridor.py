"""Corridor / strip survey (T-03)."""
from __future__ import annotations

import math

from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry

from ..geo import as_polygons, make_valid


def corridor_polygon(centerline: LineString, half_width_m: float) -> Polygon:
    """Build a survey strip around a road/powerline centerline."""
    if centerline.is_empty or half_width_m <= 0:
        return Polygon()
    buf = make_valid(centerline.buffer(half_width_m, cap_style=2, join_style=2))
    if isinstance(buf, Polygon):
        return buf
    parts = as_polygons(buf)
    return parts[0] if parts else Polygon()


def is_corridor_like(geom: BaseGeometry, *, min_aspect: float = 4.0) -> bool:
    if geom.is_empty:
        return False
    minx, miny, maxx, maxy = geom.bounds
    w, h = max(maxx - minx, 1e-9), max(maxy - miny, 1e-9)
    return max(w / h, h / w) >= min_aspect


def corridor_long_axis_angle_deg(geom: BaseGeometry) -> float:
    """Sweep angle aligned with the long axis of a corridor-like polygon (0..180)."""
    minx, miny, maxx, maxy = geom.bounds
    if (maxx - minx) >= (maxy - miny):
        return 0.0  # east-west strips → horizontal lines? lawnmower angle is line direction
    return 90.0


def preferred_corridor_angles(geom: BaseGeometry) -> list[tuple[float, str]]:
    """Angles to prefer for corridor jobs: along + across."""
    along = corridor_long_axis_angle_deg(geom)
    return [
        (along % 180.0, "corridor_along"),
        ((along + 90.0) % 180.0, "corridor_across"),
    ]
