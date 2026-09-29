"""M9 FixedWingBuffer — contract h1.m9.fixed_wing.v1 (feeds G5).

Endpoint circles + Dubins CSC (LSL/RSR) turn arcs between consecutive transect
ends. Falls back to semicircle samples only if CSC geometry is degenerate.
"""
from __future__ import annotations

import math

from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from ..models import Transect


def _heading(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.atan2(b[1] - a[1], b[0] - a[0])


def _dubins_csc_points(
    start: tuple[float, float],
    start_heading: float,
    end: tuple[float, float],
    end_heading: float,
    radius: float,
    samples: int = 12,
) -> list[tuple[float, float]] | None:
    """Approximate shortest Dubins CSC (LSL or RSR) as polyline samples."""
    if radius <= 1e-6:
        return [start, end]
    best: list[tuple[float, float]] | None = None
    best_len = float("inf")
    for side in (-1.0, 1.0):  # L = +1 center left of heading, R = -1
        cx0 = start[0] - side * radius * math.sin(start_heading)
        cy0 = start[1] + side * radius * math.cos(start_heading)
        cx1 = end[0] - side * radius * math.sin(end_heading)
        cy1 = end[1] + side * radius * math.cos(end_heading)
        dx, dy = cx1 - cx0, cy1 - cy0
        d = math.hypot(dx, dy)
        if d < 1e-9:
            continue
        # Straight tangent between circle centres (same-side CSC).
        ang = math.atan2(dy, dx)
        # Arc from start to tangent leave, then line, then arc to end.
        a0 = math.atan2(start[1] - cy0, start[0] - cx0)
        a1 = ang - side * (math.pi / 2.0)
        b0 = ang - side * (math.pi / 2.0)
        b1 = math.atan2(end[1] - cy1, end[0] - cx1)

        def _arc(cx, cy, ang0, ang1, sgn):
            # Sweep from ang0 to ang1 in direction of sgn (side).
            delta = (ang1 - ang0)
            while sgn > 0 and delta < 0:
                delta += 2 * math.pi
            while sgn < 0 and delta > 0:
                delta -= 2 * math.pi
            if abs(delta) < 1e-9:
                return [(cx + radius * math.cos(ang0), cy + radius * math.sin(ang0))]
            n = max(2, int(samples * abs(delta) / math.pi))
            return [
                (cx + radius * math.cos(ang0 + delta * (i / (n - 1))),
                 cy + radius * math.sin(ang0 + delta * (i / (n - 1))))
                for i in range(n)
            ]

        leave = (cx0 + radius * math.cos(a1), cy0 + radius * math.sin(a1))
        enter = (cx1 + radius * math.cos(b0), cy1 + radius * math.sin(b0))
        pts = _arc(cx0, cy0, a0, a1, side) + [leave, enter] + _arc(cx1, cy1, b0, b1, side)
        length = sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1))
        if length < best_len:
            best_len = length
            best = pts
    return best


def _arc_intersects_forbidden(
    a: tuple[float, float],
    b: tuple[float, float],
    radius: float,
    hard_nfz: BaseGeometry,
    allowed: BaseGeometry,
    heading_in: float | None = None,
    heading_out: float | None = None,
) -> bool:
    """Dubins CSC turn corridor between consecutive transect ends."""
    hi = heading_in if heading_in is not None else _heading(a, b)
    ho = heading_out if heading_out is not None else hi
    pts = _dubins_csc_points(a, hi, b, ho, radius)
    if pts is None or len(pts) < 2:
        # Degenerate fallback: semicircle samples (legacy Dubins-lite).
        dx, dy = b[0] - a[0], b[1] - a[1]
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            zone = Point(a).buffer(radius)
        else:
            mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
            ux, uy = -dy / dist, dx / dist
            samples = []
            for side in (-1.0, 1.0):
                cx, cy = mx + side * ux * radius, my + side * uy * radius
                arc_pts = []
                for k in range(9):
                    ang = math.atan2(a[1] - cy, a[0] - cx) + side * math.pi * (k / 8.0)
                    arc_pts.append((cx + radius * math.cos(ang), cy + radius * math.sin(ang)))
                samples.append(LineString(arc_pts) if len(arc_pts) >= 2 else Point(a))
            samples.sort(key=lambda g: g.length)
            zone = samples[0].buffer(max(radius * 0.15, 5.0))
            if not hard_nfz.is_empty and zone.intersects(hard_nfz):
                return True
            if not allowed.is_empty and not allowed.contains(zone):
                return True
            return False
    else:
        zone = LineString(pts).buffer(max(radius * 0.15, 5.0))
    if not hard_nfz.is_empty and zone.intersects(hard_nfz):
        return True
    if not allowed.is_empty and not allowed.contains(zone):
        return True
    return False


def maneuver_violations(
    transects: list[Transect],
    turn_radius_m: float,
    hard_nfz: BaseGeometry,
    allowed: BaseGeometry,
) -> int:
    if turn_radius_m <= 0:
        return 0
    bad = 0
    for t in transects:
        for pt in (t.start, t.end):
            zone = Point(pt).buffer(turn_radius_m)
            if not hard_nfz.is_empty and zone.intersects(hard_nfz):
                bad += 1
                continue
            if not allowed.is_empty and not allowed.contains(zone):
                bad += 1
    for i in range(len(transects) - 1):
        a = transects[i].end
        b = transects[i + 1].start
        hin = _heading(transects[i].start, transects[i].end)
        hout = _heading(transects[i + 1].start, transects[i + 1].end)
        if _arc_intersects_forbidden(a, b, turn_radius_m, hard_nfz, allowed, hin, hout):
            bad += 1
    return bad


def is_fixed_wing_safe(
    transects: list[Transect],
    turn_radius_m: float,
    hard_nfz: BaseGeometry,
    allowed: BaseGeometry,
) -> bool:
    return maneuver_violations(transects, turn_radius_m, hard_nfz, allowed) == 0
