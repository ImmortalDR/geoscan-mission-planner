"""Fetch the Moscow Copernicus DSM tile and create traceable UTM crops."""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform_bounds


ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / "sources"
MANIFEST = SOURCES / "copernicus_glo30_manifest.json"
CACHE = Path(os.environ.get(
    "H3_TERRAIN_CACHE", str(Path.home() / ".cache/geoscan_h3/copernicus_glo30")
))
TILE_ID = "Copernicus_DSM_COG_10_N55_00_E037_00_DEM"
TILE_NAME = f"{TILE_ID}.tif"
BASE_URL = "https://copernicus-dem-30m.s3.amazonaws.com"
TILE_URL = f"{BASE_URL}/{TILE_ID}/{TILE_NAME}"
TILE_BYTES = 30_719_198
TILE_SHA256 = "027a31311fd198c54d08d7f6025835fa34b24c3e08ddc43023b485ee63f21320"
DEST_CRS = "EPSG:32637"
NODATA = -9999.0
ATTRIBUTION = (
    "produced using Copernicus WorldDEM-30 \u00a9 DLR e.V. 2010-2014 and "
    "\u00a9 Airbus Defence and Space GmbH 2014-2018 provided under "
    "COPERNICUS by the European Union and ESA; all rights reserved"
)
LICENSE_NOTICE = (
    "The organisations in charge of the Copernicus programme by law or by "
    "delegation do not incur any liability for any use of the Copernicus WorldDEM-30"
)
DOCUMENTS = {
    "tile_metadata": (
        "Copernicus_DSM_10_N55_00_E037_00.xml",
        f"{BASE_URL}/{TILE_ID}/Copernicus_DSM_10_N55_00_E037_00.xml",
        128_000,
    ),
    "aws_readme": ("aws_readme.html", f"{BASE_URL}/readme.html", 128_000),
    "license": (
        "copernicus_dem_licenses.pdf",
        "https://docs.sentinel-hub.com/api/latest/static/files/data/dem/"
        "resources/license/License-COPDEM-30.pdf",
        4_000_000,
    ),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path, limit: int) -> dict:
    """Use verified HTTPS and publish a file only after a complete response."""
    if not url.startswith("https://"):
        raise ValueError("Source URLs must use HTTPS")
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={
        "User-Agent": "Geoscan-H3-dataset/1.0",
        "Accept-Encoding": "identity",
    })
    temporary = None
    try:
        with urlopen(request, timeout=60) as response:
            if response.status != 200 or not response.url.startswith("https://"):
                raise RuntimeError(f"Unexpected source response: {response.status}")
            advertised = response.headers.get("Content-Length")
            if advertised is not None and int(advertised) > limit:
                raise ValueError(f"Source exceeds download limit: {url}")
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=destination.name + ".", delete=False
            ) as output:
                temporary = Path(output.name)
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise ValueError(f"Source exceeds download limit: {url}")
                    output.write(chunk)
            if advertised is not None and size != int(advertised):
                raise ValueError(f"Incomplete download: {url}")
            metadata = {
                "url": url,
                "resolved_url": response.url,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "bytes": size,
                "sha256": sha256_file(temporary),
                "http_etag": response.headers.get("ETag"),
                "http_last_modified": response.headers.get("Last-Modified"),
            }
        temporary.chmod(0o644)
        temporary.replace(destination)
        return metadata
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _verified_existing(path: Path, metadata: dict) -> None:
    if path.stat().st_size != metadata["bytes"]:
        raise ValueError(f"Source byte count changed: {path}")
    if sha256_file(path) != metadata["sha256"]:
        raise ValueError(f"Source SHA-256 mismatch: {path}")


