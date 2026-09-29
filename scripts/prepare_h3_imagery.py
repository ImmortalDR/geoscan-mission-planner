#!/usr/bin/env python3
"""Download bounded, georeferenced Sentinel-2 RGB crops for the H3 demo maps."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import httpx
import numpy as np
from PIL import Image
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from shapely.geometry import box, shape
from shapely.ops import unary_union

STAC = "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/"
ITEMS = ("S2C_37UDB_20250528_0_L2A", "S2C_37UCB_20250528_0_L2A")
SCENES = (("msu", "S01_msu_100km2", "МГУ и окрестности"),
          ("peredelkino", "S02_peredelkino", "Переделкино"),
          ("orekhovo", "S03_orekhovo_domodedovskaya", "Орехово - Домодедовская"))
ATTRIBUTION = "Contains modified Copernicus Sentinel data 2025"
LICENSE = "https://cds.climate.copernicus.eu/licences/ec-sentinel"
SOURCE = "https://registry.opendata.aws/sentinel-2-l2a-cogs/"


def scene_bounds(directory: Path):
    geometries = []
    for path in sorted(directory.glob("*.geojson")):
        data = json.loads(path.read_text())
        geometries.extend(shape(feature["geometry"]) for feature in data["features"])
    if not geometries:
        raise ValueError(f"No scene geometry in {directory}")
    return unary_union(geometries).bounds


def image_grid(bounds, buffer_m=2000):
    latitude = (bounds[1] + bounds[3]) / 2
    scale = 1 / math.cos(math.radians(latitude))
    west, south, east, north = transform_bounds("EPSG:4326", "EPSG:3857", *bounds)
    extent = (west - buffer_m * scale, south - buffer_m * scale,
              east + buffer_m * scale, north + buffer_m * scale)
    width = math.ceil((extent[2] - extent[0]) / (10 * scale))
    height = math.ceil((extent[3] - extent[1]) / (10 * scale))
    if max(width, height) > 4096 or width * height > 8_000_000:
        raise ValueError("Crop exceeds the bounded demo imagery size")
    return extent, width, height, from_bounds(*extent, width, height)


def sha256(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / "manifest.json").exists():
        parser.error("Choose a new output directory; existing imagery is not overwritten")
    fetched_at = datetime.now(timezone.utc).isoformat()
    with httpx.Client(timeout=60, headers={"User-Agent": "Geoscan-H3-Imagery/1.0"}) as client:
        items = []
        for item_id in ITEMS:
            response = client.get(STAC + item_id)
            response.raise_for_status()
            item = response.json()
            if item["id"] != item_id or not item["assets"]["visual"]["href"].startswith(
                    "https://sentinel-cogs.s3.us-west-2.amazonaws.com/"):
                raise ValueError("Unexpected Sentinel metadata or RGB asset host")
            items.append(item)
            (args.output / f"{item_id}.json").write_text(json.dumps(item, indent=2) + "\n")

    images = []
    with ExitStack() as stack:
        stack.enter_context(rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_TIMEOUT=60,
                            GDAL_HTTP_MAX_RETRY=2, GDAL_HTTP_RETRY_DELAY=1))
        sources = [stack.enter_context(rasterio.open(item["assets"]["visual"]["href"])) for item in items]
        for identifier, scenario, name in SCENES:
            bounds = scene_bounds(args.dataset / "scenarios" / scenario / "input")
            extent, width, height, transform = image_grid(bounds)
            geographic = transform_bounds("EPSG:3857", "EPSG:4326", *extent)
            rgb = np.zeros((3, height, width), dtype="uint8")
            valid = np.zeros((height, width), dtype=bool)
            used = []
            for item, source in zip(items, sources):
                if not shape(item["geometry"]).intersects(box(*geographic)):
                    continue
                print(f"Reading {identifier}: {item['id']} ({width}x{height})", flush=True)
                # A target-sized VRT reads only the intersecting COG byte ranges.
                with WarpedVRT(source, crs="EPSG:3857", transform=transform,
                               width=width, height=height, src_nodata=0, nodata=0,
                               resampling=Resampling.bilinear) as vrt:
                    pixels = vrt.read(indexes=[1, 2, 3], masked=True)
                available = ~np.all(np.ma.getmaskarray(pixels), axis=0) & ~valid
                if not np.any(available):
                    continue
                rgb[:, available] = pixels.data[:, available]
                valid |= available
                used.append({"id": item["id"], "source_url": STAC + item["id"],
                             "asset_url": item["assets"]["visual"]["href"],
                             "acquired_at": item["properties"]["datetime"],
                             "tile_cloud_cover_percent": item["properties"]["eo:cloud_cover"]})
                if valid.all():
                    break
            if not valid.all():
                raise ValueError(f"{identifier}: {(~valid).mean():.3%} missing pixels; refusing partial imagery")
            if min(float(band.std()) for band in rgb) < 5:
                raise ValueError(f"{identifier}: unexpectedly blank RGB crop")

            geotiff = args.output / f"{identifier}.tif"
            with rasterio.open(geotiff, "w", driver="GTiff", count=3, dtype="uint8",
                               width=width, height=height, crs="EPSG:3857", transform=transform,
                               compress="deflate", predictor=2, tiled=True, photometric="RGB") as output:
                output.write(rgb)
                output.update_tags(attribution=ATTRIBUTION, source=SOURCE,
                                   acquired_at=used[0]["acquired_at"], role="visual_basemap_not_elevation")
            jpeg = args.output / f"{identifier}.jpg"
            Image.fromarray(np.moveaxis(rgb, 0, -1)).save(jpeg, quality=92, subsampling=0)
            west, south, east, north = geographic
            images.append({"id": identifier, "name": name, "scenario_id": scenario,
                           "url": f"/static/imagery/{jpeg.name}",
                           "geotiff_url": f"/static/imagery/{geotiff.name}",
                           "coordinates": [[west, north], [east, north], [east, south], [west, south]],
                           "bounds": list(geographic), "scene_bounds": list(bounds),
                           "acquired_at": used[0]["acquired_at"], "resolution_m": 10,
                           "crs": "EPSG:3857", "width": width, "height": height,
                           "source_url": used[0]["source_url"], "sources": used,
                           "sha256": sha256(jpeg), "geotiff_sha256": sha256(geotiff),
                           "missing_pixel_fraction": 0, "rgb_channel_std": rgb.std(axis=(1, 2)).tolist()})
            print(f"Saved {identifier}: JPEG {jpeg.stat().st_size} bytes, GeoTIFF {geotiff.stat().st_size} bytes", flush=True)
    manifest = {"schema": "geoscan.h3.imagery.v1", "attribution": ATTRIBUTION,
                "source_url": SOURCE, "license_url": LICENSE, "fetched_at": fetched_at,
                "role": "visual_basemap_only_not_dem_or_obstacle_inventory", "images": images}
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"Prepared {len(images)} georeferenced basemaps; input dataset unchanged")


if __name__ == "__main__":
    main()
