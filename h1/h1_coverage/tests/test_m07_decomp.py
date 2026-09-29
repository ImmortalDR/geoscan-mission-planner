"""M7 Decomposition / holes — contract h1.m7.decomp_holes.v1 (+ A-04 merge/order)."""
from __future__ import annotations

from shapely.geometry import LineString, Polygon, box

from h1_coverage.config import CoverageConfig
from h1_coverage.contracts import check_m7_holes
from h1_coverage.coverage.decomp import (
    bcd_vertical_cells,
    decompose_area,
    extract_holes,
    merge_adjacent_cells,
    merge_small_cells,
    order_cells,
    transition_length_m,
)
from h1_coverage.coverage.engine import build_coverage
from h1_coverage.io.scene import load_scene
from h1_coverage.modules import by_id

from paths import scene


def _holed_rect() -> Polygon:
    outer = Polygon([(0, 0), (500, 0), (500, 400), (0, 400)])
    hole = Polygon([(150, 100), (350, 100), (350, 300), (150, 300)])
    return Polygon(outer.exterior.coords, [list(hole.exterior.coords)])


def test_module_registry_m7():
    assert by_id("M7").contract_id == "h1.m7.decomp_holes.v1"


def test_m7_bcd_splits_holed_polygon():
    area = _holed_rect()
    cells = bcd_vertical_cells(area)
    assert len(cells) >= 2
    holes = extract_holes(area)
    for c in cells:
        mid = c.centroid
        assert not any(h.contains(mid) for h in holes)


def test_a04_merge_reduces_fragments():
    area = _holed_rect()
    raw = bcd_vertical_cells(area)
    merged = merge_small_cells(merge_adjacent_cells(raw))
    assert len(merged) <= len(raw)
    assert len(merged) >= 2  # hole still forces multiple cells
    holes = extract_holes(area)
    for c in merged:
        assert not any(h.contains(c.centroid) for h in holes)


def test_a04_order_shortens_transitions():
    area = _holed_rect()
    cells = merge_small_cells(merge_adjacent_cells(bcd_vertical_cells(area)))
    # worst-case order: reverse by x then scramble by area
    bad = sorted(cells, key=lambda c: -c.centroid.x)
    good = order_cells(cells)
    assert transition_length_m(good) <= transition_length_m(bad) + 1e-6


def test_open_tour_tail_reversal_terminates_with_repeated_centroids():
    points = [(4, 18), (2, 8), (3, 15), (14, 15), (12, 6), (3, 15)]
    cells = [box(x - 0.25, y - 0.25, x + 0.25, y + 0.25) for x, y in points]
    ordered = order_cells(cells)
    assert sorted(map(id, ordered)) == sorted(map(id, cells))
    assert transition_length_m(ordered) <= transition_length_m(cells)
    assert ordered == order_cells(cells)


def test_a04_decompose_wires_merge_order():
    area = _holed_rect()
    raw_n = len(bcd_vertical_cells(area))
    cells = decompose_area(area)
    assert 2 <= len(cells) <= raw_n
    # ordered left→… (non-decreasing x of successive NN may zigzag; first is leftmost)
    assert cells[0].centroid.x == min(c.centroid.x for c in cells)


def test_m7_holes_s02():
    s = load_scene(scene("S02_multipolygon_holes"))
    r = build_coverage(s, CoverageConfig(strict_coverage=False))
    assert r.coverage_percent >= 95.0
    for job in s.jobs:
        geom = job.effective_geom or job.geom
        holes = extract_holes(geom)
        cells = decompose_area(geom)
        raw = []
        for part in [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", [geom])):
            if getattr(part, "interiors", None):
                raw.extend(bcd_vertical_cells(part))
        if raw:
            assert len(cells) <= len(raw)
        assert cells
        trs = []
        for t in r.tasks.values():
            if t.job_id == job.id:
                trs.extend(t.transects)
        if holes and trs:
            check_m7_holes(trs, holes)
        for t in trs:
            mid = LineString(t.coords).interpolate(0.5, normalized=True)
            for h in holes:
                assert not h.contains(mid)