def fetch_sources() -> dict:
    """Fetch/verify the raw tile in CACHE and preserve source documents."""
    previous = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    artifacts = previous.get("artifacts", {})
    paths = {"tile": CACHE / TILE_NAME}
    urls = {"tile": (TILE_URL, 64_000_000)}
    for key, (filename, url, limit) in DOCUMENTS.items():
        paths[key] = SOURCES / filename
        urls[key] = (url, limit)
    refreshed = {}
    for key, path in paths.items():
        old = artifacts.get(key)
        if path.exists() and old is not None and old["url"] == urls[key][0]:
            _verified_existing(path, old)
            refreshed[key] = old
        else:
            refreshed[key] = _download(urls[key][0], path, urls[key][1])
        refreshed[key]["path"] = (
            f"cache://{TILE_NAME}" if key == "tile" else f"sources/{path.name}"
        )
    tile = refreshed["tile"]
    if tile["bytes"] != TILE_BYTES:
        raise ValueError("The upstream tile size differs from the selected source")
    if tile["sha256"] != TILE_SHA256:
        raise ValueError("The upstream tile SHA-256 differs from the pinned source")
    ET.parse(paths["tile_metadata"])
    if not paths["license"].read_bytes().startswith(b"%PDF-"):
        raise ValueError("The licence response is not a PDF")
    with rasterio.open(paths["tile"]) as source:
        if source.crs != rasterio.crs.CRS.from_epsg(4326) or source.count != 1:
            raise ValueError("Unexpected source raster CRS or band count")
        source_raster = {
            "horizontal_crs": source.crs.to_string(),
            "bounds_lonlat": list(source.bounds),
            "width": source.width,
            "height": source.height,
            "dtype": source.dtypes[0],
            "nodata": source.nodata,
        }
    metadata = {
        "schema": "h3.terrain_source.v1",
        "source_id": TILE_ID,
        "dataset": "Copernicus DEM GLO-30 Public, AWS COG 2021 release",
        "surface_type": "DSM",
        "bare_earth": False,
        "vertical_datum": "EGM2008",
        "vertical_crs": "EPSG:3855",
        "height_units": "m",
        "source_raster": source_raster,
        "artifacts": refreshed,
        "license_section": "COP-DEM-GLO-30-F, all three pages",
        "license_reference_url": (
            "https://dataspace.copernicus.eu/explore-data/data-collections/"
            "copernicus-contributing-missions/collections-description/COP-DEM"
        ),
        "derived_attribution": ATTRIBUTION,
        "license_notice": LICENSE_NOTICE,
        "limitations": [
            "DSM includes buildings, infrastructure and vegetation; it is not a DTM.",
            "Pixel spacing does not establish vertical accuracy or obstacle completeness.",
            "Synthetic obstacles and mission constraints are separate dataset layers.",
        ],
    }
    SOURCES.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(metadata, indent=2, ensure_ascii=True) + "\n")
    (SOURCES / "ATTRIBUTION.txt").write_text(
        ATTRIBUTION + "\n\n" + LICENSE_NOTICE + "\n\n"
        "Source licence: copernicus_dem_licenses.pdf\n"
        "The licence obligations also apply to subsequent distribution.\n",
        encoding="utf-8",
    )
    return metadata


