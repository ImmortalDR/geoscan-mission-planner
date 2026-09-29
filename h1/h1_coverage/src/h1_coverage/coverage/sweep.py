"""M6/M7 Sweep generation with holes + overshoot clipped to effective area (audit P0)."""
from __future__ import annotations

import math

from shapely.geometry import LineString, MultiLineString
from shapely.geometry.base import BaseGeometry

from ..geo import ensure_2d, line_length, rotate_geom, unrotate_geom
from ..models import Transect
from .decomp import decompose_area
from .grid import fixed_spacing_rows


def _segments_for_line(area: BaseGeometry, y: float, minx: float, maxx: float) -> list[list[tuple[float, float]]]:
    line = LineString([(minx - 50.0, y), (maxx + 50.0, y)])
    inter = line.intersection(area)
    if inter.is_empty:
        return []
    if isinstance(inter, LineString):
        parts = [inter]
    elif isinstance(inter, MultiLineString):
        parts = list(inter.geoms)
    elif hasattr(inter, "geoms"):
        parts = [g for g in inter.geoms if isinstance(g, LineString)]
    else:
        return []
    out = []
    for p in parts:
        cs = list(p.coords)
        if len(cs) >= 2 and math.dist(cs[0], cs[-1]) > 1.0:
            out.append([(cs[0][0], cs[0][1]), (cs[-1][0], cs[-1][1])])
    return out


def _extend_line(line: LineString, extra: float) -> LineString:
    (x1, y1), (x2, y2) = line.coords[0], line.coords[-1]
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    return LineString([(x1 - ux * extra, y1 - uy * extra), (x2 + ux * extra, y2 + uy * extra)])


def _clip_line_to_area(line: LineString, area: BaseGeometry) -> list[LineString]:
    """Keep only parts of the line inside area (fixes overshoot leaving effective)."""
    inter = line.intersection(area)
    if inter.is_empty:
        return []
    if isinstance(inter, LineString):
        return [inter] if inter.length > 1.0 else []
    if isinstance(inter, MultiLineString):
        return [g for g in inter.geoms if isinstance(g, LineString) and g.length > 1.0]
    if hasattr(inter, "geoms"):
        return [g for g in inter.geoms if isinstance(g, LineString) and g.length > 1.0]
    return []


def _orient_coords_to_intended(
    coords: list[tuple[float, float]],
    intended_start: tuple[float, float],
) -> list[tuple[float, float]]:
    """Shapely clip can reverse vertex order; keep travel matching the swath intent."""
    if len(coords) < 2:
        return coords
    if math.dist(coords[0], intended_start) <= math.dist(coords[-1], intended_start):
        return coords
    return list(reversed(coords))


def _orient_run_greedy(run: list[Transect]) -> list[Transect]:
    """Flip each next transect if that shortens the join from the previous end."""
    if len(run) <= 1:
        return run
    out = [run[0]]
    for t in run[1:]:
        d_start = math.dist(out[-1].end, t.start)
        d_end = math.dist(out[-1].end, t.end)
        if d_end + 1e-9 < d_start:
            t = Transect(coords=list(reversed(t.coords)), length_m=t.length_m, job_id=t.job_id)
        out.append(t)
    return out


def generate_transects(
    area: BaseGeometry,
    spacing: float,
    angle_deg: float,
    job_id: str,
    overshoot_m: float = 15.0,
) -> list[Transect]:
    """Boustrophedon sweep; overshoot is clipped back into ``area`` (P0 fix)."""
    area = ensure_2d(area)
    parts = decompose_area(area)
    if not parts or spacing <= 0:
        return []
    origin = (area.centroid.x, area.centroid.y)
    part_runs: list[list[Transect]] = []

    for part in parts:
        rot = rotate_geom(part, angle_deg, origin)
        minx, miny, maxx, maxy = rot.bounds
        height = maxy - miny
        if height <= 0:
            continue
        n_lines, pitch, y0 = fixed_spacing_rows(miny, maxy, spacing)
        run: list[Transect] = []
        flip = False
        for i in range(n_lines):
            y = y0 + i * pitch
            segs = _segments_for_line(rot, y, minx, maxx)
            if not segs:
                # Do not toggle flip: empty samples are not flown swaths.
                continue
            segs.sort(key=lambda s: min(s[0][0], s[1][0]), reverse=flip)
            for seg in segs:
                a, b = seg
                if (a[0] > b[0]) != flip:
                    a, b = b, a
                seg_line = LineString([a, b])
                if overshoot_m > 0:
                    seg_line = _extend_line(seg_line, overshoot_m)
                # rotate back then clip to original part (not rotated) — clip in world frame
                orig = unrotate_geom(seg_line, angle_deg, origin)
                intended = unrotate_geom(LineString([a, b]), angle_deg, origin)
                intended_start = (float(intended.coords[0][0]), float(intended.coords[0][1]))
                for clipped in _clip_line_to_area(orig, part):
                    cs = [(float(x), float(y2)) for x, y2 in clipped.coords]
                    if len(cs) < 2:
                        continue
                    cs = _orient_coords_to_intended(cs, intended_start)
                    run.append(Transect(coords=cs, length_m=line_length(cs), job_id=job_id))
            flip = not flip
        if run:
            part_runs.append(_orient_run_greedy(run))

    if not part_runs:
        return []
    # nearest-first part order; orient each join over 4 terminal configurations
    ordered: list[Transect] = []
    remaining = list(part_runs)
    cur = remaining.pop(0)
    ordered.extend(cur)
    while remaining:
        end_pt = ordered[-1].end

        def best_join(run: list[Transect]) -> tuple[float, list[Transect]]:
            return _best_oriented_run(end_pt, run)

        remaining.sort(key=lambda run: best_join(run)[0])
        _dist, nxt = best_join(remaining.pop(0))
        ordered.extend(nxt)
    return _orient_run_greedy(ordered)


def _reverse_run(run: list[Transect]) -> list[Transect]:
    return [
        Transect(coords=list(reversed(t.coords)), length_m=t.length_m, job_id=t.job_id)
        for t in reversed(run)
    ]


def _best_oriented_run(
    end_pt: tuple[float, float],
    run: list[Transect],
) -> tuple[float, list[Transect]]:
    """Pick orientation of ``run`` that minimizes end→start join distance."""
    candidates = [run, _reverse_run(run)]
    flipped = [
        Transect(coords=list(reversed(t.coords)), length_m=t.length_m, job_id=t.job_id)
        for t in run
    ]
    candidates.append(flipped)
    candidates.append(_reverse_run(flipped))

    best: list[Transect] | None = None
    best_d = float("inf")
    for cand in candidates:
        d = math.dist(end_pt, cand[0].start)
        if d < best_d:
            best_d = d
            best = cand
    assert best is not None
    return best_d, best
