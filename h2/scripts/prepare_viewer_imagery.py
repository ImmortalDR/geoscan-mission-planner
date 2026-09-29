#!/usr/bin/env python3
"""Prepare a real Sentinel RGB background in the viewer's existing metric CRS."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import urllib.request

import numpy as np
from PIL import Image
import rasterio
from rasterio.windows import from_bounds

ITEM_URL = "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/S2C_37UDB_20250528_0_L2A"
ATTRIBUTION = "Contains modified Copernicus Sentinel data 2025"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (args.output / "manifest.json").exists():
        parser.error("Existing imagery is not overwritten; choose a new output directory")
    points, fixtures = [], []
    for path in sorted(args.fixtures.glob("*.bundle.json")):
        content = path.read_bytes()
        bundle = json.loads(content)
        if bundle["crs"]["metric_epsg"] != 32637:
            parser.error("This bounded Moscow imagery preparation supports only EPSG:32637")
        points.extend((site["x"], site["y"]) for site in bundle["sites"])
        points.extend(point[:2] for task in bundle["tasks"] for line in task["transects"] for point in line["coords"])
        fixtures.append({"path": path.name, "sha256": hashlib.sha256(content).hexdigest()})
    if not points:
        parser.error("No task or site coordinates found")
    xs, ys = zip(*points)
    bounds = (math.floor((min(xs) - 1000) / 10) * 10, math.floor((min(ys) - 1000) / 10) * 10,
              math.ceil((max(xs) + 1000) / 10) * 10, math.ceil((max(ys) + 1000) / 10) * 10)
    if (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) / 100 > 8_000_000:
        parser.error("The fixture extent exceeds the bounded demo crop size")
    request = urllib.request.Request(ITEM_URL, headers={"User-Agent": "Geoscan-Viewer-Imagery/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        item = json.load(response)
    asset = item["assets"]["visual"]["href"]
    if not asset.startswith("https://sentinel-cogs.s3.us-west-2.amazonaws.com/"):
        raise ValueError("Unexpected Sentinel RGB source host")
    print(f"Reading the RGB crop for {len(fixtures)} unchanged bundles: {bounds}", flush=True)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                      GDAL_HTTP_TIMEOUT=60, GDAL_HTTP_MAX_RETRY=2, GDAL_HTTP_RETRY_DELAY=1):
        with rasterio.open(asset) as source:
            if source.crs.to_epsg() != 32637 or source.count != 3:
                raise ValueError("Unexpected source projection or RGB bands")
            if not (source.bounds.left <= bounds[0] < bounds[2] <= source.bounds.right and
                    source.bounds.bottom <= bounds[1] < bounds[3] <= source.bounds.top):
                raise ValueError("The Sentinel scene does not cover all fixture coordinates")
            window = from_bounds(*bounds, transform=source.transform).round_offsets().round_lengths()
            pixels = source.read(window=window, masked=True)
            if np.all(np.ma.getmaskarray(pixels), axis=0).any():
                raise ValueError("The downloaded background has missing pixels")
            transform = source.window_transform(window)
            rgb = pixels.data
    args.output.mkdir(parents=True, exist_ok=True)
    height, width = rgb.shape[1:]
    raster_path = args.output / "moscow.tif"
    with rasterio.open(raster_path, "w", driver="GTiff", width=width, height=height,
                       count=3, dtype="uint8", crs="EPSG:32637", transform=transform,
                       compress="deflate", predictor=2, tiled=True, photometric="RGB") as target:
        target.write(rgb)
        target.update_tags(attribution=ATTRIBUTION, role="visual_basemap_not_elevation")
        actual_bounds = list(target.bounds)
    image_path = args.output / "moscow.jpg"
    Image.fromarray(np.moveaxis(rgb, 0, -1)).save(image_path, quality=92, subsampling=0)
    manifest = {"schema": "geoscan.viewer.imagery.v1", "attribution": ATTRIBUTION,
                "source_url": "https://registry.opendata.aws/sentinel-2-l2a-cogs/",
                "license_url": "https://cds.climate.copernicus.eu/licences/ec-sentinel",
                "fetched_at": datetime.now(timezone.utc).isoformat(), "fixtures": fixtures,
                "images": [{"id": "moscow", "url": "/imagery/moscow.jpg", "geotiff_url": "/imagery/moscow.tif",
                            "metric_epsg": 32637, "bounds_xy": actual_bounds, "width": width, "height": height,
                            "acquired_at": item["properties"]["datetime"], "resolution_m": 10,
                            "source_url": ITEM_URL, "asset_url": asset,
                            "tile_cloud_cover_percent": item["properties"]["eo:cloud_cover"],
                            "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                            "geotiff_sha256": hashlib.sha256(raster_path.read_bytes()).hexdigest(),
                            "missing_pixel_fraction": 0}]}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (args.output / "source.json").write_text(json.dumps(item, indent=2) + "\n")
    print(f"Saved {width}x{height} RGB pixels in EPSG:32637; no bundle was modified")


if __name__ == "__main__":
    main()
