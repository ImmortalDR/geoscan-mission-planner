"""Offline checks for the real, locally served RGB basemaps."""
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest
import rasterio
from rasterio.warp import transform_bounds

DIRECTORY = Path(__file__).resolve().parents[2] / "web/h3/imagery"


@pytest.mark.parametrize("identifier", ["msu", "peredelkino", "orekhovo"])
def test_real_imagery_geometry_provenance_and_pixels(identifier):
    manifest = json.loads((DIRECTORY / "manifest.json").read_text())
    assert manifest["schema"] == "geoscan.h3.imagery.v1"
    assert manifest["attribution"] == "Contains modified Copernicus Sentinel data 2025"
    item = next(row for row in manifest["images"] if row["id"] == identifier)
    assert item["resolution_m"] == 10
    assert item["acquired_at"].startswith("2025-05-28T")
    assert item["missing_pixel_fraction"] == 0
    west, south, east, north = item["bounds"]
    scene_west, scene_south, scene_east, scene_north = item["scene_bounds"]
    assert west < scene_west < scene_east < east
    assert south < scene_south < scene_north < north
    assert item["coordinates"] == [[west, north], [east, north], [east, south], [west, south]]
    geotiff, jpeg = DIRECTORY / f"{identifier}.tif", DIRECTORY / f"{identifier}.jpg"
    assert hashlib.sha256(geotiff.read_bytes()).hexdigest() == item["geotiff_sha256"]
    assert hashlib.sha256(jpeg.read_bytes()).hexdigest() == item["sha256"]
    with rasterio.open(geotiff) as raster:
        assert raster.count == 3 and raster.crs.to_epsg() == 3857
        assert (raster.width, raster.height) == (item["width"], item["height"])
        assert raster.tags()["role"] == "visual_basemap_not_elevation"
        np.testing.assert_allclose(transform_bounds(raster.crs, "EPSG:4326", *raster.bounds), item["bounds"], atol=1e-9)
        assert np.all(raster.dataset_mask() == 255)
        assert min(raster.read().std(axis=(1, 2))) > 5
    with Image.open(jpeg) as image:
        assert image.size == (item["width"], item["height"])
        assert image.mode == "RGB"
    for source in item["sources"]:
        original = json.loads((DIRECTORY / f"{source['id']}.json").read_text())
        assert original["assets"]["visual"]["href"] == source["asset_url"]
        assert original["properties"]["datetime"] == source["acquired_at"]
