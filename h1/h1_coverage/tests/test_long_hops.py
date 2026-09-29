"""Regression: long end→start hops must not stay inside AtomicTask geom."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from shapely.geometry import MultiPolygon, Polygon, box

from h1_coverage.config import CoverageConfig
from h1_coverage.coverage.atomic import build_atomic_tasks, chunk_transects
from h1_coverage.coverage.engine import build_coverage
from h1_coverage.coverage.sweep import generate_transects
from h1_coverage.geo import CrsPipeline
from h1_coverage.models import Mission, PayloadProfile, Scene, SurveyJob, Transect, Uav


def _max_internal_hop(chunk: list[Transect]) -> float:
    if len(chunk) < 2:
        return 0.0
    return max(math.dist(chunk[i].end, chunk[i + 1].start) for i in range(len(chunk) - 1))


def _hops(trs: list[Transect]) -> list[float]:
    return [math.dist(trs[i].end, trs[i + 1].start) for i in range(len(trs) - 1)]


def _rev_helps(trs: list[Transect]) -> list[tuple[int, float, float]]:
    """Pairs where reversing the next transect alone would shorten the join."""
    out: list[tuple[int, float, float]] = []
    for i in range(len(trs) - 1):
        d0 = math.dist(trs[i].end, trs[i + 1].start)
        d1 = math.dist(trs[i].end, trs[i + 1].end)
        if d1 + 1.0 < d0:
            out.append((i, d0, d1))
    return out


def test_plain_rectangle_lawnmower_hops_stay_near_spacing():
    area = box(0, 0, 400, 300)
    spacing = 40.0
    trs = generate_transects(area, spacing=spacing, angle_deg=0, job_id="rect", overshoot_m=0)
    assert len(trs) >= 3
    max_hop = _max_internal_hop(trs)
    # Adjacent swath turns + optional overshoot stubs; not a cross-field ferry.
    assert max_hop <= 2.5 * spacing + 1.0
    assert not _rev_helps(trs)


@pytest.mark.parametrize("angle_deg", [0.0, 30.0, 90.0])
@pytest.mark.parametrize("overshoot_m", [0.0, 15.0, 25.0])
def test_plain_rectangle_no_reverse_helps_across_angles(angle_deg: float, overshoot_m: float):
    trs = generate_transects(
        box(0, 0, 400, 300),
        spacing=40.0,
        angle_deg=angle_deg,
        job_id="rect",
        overshoot_m=overshoot_m,
    )
    assert trs
    assert not _rev_helps(trs), _rev_helps(trs)


def test_multipoly_far_parts_split_on_max_hop():
    # Two islands ~5 km apart — join must not live inside one task geom.
    mp = MultiPolygon([box(0, 0, 200, 200), box(5000, 0, 5200, 200)])
    spacing = 50.0
    max_hop = max(2.5 * spacing, 100.0)
    trs = generate_transects(mp, spacing=spacing, angle_deg=0, job_id="J", overshoot_m=15)
    assert trs
    chunks = chunk_transects(trs, chunk_len_m=1e9, max_chunks=100, max_hop_m=max_hop)
    assert len(chunks) >= 2
    for ch in chunks:
        assert _max_internal_hop(ch) <= max_hop + 1e-6
    # The inter-island ferry sits between chunks, not inside geom.
    ferry = math.dist(chunks[0][-1].end, chunks[1][0].start)
    assert ferry > max_hop
    tasks = build_atomic_tasks(
        chunks,
        job_id="J",
        payload_class="rgb",
        payload_profile_id="p",
        agl_m=100,
        sweep_angle_deg=0,
        fixed_wing_safe_flags=[True] * len(chunks),
    )
    assert all(_max_internal_hop(t.transects) <= max_hop + 1e-6 for t in tasks)
    assert all(t.internal_transition_m <= max_hop * max(len(t.transects), 1) for t in tasks)


@pytest.mark.parametrize("angle_deg", [0.0, 90.0])
def test_three_islands_chunked_hops_bounded(angle_deg: float):
    mp = MultiPolygon(
        [
            box(0, 0, 100, 100),
            box(2000, 0, 2100, 100),
            box(0, 2000, 100, 2100),
        ]
    )
    spacing = 25.0
    max_hop = max(2.5 * spacing, 100.0)
    trs = generate_transects(mp, spacing=spacing, angle_deg=angle_deg, job_id="3", overshoot_m=10)
    chunks = chunk_transects(trs, chunk_len_m=1e9, max_chunks=200, max_hop_m=max_hop)
    assert len(chunks) >= 3
    assert all(_max_internal_hop(c) <= max_hop + 1e-6 for c in chunks)


def test_holed_rect_cell_join_not_full_width_when_short_join_exists():
    outer = Polygon([(0, 0), (1000, 0), (1000, 800), (0, 800)])
    hole = Polygon([(400, 200), (600, 200), (600, 600), (400, 600)])
    area = Polygon(outer.exterior.coords, [list(hole.exterior.coords)])
    spacing = 100.0
    trs = generate_transects(area, spacing=spacing, angle_deg=0, job_id="hole", overshoot_m=0)
    assert trs
    # With 4-way orientation, no consecutive pair should need a near-full-width hop
    # solely due to wrong terminal orientation (width 1000).
    width = 1000.0
    bad = [
        math.dist(trs[i].end, trs[i + 1].start)
        for i in range(len(trs) - 1)
        if math.dist(trs[i].end, trs[i + 1].start) > 0.85 * width
    ]
    # Hole gaps along a swath can still be ~200 m (hole width); full-width is the bug.
    assert not bad, f"full-width joins remain: {bad}"


@pytest.mark.parametrize("angle_deg", [0.0, 90.0])
@pytest.mark.parametrize("overshoot_m", [0.0, 15.0, 25.0])
def test_holed_rect_clip_does_not_leave_reverse_helps(angle_deg: float, overshoot_m: float):
    outer = Polygon([(0, 0), (1000, 0), (1000, 800), (0, 800)])
    hole = Polygon([(400, 200), (600, 200), (600, 600), (400, 600)])
    area = Polygon(outer.exterior.coords, [list(hole.exterior.coords)])
    trs = generate_transects(
        area, spacing=40.0, angle_deg=angle_deg, job_id="hole", overshoot_m=overshoot_m
    )
    assert trs
    assert not _rev_helps(trs), _rev_helps(trs)


def test_u_shape_along_swath_gaps_split_by_chunk():
    """Same-row multi-seg gaps (U opening) must leave geom via max_hop split."""
    u = Polygon(
        [
            (0, 0),
            (400, 0),
            (400, 300),
            (300, 300),
            (300, 100),
            (100, 100),
            (100, 300),
            (0, 300),
        ]
    )
    spacing = 40.0
    max_hop = max(2.5 * spacing, 100.0)
    trs = generate_transects(u, spacing=spacing, angle_deg=0, job_id="U", overshoot_m=0)
    assert any(h > max_hop for h in _hops(trs)), "precondition: U has along-swath gaps"
    chunks = chunk_transects(trs, chunk_len_m=1e9, max_chunks=200, max_hop_m=max_hop)
    assert len(chunks) > 1
    assert all(_max_internal_hop(c) <= max_hop + 1e-6 for c in chunks)


def test_chunk_merge_prefers_short_hops_under_max_chunks():
    # Force many tiny chunks then collapse: long hop must stay a boundary if possible.
    lines = [
        Transect(coords=[(0, 0), (10, 0)], length_m=10, job_id="j"),
        Transect(coords=[(10, 40), (20, 40)], length_m=10, job_id="j"),  # short hop ~40
        Transect(coords=[(5000, 0), (5010, 0)], length_m=10, job_id="j"),  # long hop
        Transect(coords=[(5010, 40), (5020, 40)], length_m=10, job_id="j"),
    ]
    chunks = chunk_transects(lines, chunk_len_m=1.0, max_chunks=2, max_hop_m=100.0)
    assert len(chunks) == 2
    assert _max_internal_hop(chunks[0]) <= 100.0
    assert _max_internal_hop(chunks[1]) <= 100.0
    # The long ferry is between chunks, not inside.
    assert math.dist(chunks[0][-1].end, chunks[1][0].start) > 100.0
    assert chunk_transects.last_forced_long_joins == 0


def test_forced_long_hop_merge_when_max_chunks_too_tight():
    lines = [
        Transect(coords=[(0, 0), (10, 0)], length_m=10, job_id="j"),
        Transect(coords=[(5000, 0), (5010, 0)], length_m=10, job_id="j"),
        Transect(coords=[(10000, 0), (10010, 0)], length_m=10, job_id="j"),
    ]
    chunks = chunk_transects(lines, chunk_len_m=1.0, max_chunks=1, max_hop_m=100.0)
    assert len(chunks) == 1
    assert chunk_transects.last_forced_long_joins >= 1
    assert _max_internal_hop(chunks[0]) > 100.0


def test_engine_multipoly_tasks_have_bounded_internal_hops():
    spacing = 50.0
    max_hop = max(2.5 * spacing, 100.0)
    area = MultiPolygon([box(0, 0, 200, 200), box(5000, 0, 5200, 200)])
    scene = Scene(
        id="long_hop_engine",
        crs=CrsPipeline.for_point(37.5, 55.7),
        jobs=[SurveyJob(id="job", survey_type="rgb", payload_profile_id="p", geom=area)],
        fleet=[
            Uav(
                id="u1",
                model="geoscan_201",
                uav_class="multirotor",
                ground_speed_ms=12.0,
                operational_endurance_min=40.0,
                max_wind_ms=12.0,
                payload_classes=["rgb"],
            )
        ],
        sites=[],
        mission=Mission(),
        payloads={
            "p": PayloadProfile(
                id="p",
                type="rgb",
                nominal_agl_m=100,
                agl_m=100,
                line_spacing_m=spacing,
                swath_spacing_m=spacing,
            )
        },
    )
    result = build_coverage(scene, CoverageConfig(strict_coverage=False, max_tasks_per_job=80))
    assert result.tasks
    for task in result.tasks.values():
        assert _max_internal_hop(task.transects) <= max_hop + 1e-6
        assert task.entry == task.transects[0].start
        assert task.exit == task.transects[-1].end
        assert task.geom_coords[0] == task.entry
        assert task.geom_coords[-1] == task.exit


def _fixture_tasks(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    tasks = data.get("tasks")
    if isinstance(tasks, dict):
        return list(tasks.values())
    return list(tasks or [])


def test_live_fixtures_have_no_cross_map_internal_hops():
    """Guard regenerated seam fixtures: no multi-km survey diagonals inside a task."""
    root = Path(__file__).resolve().parents[2] / "fixtures" / "h1_h2"
    bundles = sorted(root.glob("*.bundle.json"))
    assert bundles, f"missing fixtures in {root}"
    offenders: list[str] = []
    for path in bundles:
        for task in _fixture_tasks(path):
            trs = task.get("transects") or []
            for i in range(len(trs) - 1):
                hop = math.dist(trs[i]["coords"][-1], trs[i + 1]["coords"][0])
                # Engine threshold is max(2.5*spacing, 100); allow small numeric slack.
                if hop > 150.0:
                    offenders.append(f"{path.name} {task.get('id')} hop={hop:.1f}")
    assert not offenders, "\n".join(offenders[:20])
