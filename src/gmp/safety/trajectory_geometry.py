"""Independent trajectory geometry used by the H3 safety gate."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from shapely.geometry import LineString, Point, Polygon

@dataclass
class Segment:
    a: np.ndarray
    b: np.ndarray
    t0: float
    t1: float
    agl0: float
    agl1: float
    phase: str
    job_id: str | None
    uav_id: str
    sortie_id: str
    index: int

    @property
    def line(self):
        if np.linalg.norm(self.b[:2] - self.a[:2]) < 1e-9:
            return Point(self.a[:2])
        return LineString([self.a[:2], self.b[:2]])

    def at(self, time: float) -> np.ndarray:
        if self.t1 == self.t0:
            return self.a
        return self.a + (self.b - self.a) * ((time - self.t0) / (self.t1 - self.t0))


def interval_linear(a: float, b: float, low: float, high: float) -> tuple | None:
    if abs(b - a) < 1e-12:
        return (0.0, 1.0) if low <= a <= high else None
    limits = sorted(((low - a) / (b - a), (high - a) / (b - a)))
    left, right = max(0.0, limits[0]), min(1.0, limits[1])
    return (left, right) if left <= right else None


def spatial_intervals(segment: Segment, geometry) -> list[tuple[float, float]]:
    line = segment.line
    if line.geom_type == "Point":
        return [(0.0, 1.0)] if geometry.distance(line) <= 1e-7 else []
    intersection = line.intersection(geometry)
    if intersection.is_empty and line.distance(geometry) <= 1e-7:
        # Round-tripping WGS84 can displace nominally collinear points by a few
        # nanometres. Keep zero-buffer point/line obstacles effective.
        intersection = line.intersection(geometry.buffer(1e-7))
    if intersection.is_empty:
        return []

    def flatten(part):
        if hasattr(part, "geoms"):
            return [item for child in part.geoms for item in flatten(child)]
        return [part]

    ranges = []
    for part in flatten(intersection):
        coords = list(part.coords)
        values = [line.project(Point(x[:2]), normalized=True) for x in coords]
        ranges.append((min(values), max(values)))
    return ranges


def physical_swath(segment: Segment, payload: dict, minimum_agl: float):
    length = np.linalg.norm(segment.b[:2] - segment.a[:2])
    if length <= 1e-9:
        return Polygon()
    direction = (segment.b[:2] - segment.a[:2]) / length
    perpendicular = np.array([-direction[1], direction[0]])
    if payload["type"] in {"lidar", "geophysics"}:
        width0 = width1 = payload["line_spacing_m"] / 2
        half_forward0 = half_forward1 = 0.0
    else:
        camera = payload["camera"]
        scale = camera["pixel_pitch_um"] / 1000 / camera["focal_length_mm"]
        width0 = width1 = max(0, minimum_agl) * camera["image_width_px"] * scale / 2
        half_forward0 = half_forward1 = max(0, minimum_agl) * camera["image_height_px"] * scale / 2
    start = segment.a[:2] - direction * half_forward0
    end = segment.b[:2] + direction * half_forward1
    return Polygon([start + perpendicular * width0, end + perpendicular * width1,
                    end - perpendicular * width1, start - perpendicular * width0])


def conflict(first: Segment, second: Segment, horizontal: float, vertical: float) -> dict | None:
    start, end = max(first.t0, second.t0), min(first.t1, second.t1)
    if end < start:
        return None
    initial = first.at(start) - second.at(start)
    duration = end - start
    final = first.at(end) - second.at(end)
    vertical_interval = interval_linear(initial[2], final[2], -vertical, vertical)
    if vertical_interval is None:
        return None
    left, right = vertical_interval
    displacement = final[:2] - initial[:2]
    denom = float(np.dot(displacement, displacement))
    optimum = left if denom < 1e-20 else float(np.clip(-np.dot(initial[:2], displacement) / denom, left, right))
    distance = np.linalg.norm(initial[:2] + displacement * optimum)
    if distance < horizontal - 1e-8:
        return {"time_s": start + optimum * duration, "horizontal_m": float(distance),
                "vertical_m": float(abs(initial[2] + (final[2] - initial[2]) * optimum))}
    return None

