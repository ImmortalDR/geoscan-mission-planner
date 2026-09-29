"""M7 DecompositionEngine — BCD-style cells (Choset / ETH ideas, Shapely MVP).

Architecture: Boustrophedon Cellular Decomposition without CGAL/ROS.
- MultiPolygon → one cell per part
- Polygon with holes → vertical critical-event slices → simple cells
- A-04: merge undersized / adjacent cells; nearest-neighbor cell order
"""
from __future__ import annotations

import math

from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry

from ..geo import as_polygons, make_valid


def extract_holes(geom: BaseGeometry) -> list[Polygon]:
    holes: list[Polygon] = []
    for poly in as_polygons(geom):
        for ring in poly.interiors:
            holes.append(Polygon(ring))
    return holes


def _critical_xs(poly: Polygon) -> list[float]:
    xs: set[float] = set()
    for x, _y in poly.exterior.coords:
        xs.add(float(x))
    for ring in poly.interiors:
        for x, _y in ring.coords:
            xs.add(float(x))
    return sorted(xs)


def _union_simple(a: Polygon, b: Polygon) -> Polygon | None:
    """Union only if result is a single hole-free Polygon (still a lawnmower cell)."""
    u = make_valid(a.union(b))
    if isinstance(u, Polygon) and not u.is_empty and not u.interiors:
        # area sanity: no massive topology loss
        if u.area + 1.0 >= a.area + b.area - 1.0:
            return u
    return None


def bcd_vertical_cells(poly: Polygon, *, min_cell_area_m2: float = 1.0, merge_tol_m: float = 1.0) -> list[Polygon]:
    """
    Approximate BCD: slice by vertex abscissae, keep free-space strips.
    Cells remain free of hole interiors by construction (intersection with poly).
    """
    poly = make_valid(poly)
    if not isinstance(poly, Polygon) or poly.is_empty:
        return []
    if not poly.interiors:
        return [poly] if poly.area >= min_cell_area_m2 else []

    xs = _critical_xs(poly)
    if len(xs) < 2:
        return [poly]
    # merge nearly-duplicate critical lines
    merged = [xs[0]]
    for x in xs[1:]:
        if x - merged[-1] >= merge_tol_m:
            merged.append(x)

    minx, miny, maxx, maxy = poly.bounds
    pad = 1.0
    cells: list[Polygon] = []
    for i in range(len(merged) - 1):
        x0, x1 = merged[i], merged[i + 1]
        strip = box(x0, miny - pad, x1, maxy + pad)
        piece = make_valid(poly.intersection(strip))
        for part in as_polygons(piece):
            if part.area >= min_cell_area_m2:
                cells.append(part)
    return cells or ([poly] if poly.area >= min_cell_area_m2 else [])


def merge_adjacent_cells(
    cells: list[Polygon],
    *,
    touch_tol_m: float = 1.5,
) -> list[Polygon]:
    """
    Left-to-right greedy merge of touching hole-free cells (A-04).
    Collapses BCD strips that do not need a critical event between them.
    """
    if len(cells) <= 1:
        return list(cells)
    ordered = sorted((make_valid(c) for c in cells if not c.is_empty), key=lambda c: (c.centroid.x, c.centroid.y))
    out: list[Polygon] = [ordered[0]]
    for c in ordered[1:]:
        prev = out[-1]
        if prev.distance(c) <= touch_tol_m:
            u = _union_simple(prev, c)
            if u is not None:
                out[-1] = u
                continue
        out.append(c)
    return out


