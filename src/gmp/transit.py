"""Transit path construction: straight line when safe, detour when not.

Transit legs must not cross hard NFZ, obstacle protection footprints or
permanently active airspace volumes that prohibit the transit altitude. The
router builds a small visibility graph over the blocking polygons' vertices,
which is cheap for the handful of zones present in a preflight scene.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

from .geo import as_polygons, union_all
from .models import Obstacle, Scene, Zone

#: Clearance added around blocking geometry when building detours.
DETOUR_CLEARANCE_M = 40.0


@dataclass
class TransitRouter:
    """Cached router for one transit altitude band."""

    blocked: BaseGeometry
    allowed: BaseGeometry
    clearance_m: float = DETOUR_CLEARANCE_M
    _cache: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._inflated = (
            self.blocked.buffer(self.clearance_m) if not self.blocked.is_empty else self.blocked
        )
        self._prepared = prep(self._inflated) if not self._inflated.is_empty else None
        self._graph_points: list[tuple[float, float]] = []
        if not self._inflated.is_empty:
            for poly in as_polygons(self._inflated):
                ring = poly.exterior.simplify(5.0)
                self._graph_points.extend(
                    (x, y) for x, y in list(ring.coords)[:-1]
                )

    # ------------------------------------------------------------------ #
    @property
    def has_obstructions(self) -> bool:
        return self._prepared is not None

    def _blocked_segment(self, a: tuple[float, float], b: tuple[float, float]) -> bool:
        if self._prepared is None:
            return False
        seg = LineString([a, b])
        if self._prepared.intersects(seg):
            return True
        if not self.allowed.is_empty and not self.allowed.covers(seg):
            return True
        return False

    def path(
        self, a: tuple[float, float], b: tuple[float, float]
    ) -> tuple[list[tuple[float, float]], float]:
        """Return (polyline, length) of a safe transit from a to b."""
        if math.dist(a, b) < 1e-6:
            return [a, b], 0.0
        if not self._blocked_segment(a, b):
            return [a, b], math.dist(a, b)
        key = (round(a[0], 1), round(a[1], 1), round(b[0], 1), round(b[1], 1))
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        nodes: list[tuple[float, float]] = [a, b] + self._graph_points
        n = len(nodes)
        # Dijkstra over the visibility graph.
        dist = [math.inf] * n
        prev = [-1] * n
        dist[0] = 0.0
        visited = [False] * n
        pq: list[tuple[float, int]] = [(0.0, 0)]
        vis_cache: dict[tuple[int, int], bool] = {}

        def visible(i: int, j: int) -> bool:
            k = (min(i, j), max(i, j))
            v = vis_cache.get(k)
            if v is None:
                v = not self._blocked_segment(nodes[i], nodes[j])
                vis_cache[k] = v
            return v

        while pq:
            d, i = heapq.heappop(pq)
            if visited[i]:
                continue
            visited[i] = True
            if i == 1:
                break
            for j in range(n):
                if j == i or visited[j]:
                    continue
                if not visible(i, j):
                    continue
                nd = d + math.dist(nodes[i], nodes[j])
                if nd < dist[j] - 1e-9:
                    dist[j] = nd
                    prev[j] = i
                    heapq.heappush(pq, (nd, j))

        if dist[1] == math.inf:
            # No safe detour found: report the straight line, the safety
            # validator will flag it instead of silently hiding the problem.
            result = ([a, b], math.dist(a, b))
        else:
            path: list[tuple[float, float]] = []
            cur = 1
            while cur != -1:
                path.append(nodes[cur])
                cur = prev[cur]
            path.reverse()
            result = (path, dist[1])
        self._cache[key] = result
        return result

    def length(self, a: tuple[float, float], b: tuple[float, float]) -> float:
        return self.path(a, b)[1]


def blocking_geometry(
    scene: Scene, altitude_agl_m: float, include_temporal: bool = False
) -> BaseGeometry:
    """Geometry a UAV must not overfly at the given AGL."""
    parts: list[BaseGeometry] = [z.geom for z in scene.no_fly_zones if z.hard]
    for obs in scene.obstacles:
        if obs.height_m + obs.vertical_buffer_m >= altitude_agl_m:
            parts.append(obs.protected_footprint())
    win_start = scene.mission.effective_start()
    win_end = scene.mission.effective_end()
    terrain = scene.dem.sampler.mean_elevation_m if scene.dem.available else 0.0
    for zone in scene.airspace_constraints:
        permanent = zone.covers_window(win_start, win_end)
        if not permanent and not include_temporal:
            continue
        max_terrain = scene.dem.max_elevation(zone.geom) if scene.dem.available else terrain
        if not zone.permits_altitude(max_terrain + altitude_agl_m):
            parts.append(zone.geom)
    return union_all(parts)


def make_router(scene: Scene, altitude_agl_m: float, inset_m: float = 20.0) -> TransitRouter:
    allowed = union_all([z.geom for z in scene.allowed_airspace])
    if not allowed.is_empty and inset_m > 0:
        inset = allowed.buffer(-inset_m)
        if not inset.is_empty:
            allowed = inset
    return TransitRouter(blocked=blocking_geometry(scene, altitude_agl_m), allowed=allowed)


class RouterBank:
    """Routers keyed by rounded altitude band (25 m granularity)."""

    def __init__(self, scene: Scene):
        self.scene = scene
        self._bank: dict[int, TransitRouter] = {}

    def for_altitude(self, agl_m: float) -> TransitRouter:
        key = int(round(agl_m / 25.0))
        r = self._bank.get(key)
        if r is None:
            r = make_router(self.scene, key * 25.0)
            self._bank[key] = r
        return r
