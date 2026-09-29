"""Extreme survey geometries — wild 2D footprints + Z/DEM (2.5D) stress.

Coverage is planar metres; vertical axis is AGL + DEM, not true volumetric 3D.
These tests lock: no crash, 2D outputs, hop bounds after chunking, Z stripped.
"""
from __future__ import annotations

import math

import pytest
from shapely import force_2d
from shapely.geometry import (
    LineString,
    MultiPolygon,
    Point,
    Polygon,
    box,
    shape,
)

from h1_coverage.config import CoverageConfig
from h1_coverage.coverage.atomic import chunk_transects
from h1_coverage.coverage.engine import build_coverage
from h1_coverage.coverage.sweep import generate_transects
from h1_coverage.geo import CrsPipeline, ensure_2d
from h1_coverage.io.geojson import normalize_survey_geom
from h1_coverage.models import (
    AtomicTask,
    Dem,
    Mission,
    PayloadProfile,
    Scene,
    SurveyJob,
    Transect,
    Uav,
)


SPACING = 40.0
MAX_HOP = max(2.5 * SPACING, 100.0)


def _star(cx: float, cy: float, r_out: float, r_in: float, n: int = 7) -> Polygon:
    coords: list[tuple[float, float]] = []
    for i in range(2 * n):
        ang = math.pi / 2 + i * math.pi / n
        r = r_out if i % 2 == 0 else r_in
        coords.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    coords.append(coords[0])
    return Polygon(coords)


def _ring(cx: float, cy: float, r_out: float, r_in: float, steps: int = 48) -> Polygon:
    outer = [
        (cx + r_out * math.cos(2 * math.pi * i / steps), cy + r_out * math.sin(2 * math.pi * i / steps))
        for i in range(steps)
    ]
    inner = [
        (cx + r_in * math.cos(2 * math.pi * i / steps), cy + r_in * math.sin(2 * math.pi * i / steps))
        for i in range(steps)
    ][::-1]
    return Polygon(outer, [inner])


def _crescent() -> Polygon:
    big = Point(0, 0).buffer(200)
    bite = Point(80, 0).buffer(160)
    return big.difference(bite)


def _spike() -> Polygon:
    return Polygon([(0, 0), (500, 0), (500, 40), (20, 40), (20, 400), (0, 400)])


def _many_holes(n: int = 5) -> Polygon:
    outer = box(0, 0, 600, 600)
    holes = []
    for i in range(n):
        x0 = 60 + i * 100
        y0 = 80 + (i % 3) * 120
        holes.append([(x0, y0), (x0 + 50, y0), (x0 + 50, y0 + 50), (x0, y0 + 50)])
    return Polygon(outer.exterior.coords, holes)


def _bowtie_invalid() -> Polygon:
    # Self-crossing; make_valid / ensure path must repair.
    return Polygon([(0, 0), (100, 100), (100, 0), (0, 100), (0, 0)])


def _zigzag_corridor() -> LineString:
    pts = []
    for i in range(12):
        pts.append((i * 40.0, 20.0 if i % 2 == 0 else 120.0))
    return LineString(pts)


def _max_hop(trs: list[Transect]) -> float:
    if len(trs) < 2:
        return 0.0
    return max(math.dist(trs[i].end, trs[i + 1].start) for i in range(len(trs) - 1))


def _assert_xy_only(trs: list[Transect]) -> None:
    for t in trs:
        for c in t.coords:
            assert len(c) == 2, f"expected XY, got {c}"


def _scene_for(geom, *, spacing: float = SPACING, job_id: str = "job") -> Scene:
    return Scene(
        id="extreme",
        crs=CrsPipeline.for_point(37.5, 55.7),
        jobs=[SurveyJob(id=job_id, survey_type="rgb", payload_profile_id="p", geom=geom)],
        fleet=[
            Uav(
                id="u1",
                model="geoscan_201",
                uav_class="multirotor",
                ground_speed_ms=12.0,
                operational_endurance_min=45.0,
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
                nominal_agl_m=120,
                agl_m=120,
                line_spacing_m=spacing,
                swath_spacing_m=spacing,
            )
        },
    )