def merge_small_cells(
    cells: list[Polygon],
    *,
    min_keep_area_m2: float | None = None,
    touch_tol_m: float = 2.0,
    max_passes: int = 64,
) -> list[Polygon]:
    """
    Absorb undersized cells into a touching neighbor when union stays simple (A-04).
    Default threshold: max(50 m², 5% of median cell area).
    """
    work = [make_valid(c) for c in cells if isinstance(c, Polygon) and not c.is_empty]
    if len(work) <= 1:
        return work

    areas = sorted(c.area for c in work)
    median = areas[len(areas) // 2]
    threshold = min_keep_area_m2 if min_keep_area_m2 is not None else max(50.0, 0.05 * median)

    for _ in range(max_passes):
        small_idx = min(range(len(work)), key=lambda i: work[i].area)
        if work[small_idx].area >= threshold:
            break
        small = work[small_idx]
        best_j: int | None = None
        best_u: Polygon | None = None
        best_score = float("inf")
        for j, other in enumerate(work):
            if j == small_idx:
                continue
            d = small.distance(other)
            if d > touch_tol_m:
                continue
            u = _union_simple(small, other)
            if u is None:
                continue
            # prefer closer + larger partner (fewer future fragments)
            score = d - 0.001 * other.area
            if score < best_score:
                best_score = score
                best_j = j
                best_u = u
        if best_j is None or best_u is None:
            break
        keep = [c for i, c in enumerate(work) if i not in (small_idx, best_j)]
        keep.append(best_u)
        work = keep
    return work


def order_cells(cells: list[Polygon]) -> list[Polygon]:
    """NN tour + 2-opt polish (ETH-style cell visit order without GTSP solver)."""
    if len(cells) <= 1:
        return list(cells)
    centers = [(float(c.centroid.x), float(c.centroid.y)) for c in cells]
    remaining = sorted(range(len(cells)), key=lambda i: centers[i])
    ordered = [remaining.pop(0)]
    while remaining:
        last = centers[ordered[-1]]
        remaining.sort(key=lambda i: math.dist(last, centers[i]))
        ordered.append(remaining.pop(0))
    # An open tour has no outgoing edge at its tail. Every accepted reversal
    # must reduce its actual length; using an imaginary tail edge can cycle.
    improved = True
    while improved and len(ordered) >= 4:
        improved = False
        for i in range(len(ordered) - 2):
            for k in range(i + 2, len(ordered)):
                a, b = centers[ordered[i]], centers[ordered[i + 1]]
                c = centers[ordered[k]]
                if k + 1 >= len(ordered):
                    before = math.dist(a, b)
                    after = math.dist(a, c)
                else:
                    d = centers[ordered[k + 1]]
                    before = math.dist(a, b) + math.dist(c, d)
                    after = math.dist(a, c) + math.dist(b, d)
                if after + 1e-9 < before:
                    ordered[i + 1 : k + 1] = reversed(ordered[i + 1 : k + 1])
                    improved = True
                    break
            if improved:
                break
    return [cells[i] for i in ordered]


def transition_length_m(cells: list[Polygon]) -> float:
    """Sum of centroid→centroid hops (proxy for inter-cell ferry cost)."""
    if len(cells) < 2:
        return 0.0
    total = 0.0
    for a, b in zip(cells, cells[1:]):
        total += float(a.centroid.distance(b.centroid))
    return total


def decompose_area(
    geom: BaseGeometry,
    *,
    merge: bool = True,
    order: bool = True,
) -> list[Polygon]:
    """Split into plannable cells (BCD for holed polys, parts for MultiPolygon)."""
    out: list[Polygon] = []
    for part in as_polygons(geom):
        part = make_valid(part)
        if part.is_empty or part.area <= 1.0:
            continue
        if part.interiors:
            cells = bcd_vertical_cells(part)
            if merge:
                cells = merge_adjacent_cells(cells)
                cells = merge_small_cells(cells)
            if order:
                cells = order_cells(cells)
            out.extend(cells)
        else:
            out.append(part)
    if order and len(out) > 1:
        out = order_cells(out)
    return out


def cell_covers_without_holes(cells: list[Polygon], holes: list[Polygon]) -> bool:
    for cell in cells:
        for hole in holes:
            if hole.contains(cell):
                return False
    return True


def cells_avoid_hole_midlines(cells: list[Polygon], holes: list[Polygon]) -> bool:
    """Sanity: no cell centroid inside a hole."""
    for cell in cells:
        c = cell.centroid
        for hole in holes:
            if hole.contains(c):
                return False
    return True
