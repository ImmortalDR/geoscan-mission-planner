from copy import deepcopy
import math

import pytest
from shapely.geometry import Polygon, box, mapping

from h2.transit import NoPath, TransitContext


def scene(**kwargs):
    return dict(schema_version="h2.scene.v1", crs={"metric_epsg": 32637}, **kwargs)


def test_straight_and_obstacle_detour_preserve_inputs():
    context = TransitContext(None, 32637)
    assert context.path([0, 0], [10, 0]) == [(0, 0), (10, 0)]
    raw = scene(forbidden=[mapping(box(4, -1, 6, 1))])
    original = deepcopy(raw)
    context = TransitContext(raw, 32637)
    route = context.path([0, 0], [10, 0])
    assert len(route) == 4
    assert sum(math.dist(a, b) for a, b in zip(route, route[1:])) > 10
    assert context.geometry_clear(route)
    assert not context.geometry_clear([[0, 0], [10, 0]])
    assert raw == original
    route.append((99, 99))
    assert len(context.path([0, 0], [10, 0])) == 4
    assert context.path([10, 0], [0, 0]) == list(reversed(route[:-1]))


def test_obstacle_wall_blocks_allowed_area():
    context = TransitContext(scene(allowed=mapping(box(0, 0, 10, 10)), forbidden=[mapping(box(4, -1, 6, 11))]), 32637)
    with pytest.raises(NoPath):
        context.path([1, 5], [9, 5])


def test_clearance_buffer_blocks_narrow_corridor():
    context = TransitContext(scene(allowed=mapping(box(0, 0, 10, 10)), forbidden=[mapping(box(4, 0, 6, 8))]), 32637)
    assert context.path([2, 5], [8, 5])
    with pytest.raises(NoPath):
        context.path([2, 5], [8, 5], buffer_m=1.1)


def test_allowed_concavity_and_hole_detours():
    polygon = Polygon([(0, 0), (10, 0), (10, 3), (3, 3), (3, 10), (0, 10)])
    context = TransitContext(scene(allowed=mapping(polygon)), 32637)
    route = context.path([8, 1], [1, 8])
    assert len(route) >= 3
    assert context.geometry_clear(route)
    polygon = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], holes=[[(4, 4), (6, 4), (6, 6), (4, 6)]])
    context = TransitContext(scene(allowed=mapping(polygon)), 32637)
    assert len(context.path([1, 5], [9, 5])) == 4


def test_terrain_bilinear_bounds_and_absence():
    assert TransitContext(None, 32637).elevation(123, 456) == 0
    context = TransitContext(scene(terrain=dict(origin=[10, 20], cell_size_m=10, values=[[0, 10], [20, 30]])), 32637)
    assert context.has_terrain
    assert context.elevation(15, 25) == 15
    assert context.elevation(20, 30) == 30
    with pytest.raises(ValueError, match="outside DEM"):
        context.elevation(9, 20)


def test_wrong_crs_and_blocked_endpoint():
    with pytest.raises(ValueError, match="CRS"):
        TransitContext(scene(), 32638)
    context = TransitContext(scene(forbidden=[mapping(box(0, 0, 2, 2))]), 32637)
    with pytest.raises(NoPath):
        context.path([1, 1], [5, 5])


def test_feature_buffer_applies():
    feature = dict(type="Feature", geometry=mapping(box(0, 0, 1, 1)), properties={"buffer_m": 2})
    context = TransitContext(scene(forbidden=[feature]), 32637)
    with pytest.raises(NoPath):
        context.path([2, 0], [10, 0])


def test_raster_terrain_uses_containing_pixel_and_rejects_nodata(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    import numpy as np

    path = tmp_path / "dem.tif"
    values = np.array([[10, 20], [30, -9999]], dtype="float32")
    with rasterio.open(path, "w", driver="GTiff", width=2, height=2, count=1,
                       dtype="float32", crs="EPSG:32637",
                       transform=rasterio.transform.from_origin(500000, 6000020, 10, 10),
                       nodata=-9999) as output:
        output.write(values, 1)
    context = TransitContext(scene(terrain=dict(kind="raster", path=str(path))), 32637)
    assert context.elevation(500001, 6000019) == 10
    assert context.elevation(500009, 6000011) == 10
    assert context.elevation(500010, 6000015) == 20
    assert context.sample_step_m == pytest.approx(5)
    with pytest.raises(ValueError, match="missing DEM"):
        context.elevation(500015, 6000005)
    with pytest.raises(ValueError, match="outside DEM"):
        context.elevation(499999, 6000015)


def test_raster_terrain_projects_metric_coordinates(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    import numpy as np
    from pyproj import Transformer

    path = tmp_path / "geographic-dem.tif"
    with rasterio.open(path, "w", driver="GTiff", width=2, height=2, count=1,
                       dtype="float32", crs="EPSG:4326",
                       transform=rasterio.transform.from_origin(37, 56, 0.01, 0.01)) as output:
        output.write(np.array([[100, 110], [120, 130]], dtype="float32"), 1)
    context = TransitContext(scene(terrain=dict(kind="raster", path=str(path))), 32637)
    xy = Transformer.from_crs(4326, 32637, always_xy=True).transform(37.005, 55.995)
    assert context.elevation(*xy) == 100


def test_cached_space_and_whole_path_match_segment_checks():
    context = TransitContext(scene(allowed=mapping(box(0, 0, 20, 20)),
                                   forbidden=[mapping(box(8, 8, 12, 12))]), 32637)
    paths = [[(1, 1), (19, 1), (19, 19)], [(1, 1), (10, 10), (19, 1)],
             [(1, 1), (1, 1)], [(1, 1)], [(1, 1), (-1, 1), (1, 1)],
             [(1, 1), (19, 1), (19, 19), (1, 19), (1, 1)]]
    for margin in (0, 0.5, 2):
        blocked, allowed = context._space(margin)
        assert context._space(margin)[0] is blocked
        assert context._space(margin)[1] is allowed
        for points in paths:
            expected = all(context._clear(a, b, blocked, allowed)
                           for a, b in zip(points, points[1:] or points))
            assert context.geometry_clear(points, margin) == expected


def test_raster_affine_cache_preserves_rotated_cell_boundaries(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    import numpy as np
    from affine import Affine

    path = tmp_path / "rotated.tif"
    affine = Affine(10, 2, 500000, 1, -10, 6000020)
    values = np.array([[10, 20], [30, 40]], dtype="int16")
    with rasterio.open(path, "w", driver="GTiff", width=2, height=2, count=1,
                       dtype="int16", crs="EPSG:32637", transform=affine) as output:
        output.write(values, 1)
    context = TransitContext(scene(terrain=dict(kind="raster", path=str(path))), 32637)
    for col, row in [(0.5, 0.5), (1.5, 0.5), (0.5, 1.5), (1.5, 1.5),
                     (1, 0.5), (1-1e-5, 0.5), (1+1e-5, 0.5)]:
        x, y = affine * (col, row)
        expected_row, expected_col = rasterio.transform.rowcol(affine, x, y)
        assert context.elevation(x, y) == values[expected_row, expected_col]
