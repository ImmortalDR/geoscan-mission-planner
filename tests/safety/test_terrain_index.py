"""Raster indexing optimizations must match the original Rasterio oracle."""

from dataclasses import replace
from pathlib import Path

from affine import Affine
import numpy as np
import pytest
from rasterio.transform import rowcol

from gmp.safety.h3_core import InvalidData, Scene, Segment

pytestmark = pytest.mark.filterwarnings("ignore:Use.*matmul.*:PendingDeprecationWarning")

def make_scene(transform=Affine(30, 0, 400950, 0, -30, 6180720), nodata=-9999.0):
    return Scene(
        directory=Path("/tmp"), metadata={}, mission={}, jobs={}, sites={}, fleet={},
        payloads={}, allowed=None, nfz=None, obstacles=[], temporal=[], project=None,
        dem=np.arange(72, dtype=float).reshape(8, 9), dem_transform=transform,
        dem_nodata=nodata, policy={"terrain_sample_step_m": 10.0},
    )


def rasterio_terrain(scene, xy):
    rows, cols = rowcol(scene.dem_transform, xy[:, 0], xy[:, 1])
    rows, cols = np.asarray(rows), np.asarray(cols)
    if np.any(rows < 0) or np.any(cols < 0) or np.any(rows >= scene.dem.shape[0]) or np.any(cols >= scene.dem.shape[1]):
        raise InvalidData("Trajectory leaves DEM bounds")
    values = scene.dem[rows, cols].astype(float)
    if not np.all(np.isfinite(values)) or (scene.dem_nodata is not None and np.any(values == scene.dem_nodata)):
        raise InvalidData("Trajectory intersects missing DEM values")
    return values


TRANSFORMS = [
    Affine.identity(),
    Affine(30, 0, 400950, 0, -30, 6180720),
    Affine(0.3, 0.04, -42.6, 0.08, -0.2, -13.7),
    Affine.translation(-120.0, 50.0) @ Affine.rotation(33) @ Affine.scale(2.5, -3.2),
]


@pytest.mark.parametrize("transform", TRANSFORMS)
@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_pixel_boundaries_interiors_and_outside_match_rasterio(transform, dtype):
    scene = make_scene(transform)
    pixels = [(col + delta, row + delta) for col in (-1, 0, 1, 4, 8, 9)
              for row in (-1, 0, 1, 4, 7, 8) for delta in (-1e-8, 0, 1e-8, 0.5)]
    points = np.array([transform * point for point in pixels], dtype=dtype)
    valid = []
    for point in points:
        xy = point.reshape(1, 2)
        try:
            expected = rasterio_terrain(scene, xy)
        except InvalidData as exc:
            with pytest.raises(InvalidData, match=str(exc)):
                scene.terrain(xy)
        else:
            np.testing.assert_array_equal(scene.terrain(xy), expected)
            valid.append(point)
    batch = np.array(valid)
    np.testing.assert_array_equal(scene.terrain(batch), rasterio_terrain(scene, batch))
    with pytest.raises(InvalidData, match="bounds"):
        scene.terrain(points)


@pytest.mark.parametrize("value,nodata", [(-9999, -9999), (np.nan, None), (np.inf, None), (-np.inf, None)])
def test_missing_dem_cells_are_rejected_for_scalar_and_batch(value, nodata):
    scene = make_scene(Affine.identity(), nodata)
    scene.dem[2, 3] = value
    for points in ([[3.5, 2.5]], [[0.5, 0.5], [3.5, 2.5]]):
        with pytest.raises(InvalidData, match="missing DEM"):
            scene.terrain(np.array(points))


@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf, 1e300, -1e300])
def test_nonfinite_or_unrepresentable_indices_fail_closed(invalid):
    scene = make_scene(Affine.identity())
    with np.errstate(invalid="ignore"):
        for points in ([[invalid, 0]], [[0, 0], [invalid, invalid]]):
            with pytest.raises(InvalidData, match="bounds"):
                scene.terrain(np.array(points))


def test_inverse_cache_is_invalidated_when_transform_is_replaced():
    scene = make_scene(Affine.identity())
    point = np.array([[1.5, 2.5]])
    np.testing.assert_array_equal(scene.terrain(point), [19])
    scene.dem_transform = Affine.translation(1, 1)
    np.testing.assert_array_equal(scene.terrain(point), rasterio_terrain(scene, point))
    np.testing.assert_array_equal(scene.terrain(point), [9])


@pytest.mark.parametrize("transform", TRANSFORMS)
def test_cell_clearance_extrema_match_rasterio_reference(transform):
    scene = make_scene(transform)
    reference = replace(scene)
    reference.terrain = lambda xy: rasterio_terrain(reference, xy)
    reference._pixel_coordinates = lambda x, y: (~reference.dem_transform) * (x, y)
    for start, end in [((0.5, 0.5), (7.5, 6.5)), ((1, 1), (6, 4)), ((2.5, 2.5), (2.5, 2.5))]:
        a, b = transform * start, transform * end
        segment = Segment(np.array([*a, 200]), np.array([*b, 250]), 0, 60,
                          200, 250, "survey", "job", "uav", "sortie", 0)
        for left, right in ((0, 1), (0.1, 0.8)):
            actual = scene.terrain_clearances(segment, left, right)
            expected = reference.terrain_clearances(segment, left, right)
            np.testing.assert_array_equal(actual, expected)