def _chunked_ok(trs: list[Transect], max_hop: float = MAX_HOP) -> list[list[Transect]]:
    chunks = chunk_transects(trs, chunk_len_m=1e9, max_chunks=500, max_hop_m=max_hop)
    for ch in chunks:
        assert _max_hop(ch) <= max_hop + 1e-6
    return chunks


# --- Extreme 2D footprints -------------------------------------------------


@pytest.mark.parametrize(
    "name,geom",
    [
        ("star", _star(0, 0, 250, 90, 7)),
        ("ring", _ring(0, 0, 220, 120)),
        ("crescent", _crescent()),
        ("spike", _spike()),
        ("many_holes", _many_holes(6)),
        ("L", Polygon([(0, 0), (400, 0), (400, 80), (80, 80), (80, 400), (0, 400)])),
        ("U", Polygon([(0, 0), (400, 0), (400, 300), (320, 300), (320, 80), (80, 80), (80, 300), (0, 300)])),
        ("C", Polygon([(0, 0), (400, 0), (400, 300), (80, 300), (80, 220), (320, 220), (320, 80), (0, 80)])),
        (
            "islands",
            MultiPolygon([box(0, 0, 120, 120), box(800, 0, 920, 120), box(0, 700, 120, 820)]),
        ),
        ("thin_strip", box(0, 0, 2000, 25)),
        ("near_point", box(0, 0, 15, 15)),
    ],
)
@pytest.mark.parametrize("angle_deg", [0.0, 35.0, 90.0])
def test_extreme_2d_shapes_sweep_and_chunk(name: str, geom, angle_deg: float):
    assert not geom.is_empty
    trs = generate_transects(geom, spacing=SPACING, angle_deg=angle_deg, job_id=name, overshoot_m=12)
    # Tiny / pathological areas may yield no lines — that is acceptable if area ≪ spacing².
    if geom.area < SPACING * SPACING * 0.5:
        return
    assert trs, f"{name}@{angle_deg}: expected transects"
    _assert_xy_only(trs)
    _chunked_ok(trs)


def test_bowtie_invalid_is_repaired_and_sweepable():
    raw = _bowtie_invalid()
    assert not raw.is_valid
    trs = generate_transects(raw, spacing=20, angle_deg=0, job_id="bow", overshoot_m=5)
    # After make_valid inside decomp/sweep path — either lines or empty, never crash.
    _assert_xy_only(trs)
    if trs:
        _chunked_ok(trs, max_hop=max(2.5 * 20, 100))


def test_zigzag_corridor_normalize_and_sweep():
    line = _zigzag_corridor()
    buffered = line.buffer(25.0, cap_style=2)
    trs = generate_transects(buffered, spacing=20, angle_deg=0, job_id="zz", overshoot_m=5)
    assert trs
    _assert_xy_only(trs)
    _chunked_ok(trs, max_hop=max(2.5 * 20, 100))


# --- Z / "3D" coordinates (planarized) -------------------------------------


@pytest.mark.parametrize(
    "gj",
    [
        {
            "type": "Polygon",
            "coordinates": [
                [[0, 0, 10], [300, 0, 40], [300, 200, 5], [0, 200, 0], [0, 0, 10]]
            ],
        },
        {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0, 0, 1], [100, 0, 2], [100, 80, 3], [0, 80, 1], [0, 0, 1]]],
                [[[400, 0, 9], [500, 0, 8], [500, 80, 7], [400, 80, 9], [400, 0, 9]]],
            ],
        },
    ],
)
def test_polygon_z_is_planarized_and_sweepable(gj: dict):
    g = shape(gj)
    assert g.has_z
    flat = ensure_2d(g)
    assert not flat.has_z
    norm = normalize_survey_geom(g)
    assert norm is not None
    assert not getattr(norm, "has_z", False)
    trs = generate_transects(g, spacing=30, angle_deg=0, job_id="z", overshoot_m=10)
    assert trs
    _assert_xy_only(trs)
    _chunked_ok(trs, max_hop=max(2.5 * 30, 100))