def prepare_real_dem(
    destination: Path,
    bounds: tuple[float, float, float, float],
    resolution_m: float = 30,
) -> dict:
    """Write a DSM crop for UTM 37N bounds; preserve EGM2008 heights.

    Bounds are (xmin, ymin, xmax, ymax) in EPSG:32637 metres. The raster
    extends outward to the global resolution grid. Returned provenance
    distinguishes requested bounds from this raster extent.
    """
    if len(bounds) != 4 or not all(math.isfinite(value) for value in bounds):
        raise ValueError("Expected four finite EPSG:32637 bounds")
    if not math.isfinite(resolution_m) or resolution_m <= 0:
        raise ValueError("resolution_m must be finite and positive")
    xmin, ymin, xmax, ymax = map(float, bounds)
    if xmin >= xmax or ymin >= ymax:
        raise ValueError("Bounds must have positive width and height")
    left = math.floor(xmin / resolution_m) * resolution_m
    bottom = math.floor(ymin / resolution_m) * resolution_m
    right = math.ceil(xmax / resolution_m) * resolution_m
    top = math.ceil(ymax / resolution_m) * resolution_m
    width = round((right - left) / resolution_m)
    height = round((top - bottom) / resolution_m)
    if width * height > 25_000_000:
        raise ValueError("Requested crop is too large; maximum is 25 million pixels")
    transform = from_origin(left, top, resolution_m, resolution_m)
    source_metadata = fetch_sources()
    source_sha256 = source_metadata["artifacts"]["tile"]["sha256"]
    heights = np.full((height, width), NODATA, dtype="float32")
    with rasterio.open(CACHE / TILE_NAME) as source:
        west, south, east, north = transform_bounds(
            DEST_CRS, source.crs, left, bottom, right, top, densify_pts=41
        )
        if not (
            source.bounds.left <= west < east <= source.bounds.right
            and source.bounds.bottom <= south < north <= source.bounds.top
        ):
            raise ValueError("Requested crop is not contained in the Moscow source tile")
        reproject(
            source=rasterio.band(source, 1),
            destination=heights,
            src_transform=source.transform,
            src_crs=source.crs,
            src_nodata=source.nodata,
            dst_transform=transform,
            dst_crs=DEST_CRS,
            dst_nodata=NODATA,
            resampling=Resampling.bilinear,
            num_threads=1,
        )
    invalid = ~np.isfinite(heights) | (heights == NODATA)
    if np.any(invalid):
        raise ValueError(f"Crop contains {np.count_nonzero(invalid)} missing pixels")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=destination.name + ".", suffix=".tif", delete=False
    ) as output:
        temporary = Path(output.name)
    try:
        with rasterio.open(
            temporary, "w", driver="GTiff", count=1, dtype="float32",
            width=width, height=height, transform=transform, crs=DEST_CRS,
            nodata=NODATA, compress="deflate", predictor=3,
            tiled=True, blockxsize=256, blockysize=256,
        ) as raster:
            raster.write(heights, 1)
            raster.set_band_description(1, "Surface height above EGM2008 geoid (metres)")
            raster.set_band_unit(1, "m")
            raster.update_tags(
                SOURCE_URL=TILE_URL, SOURCE_SHA256=source_sha256,
                SURFACE_TYPE="DSM", BARE_EARTH="false",
                VERTICAL_DATUM="EGM2008", VERTICAL_CRS="EPSG:3855",
                RESAMPLING="bilinear", ATTRIBUTION=ATTRIBUTION,
                LICENSE_NOTICE=LICENSE_NOTICE,
            )
        with rasterio.open(temporary) as check:
            observed = check.read(1, masked=True)
            if np.ma.getmaskarray(observed).any() or not np.isfinite(observed).all():
                raise ValueError("Written DSM has missing or non-finite pixels")
            if check.crs.to_string() != DEST_CRS:
                raise ValueError("Written DSM CRS does not match the requested UTM CRS")
        temporary.chmod(0o644)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "schema": "h3.terrain_crop.v1",
        "path": destination.name,
        "sha256": sha256_file(destination),
        "bytes": destination.stat().st_size,
        "source_id": TILE_ID,
        "source_url": TILE_URL,
        "source_sha256": source_sha256,
        "source_manifest": "sources/copernicus_glo30_manifest.json",
        "horizontal_crs": DEST_CRS,
        "vertical_datum": "EGM2008",
        "vertical_crs": "EPSG:3855",
        "surface_type": "DSM",
        "bare_earth": False,
        "height_units": "m",
        "requested_bounds_m": list(bounds),
        "raster_bounds_m": [left, bottom, right, top],
        "resolution_m": resolution_m,
        "width": width,
        "height": height,
        "resampling": "bilinear",
        "nodata": NODATA,
        "nodata_pixel_count": 0,
        "minimum_m": float(heights.min()),
        "maximum_m": float(heights.max()),
        "attribution": ATTRIBUTION,
        "license_notice": LICENSE_NOTICE,
        "software": {
            "rasterio": rasterio.__version__, "gdal": rasterio.__gdal_version__,
            "numpy": np.__version__,
        },
    }


if __name__ == "__main__":
    print(json.dumps(fetch_sources(), indent=2, ensure_ascii=True))