def test_linestring_z_corridor_normalize():
    gj = {
        "type": "LineString",
        "coordinates": [[0, 0, 100], [150, 40, 120], [300, 0, 90], [450, 50, 110]],
    }
    g = shape(gj)
    assert g.has_z
    norm = normalize_survey_geom(g)
    assert norm is not None
    assert norm.geom_type in ("LineString", "MultiLineString")
    assert not getattr(norm, "has_z", False)
    buffered = force_2d(g).buffer(30.0)
    trs = generate_transects(buffered, spacing=25, angle_deg=20, job_id="lz", overshoot_m=8)
    assert trs
    _assert_xy_only(trs)


def test_engine_accepts_polygon_z_job():
    g = shape(
        {
            "type": "Polygon",
            "coordinates": [
                [[0, 0, 50], [250, 0, 80], [250, 180, 20], [0, 180, 0], [0, 0, 50]]
            ],
        }
    )
    result = build_coverage(_scene_for(g), CoverageConfig(strict_coverage=False, max_tasks_per_job=40))
    assert result.tasks
    for task in result.tasks.values():
        _assert_xy_only(task.transects)
        assert _max_hop(task.transects) <= MAX_HOP + 1e-6
        assert all(len(c) == 2 for c in task.geom_coords)


# --- DEM / vertical stress (product 2.5D) ----------------------------------


class _RidgeDem:
    """Sinusoidal ridge — steep vertical variation under a planar footprint."""

    mean_elevation_m = 50.0

    def elevation(self, x: float, y: float) -> float:
        return 50.0 + 60.0 * math.sin(x / 40.0) * math.cos(y / 55.0)


class _CliffDem:
    mean_elevation_m = 0.0

    def __init__(self, x_split: float):
        self.x_split = x_split

    def elevation(self, x: float, y: float) -> float:
        return 0.0 if x < self.x_split else 120.0


def test_engine_star_over_ridge_dem_no_crash():
    geom = _star(200, 200, 180, 70, 5)
    scene = _scene_for(geom, spacing=35)
    scene.dem = Dem(path="ridge", sampler=_RidgeDem(), nominal_elevation_m=50.0)
    result = build_coverage(
        scene,
        CoverageConfig(strict_coverage=False, max_tasks_per_job=60, terrain_strict=False),
    )
    assert result.tasks
    for task in result.tasks.values():
        _assert_xy_only(task.transects)
        assert _max_hop(task.transects) <= max(2.5 * 35, 100) + 1e-6


def test_engine_crescent_over_cliff_emits_terrain_signal_or_tasks():
    geom = _crescent()
    scene = _scene_for(geom, spacing=30)
    cx = geom.centroid.x
    scene.dem = Dem(path="cliff", sampler=_CliffDem(cx), nominal_elevation_m=0.0)
    # Low AGL so cliff can trip LOCAL_AGL_BELOW_MIN
    scene.payloads["p"] = PayloadProfile(
        id="p",
        type="rgb",
        nominal_agl_m=50,
        agl_m=50,
        line_spacing_m=30,
        swath_spacing_m=30,
    )
    result = build_coverage(
        scene,
        CoverageConfig(strict_coverage=False, max_tasks_per_job=60, terrain_strict=True),
    )
    assert result.tasks or result.warnings
    terrainish = [w for w in result.warnings if "AGL" in w or "terrain" in w.lower() or "M3" in w]
    # Either terrain warned or tasks still produced — must not raise.
    assert result.tasks or terrainish


def test_extreme_shapes_engine_smoke_batch():
    """One engine pass over several nasty footprints — no raise, XY-only tasks."""
    shapes = [
        _star(0, 0, 200, 80, 9),
        _ring(0, 0, 180, 100),
        _many_holes(4),
        MultiPolygon([box(0, 0, 150, 150), box(600, 400, 750, 550)]),
    ]
    for i, geom in enumerate(shapes):
        result = build_coverage(
            _scene_for(geom, spacing=45, job_id=f"e{i}"),
            CoverageConfig(strict_coverage=False, max_tasks_per_job=50, keep_candidates=2),
        )
        assert result.coverage_percent >= 0.0
        for task in result.tasks.values():
            _assert_xy_only(task.transects)
            assert len(task.entry) == 2 and len(task.exit) == 2
