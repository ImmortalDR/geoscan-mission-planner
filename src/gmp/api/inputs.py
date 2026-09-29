"""Bounded scene intake, immutable snapshots and real DSM acquisition."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import shutil
import stat
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform_bounds
from shapely.geometry import shape
from shapely.ops import transform, unary_union

from .planner_adapter import load_dataset_module

LAYERS = ("survey_areas", "allowed_airspace", "no_fly_zones", "temporal_airspace", "landing_sites", "obstacles")
DOCUMENTS = ("mission", "fleet", "payload_catalog", "metadata")
INPUT_FILES = {f"{n}.geojson" for n in LAYERS} | {f"{n}.json" for n in DOCUMENTS} | {"dem.tif", "scene.kml"} | {f"{n}.kml" for n in LAYERS}
MAX_UPLOAD = 100 * 1024 * 1024
MAX_JSON = 8 * 1024 * 1024
EMPTY = {"type": "FeatureCollection", "features": []}
TIFF_SIGNATURES = {b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"}


@contextmanager
def open_geotiff(path: Path):
    """Reject GDAL virtual datasets before GDAL can resolve external sources."""
    with path.open("rb") as stream:
        if stream.read(4) not in TIFF_SIGNATURES:
            raise ValueError("DEM must be a TIFF/BigTIFF file; renamed XML/VRT files are not accepted")
    try:
        with rasterio.open(path) as dataset:
            if dataset.driver != "GTiff":
                raise ValueError("DEM must use the GeoTIFF driver")
            yield dataset
    except (rasterio.errors.RasterioError, rasterio.errors.RasterioIOError) as exc:
        raise ValueError("DEM is a damaged or unreadable GeoTIFF") from exc


def read_json(path: Path) -> dict:
    if path.stat().st_size > MAX_JSON:
        raise ValueError(f"{path.name}: JSON exceeds 8 MiB")
    def reject(value):
        raise ValueError(f"Non-finite JSON number: {value}")
    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: expected JSON object")
    return value


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def input_hash(directory: Path) -> str:
    from gmp.safety.h3_gate import INPUT_FILES as REQUIRED_FILES
    hashes = {}
    for name in REQUIRED_FILES:
        path = directory / name
        if path.is_file():
            with path.open("rb") as stream:
                hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    return hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def legacy_input_hash(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.iterdir()):
        if path.name not in INPUT_FILES or not path.is_file():
            continue
        digest.update(path.name.encode() + b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def snapshot(directory: Path) -> dict:
    return {
        "layers": {name: read_json(directory / f"{name}.geojson") if (directory / f"{name}.geojson").exists() else EMPTY for name in LAYERS},
        **{name: read_json(directory / f"{name}.json") if (directory / f"{name}.json").exists() else {} for name in DOCUMENTS},
    }


def check_documents(directory: Path) -> None:
    data = snapshot(directory)
    vertices = 0
    def count_coordinates(value):
        if not isinstance(value, (list, tuple)):
            raise ValueError("Geometry coordinates must be arrays")
        if value and isinstance(value[0], (int, float)):
            if len(value) not in (2, 3) or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in value):
                raise ValueError("Geometry coordinates must contain finite numbers")
            return 1
        return sum(count_coordinates(child) for child in value)
    for name, collection in data["layers"].items():
        if collection.get("type") != "FeatureCollection" or not isinstance(collection.get("features"), list):
            raise ValueError(f"{name}: expected FeatureCollection")
        if len(collection["features"]) > 500:
            raise ValueError(f"{name}: maximum 500 features")
        seen = set()
        for feature in collection["features"]:
            if not isinstance(feature, dict) or feature.get("type") != "Feature" or not isinstance(feature.get("properties"), dict) or not isinstance(feature.get("geometry"), dict):
                raise ValueError(f"{name}: invalid feature/properties")
            identifier = feature["properties"].get("id")
            if not isinstance(identifier, str) or not identifier or identifier in seen or len(identifier) > 128:
                raise ValueError(f"{name}: feature IDs must be unique nonempty strings")
            seen.add(identifier)
            count = count_coordinates(feature.get("geometry", {}).get("coordinates"))
            vertices += count
            if count > 5000 or vertices > 20000:
                raise ValueError("Geometry exceeds 5000 vertices per feature or 20000 per scene")
            geom = shape(feature["geometry"])
            allowed_types = ("Point",) if name == "landing_sites" else ("Polygon", "MultiPolygon")
            if name == "obstacles":
                allowed_types += ("Point", "LineString")
            if geom.geom_type not in allowed_types or geom.is_empty or not geom.is_valid:
                raise ValueError(f"{name}/{identifier}: invalid geometry")
            xmin, ymin, xmax, ymax = geom.bounds
            if not all(math.isfinite(x) for x in geom.bounds) or not (-180 <= xmin <= xmax <= 180 and -85 <= ymin <= ymax <= 85):
                raise ValueError(f"{name}/{identifier}: coordinates must be longitude/latitude")
    fleet = data["fleet"].get("uavs", [])
    if not isinstance(fleet, list) or len(fleet) > 10:
        raise ValueError("Fleet supports at most 10 UAVs")
    numeric_limits = {
        "ground_speed_kmh": (0.1, 500), "operational_endurance_min": (0.1, 1440),
        "max_wind_ms": (0, 50), "energy_reserve_fraction": (0.1, 0.5),
        "horizontal_separation_m": (1, 2000), "vertical_separation_m": (1, 1000),
        "turnaround_buffer_m": (0, 2000), "takeoff_time_s": (0, 3600),
        "landing_time_s": (0, 3600), "service_time_s": (0, 86400),
    }
    ids = set()
    for uav in fleet:
        if not isinstance(uav, dict):
            raise ValueError("UAV entries must be objects")
        if uav.get("id") in ids or not isinstance(uav.get("id"), str) or not uav["id"]:
            raise ValueError("UAV IDs must be unique nonempty strings")
        ids.add(uav["id"])
        if uav.get("model") not in ("geoscan_201", "geoscan_401", "geoscan_701", "geoscan_801", "geoscan_gemini", "gemini"):
            raise ValueError("Unsupported UAV model")
        if uav.get("class") not in ("fixed_wing", "fixedwing", "airplane", "copter", "multirotor"):
            raise ValueError("Unsupported UAV flight class")
        for name, (low, high) in numeric_limits.items():
            value = uav.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{uav['id']}/{name}: expected value in [{low}, {high}]")
    mission = data["mission"]
    if any(x not in ("makespan", "total_flight") for x in mission.get("objectives", [])):
        raise ValueError("Unsupported objective")
    for field in ("preparation_time_s", "data_download_time_s"):
        value = mission.get(field, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 86400:
            raise ValueError(f"{field}: expected seconds in [0, 86400]")
    policy = mission.get("validation_policy", {})
    if not isinstance(policy, dict) or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in policy.values()):
        raise ValueError("Validation policy values must be finite nonnegative numbers")
    if policy.get("coverage_tolerance_fraction", .001) > .001 or policy.get("min_agl_m", 40) < 40 or policy.get("altitude_tolerance_m", 3) > 3 or policy.get("terrain_sample_step_m", 20) > 20:
        raise ValueError("Safety validation policy cannot be weakened")
    if policy.get("takeoff_landing_corridor_radius_m", 100) > 100:
        raise ValueError("Takeoff/landing corridor radius cannot exceed 100 metres")
    if mission.get("require_complete_coverage") is not True:
        raise ValueError("Complete coverage must be required")
    generation = data["metadata"].get("reference_generation", {})
    angle = generation.get("angle_step_deg", 15) if isinstance(generation, dict) else None
    if isinstance(angle, bool) or not isinstance(angle, (int, float)) or not math.isfinite(angle) or not 5 <= angle <= 180:
        raise ValueError("Coverage angle step must be between 5 and 180 degrees")
    profiles = data["payload_catalog"].get("payload_profiles", [])
    if not isinstance(profiles, list) or len(profiles) > 20:
        raise ValueError("Payload catalog supports at most 20 profiles")
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ValueError("Payload profiles must be objects")
        for key in ("front_overlap", "side_overlap"):
            value = profile.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= .95:
                raise ValueError(f"{key}: hosted calculation supports overlap between 0 and 0.95")
        if profile.get("line_spacing_m") is not None and not 5 <= float(profile["line_spacing_m"]) <= 10000:
            raise ValueError("Hosted line spacing must be between 5 and 10000 metres")
    crs = CRS.from_user_input(data["metadata"].get("metric_crs", "EPSG:32637"))
    if not crs.is_projected or not crs.axis_info or crs.axis_info[0].unit_name != "metre":
        raise ValueError("metric_crs must be a projected CRS in metres")
    jobs = [shape(f["geometry"]) for f in data["layers"]["survey_areas"]["features"]]
    if jobs:
        convert = Transformer.from_crs(4326, crs, always_xy=True).transform
        if transform(convert, unary_union(jobs)).area > 200_000_000:
            raise ValueError("Maximum required survey area is 200 square kilometres")
    dem = directory / "dem.tif"
    if dem.exists():
        with open_geotiff(dem) as ds:
            if ds.count != 1 or ds.width * ds.height > 16_000_000 or not ds.crs:
                raise ValueError("DEM must have one band, a CRS and at most 16 million pixels")


def validate_input(directory: Path, dataset: Path) -> dict:
    errors, warnings = [], []
    try:
        check_documents(directory)
        from gmp.safety.h3_core import load_scene
        load_scene(directory)
        if not terrain_covers(directory):
            errors.append("The elevation raster does not cover all input layers; request automatic DSM or upload a GeoTIFF.")
    except Exception as exc:
        errors.append(str(exc))
    data = snapshot(directory)
    import_warnings = data.get("metadata", {}).get("import_warnings", [])
    if isinstance(import_warnings, list):
        warnings.extend(value[:1000] for value in import_warnings[:20] if isinstance(value, str))
    if data.get("metadata", {}).get("user_overrides"):
        warnings.append("UAV characteristics contain user assumptions, not manufacturer-certified operating limits.")
    warnings.append("Model validation is not a flight permit or proof of real-world operational safety.")
    return {"valid": not errors, "errors": errors, "warnings": warnings, "terrain_available": (directory / "dem.tif").exists()}


def _geographic_bounds(directory: Path) -> tuple:
    geometries = [shape(f["geometry"]) for collection in snapshot(directory)["layers"].values() for f in collection["features"]]
    if not geometries:
        raise ValueError("Draw a survey area and sites before downloading elevation")
    return unary_union(geometries).bounds


def terrain_covers(directory: Path) -> bool:
    if not (directory / "dem.tif").exists():
        return False
    try:
        geometries = [shape(f["geometry"]) for collection in snapshot(directory)["layers"].values() for f in collection["features"]]
        if not geometries:
            return False
        with open_geotiff(directory / "dem.tif") as ds:
            project = Transformer.from_crs(4326, ds.crs, always_xy=True).transform
            west, south, east, north = transform(project, unary_union(geometries)).bounds
            return ds.bounds.left <= west and ds.bounds.bottom <= south and ds.bounds.right >= east and ds.bounds.top >= north
    except ValueError:
        return False


def unpack_upload(entries: list[tuple[str, bytes]], destination: Path, uploaded_names: set | None = None) -> list[str]:
    """Normalize uploaded geometry; explicit GeoJSON wins over KML aliases."""
    total, seen, warnings = 0, set(), []
    files = []
    for name, content in entries:
        if name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if len(archive.infolist()) > 40:
                    raise ValueError("ZIP has too many entries")
                for member in archive.infolist():
                    path = PurePosixPath(member.filename)
                    if path.is_absolute() or ".." in path.parts or "\\" in member.filename or stat.S_ISLNK(member.external_attr >> 16):
                        raise ValueError("Unsafe ZIP member")
                    if member.is_dir():
                        continue
                    if member.file_size > MAX_UPLOAD or (member.compress_size and member.file_size / member.compress_size > 1000):
                        raise ValueError("ZIP member exceeds compression/size limit")
                    total += member.file_size
                    if total > MAX_UPLOAD:
                        raise ValueError("Expanded upload exceeds 100 MiB")
                    files.append((path.name, archive.read(member)))
        else:
            if Path(name).name != name or "\\" in name:
                raise ValueError("Upload filenames cannot contain paths")
            total += len(content)
            if total > MAX_UPLOAD:
                raise ValueError("Upload exceeds 100 MiB")
            files.append((name, content))
    for name, content in files:
        if name not in INPUT_FILES or name in seen:
            raise ValueError(f"Unsupported or duplicate input filename: {name}")
        seen.add(name)
        (destination / name).write_bytes(content)
    for layer in LAYERS:
        canonical, named = f"{layer}.geojson", f"{layer}.kml"
        alias = "scene.kml" if layer == "survey_areas" else None
        uploaded_kml = [name for name in (named, alias) if name and name in seen]
        if canonical in seen:
            if uploaded_kml:
                warnings.append(f"{canonical} takes precedence over {', '.join(uploaded_kml)}.")
            continue
        if not uploaded_kml:
            continue
        selected = uploaded_kml[0]
        if len(uploaded_kml) > 1:
            warnings.append(f"{selected} takes precedence over scene.kml for survey areas.")
        previous = read_json(destination / canonical) if (destination / canonical).exists() else EMPTY
        collection = parse_kml((destination / selected).read_bytes(), layer)
        if selected == "scene.kml":
            polygons = [f for f in collection["features"] if f["geometry"]["type"] in ("Polygon", "MultiPolygon")]
            if not polygons:
                raise ValueError("scene.kml must contain survey polygons")
            if len(polygons) != len(collection["features"]):
                warnings.append("scene.kml imports survey polygons only; other geometries do not replace landing sites.")
            collection["features"] = polygons
        properties = {f["properties"].get("id"): f["properties"] for f in previous.get("features", []) if isinstance(f, dict) and isinstance(f.get("properties"), dict)}
        for feature in collection["features"]:
            feature["properties"] = {**properties.get(feature["properties"]["id"], {}), **feature["properties"]}
        write_json(destination / canonical, collection)
    for layer in LAYERS:
        if not (destination / f"{layer}.geojson").exists():
            write_json(destination / f"{layer}.geojson", EMPTY)
    for name in DOCUMENTS:
        if not (destination / f"{name}.json").exists():
            raise ValueError(f"Missing {name}.json; upload a complete scene input package")
    if uploaded_names is not None:
        uploaded_names.update(seen)
    return warnings


def parse_kml(content: bytes, layer: str) -> dict:
    if len(content) > MAX_JSON or b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("KML size or XML entity declaration is not permitted")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"Malformed KML XML: {exc}") from exc
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    features = []
    def coordinates(node):
        values = node.findtext("coordinates", "").split()
        return [[float(x) for x in value.split(",")[:2]] for value in values]
    for index, placemark in enumerate(root.iter("Placemark")):
        props = {"id": placemark.findtext("name") or f"{layer}_{index + 1}"}
        for data in placemark.iter("Data"):
            if data.get("name"):
                name, value = data.get("name"), data.findtext("value", "")
                if name == "candidate":
                    if value.lower() not in ("true", "false", "1", "0"):
                        raise ValueError("KML candidate must be true or false")
                    value = value.lower() in ("true", "1")
                elif name in ("height_m", "horizontal_buffer_m", "vertical_buffer_m", "min_alt_m", "max_alt_m"):
                    value = float(value)
                props[name] = value
        polygons = []
        for polygon in placemark.iter("Polygon"):
            exterior = polygon.find("outerBoundaryIs/LinearRing")
            if exterior is None:
                raise ValueError("KML polygon lacks an exterior ring")
            polygons.append([coordinates(exterior)] + [coordinates(ring) for ring in polygon.findall("innerBoundaryIs/LinearRing")])
        points = list(placemark.iter("Point"))
        if polygons:
            geometry = {"type": "Polygon", "coordinates": polygons[0]} if len(polygons) == 1 else {"type": "MultiPolygon", "coordinates": polygons}
        elif len(points) == 1:
            point_coordinates = coordinates(points[0])
            if len(point_coordinates) != 1:
                raise ValueError("KML Point must contain exactly one coordinate")
            geometry = {"type": "Point", "coordinates": point_coordinates[0]}
        else:
            raise ValueError("KML placemark must contain polygons or one point")
        features.append({"type": "Feature", "properties": props, "geometry": geometry})
    return {"type": "FeatureCollection", "features": features}


def create_flat_terrain(directory: Path) -> dict:
    """Create an explicitly synthetic zero-height DEM, without network access."""
    bounds = _geographic_bounds(directory)
    lon, lat = (bounds[0]+bounds[2])/2, (bounds[1]+bounds[3])/2
    epsg = (32600 if lat >= 0 else 32700) + min(60, int((lon+180)/6)+1)
    xmin, ymin, xmax, ymax = transform_bounds(4326, epsg, *bounds, densify_pts=21)
    left, bottom = math.floor((xmin-1000)/30)*30, math.floor((ymin-1000)/30)*30
    right, top = math.ceil((xmax+1000)/30)*30, math.ceil((ymax+1000)/30)*30
    width, height = round((right-left)/30), round((top-bottom)/30)
    if width*height > 2_000_000:
        raise ValueError("Flat DEM extent exceeds 2 million 30-metre pixels")
    with rasterio.open(directory / "dem.tif", "w", driver="GTiff", width=width, height=height,
                       count=1, dtype="float32", crs=f"EPSG:{epsg}",
                       transform=from_origin(left, top, 30, 30), compress="deflate") as output:
        output.write(np.zeros((height,width), dtype="float32"), 1)
    metadata = read_json(directory / "metadata.json")
    metadata["real_elevation"] = False
    metadata["terrain"] = {"kind":"synthetic_flat_surface", "height_m":0, "resolution_m":30,
                           "selection":"user_requested_flat", "limitations":["Real terrain relief is not included"]}
    write_json(directory / "metadata.json", metadata)
    return metadata["terrain"]


def acquire_terrain(directory: Path, cache: Path) -> dict:
    """Fetch bounded public Copernicus tiles. User-supplied network URLs are never used."""
    bounds = _geographic_bounds(directory)
    center_lon, center_lat = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
    epsg = (32600 if center_lat >= 0 else 32700) + min(60, int((center_lon + 180) / 6) + 1)
    metric = f"EPSG:{epsg}"
    xmin, ymin, xmax, ymax = transform_bounds(4326, metric, *bounds, densify_pts=21)
    left, bottom = math.floor((xmin - 1000) / 30) * 30, math.floor((ymin - 1000) / 30) * 30
    right, top = math.ceil((xmax + 1000) / 30) * 30, math.ceil((ymax + 1000) / 30) * 30
    width, height = round((right - left) / 30), round((top - bottom) / 30)
    if width * height > 2_000_000:
        raise ValueError("Automatic DSM extent exceeds 2 million 30-metre pixels")
    west, south, east, north = transform_bounds(metric, 4326, left, bottom, right, top, densify_pts=21)
    tiles = [(lat, lon) for lat in range(math.floor(south), math.floor(north) + 1) for lon in range(math.floor(west), math.floor(east) + 1)]
    if len(tiles) > 4:
        raise ValueError("Automatic DSM supports at most four source tiles per scene")
    cache.mkdir(parents=True, exist_ok=True)
    sources, source_paths = [], []
    for lat, lon in tiles:
        tile = f"Copernicus_DSM_COG_10_{'N' if lat >= 0 else 'S'}{abs(lat):02d}_00_{'E' if lon >= 0 else 'W'}{abs(lon):03d}_00_DEM"
        url = f"https://copernicus-dem-30m.s3.amazonaws.com/{tile}/{tile}.tif"
        local, manifest = cache / f"{tile}.tif", cache / f"{tile}.json"
        if not (local.exists() and manifest.exists()):
            if os.environ.get("GMP_OFFLINE") == "1":
                raise ValueError("Offline mode requires an uploaded DSM or a preloaded terrain cache")
            temporary = local.with_suffix(".download")
            try:
                with urlopen(Request(url, headers={"User-Agent": "Geoscan-H3/1.0", "Accept-Encoding": "identity"}), timeout=60) as response:
                    if response.status != 200 or urlparse(response.url).hostname != "copernicus-dem-30m.s3.amazonaws.com" or urlparse(response.url).scheme != "https":
                        raise ValueError("Unexpected DSM provider response")
                    digest, total = hashlib.sha256(), 0
                    with temporary.open("wb") as stream:
                        while chunk := response.read(1024 * 1024):
                            total += len(chunk)
                            if total > 128 * 1024 * 1024:
                                raise ValueError("DSM source exceeds 128 MiB")
                            stream.write(chunk)
                            digest.update(chunk)
                    from .workspace_store import utcnow
                    provenance = {"url": url, "sha256": digest.hexdigest(), "bytes": total, "retrieved_at": utcnow(), "etag": response.headers.get("ETag")}
                with rasterio.open(temporary) as ds:
                    if ds.crs != rasterio.crs.CRS.from_epsg(4326) or ds.count != 1:
                        raise ValueError("Unexpected DSM source raster")
                temporary.replace(local)
                write_json(manifest, provenance)
            finally:
                temporary.unlink(missing_ok=True)
        provenance = read_json(manifest)
        with local.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != provenance["sha256"]:
                raise ValueError("Cached DSM checksum mismatch; remove cache entry and retry")
        source_paths.append(local)
        sources.append(provenance)
    raster_transform = from_origin(left, top, 30, 30)
    heights = np.full((height, width), -9999, dtype="float32")
    for source_path in source_paths:
        with rasterio.open(source_path) as source:
            reproject(source=rasterio.band(source, 1), destination=heights, src_transform=source.transform, src_crs=source.crs, src_nodata=source.nodata, dst_transform=raster_transform, dst_crs=metric, dst_nodata=-9999, resampling=Resampling.bilinear, init_dest_nodata=False, num_threads=1)
    if np.any(~np.isfinite(heights) | (heights == -9999)):
        raise ValueError("DSM source has missing pixels; upload a complete GeoTIFF")
    with tempfile.NamedTemporaryFile(dir=directory, suffix=".tif", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with rasterio.open(temporary, "w", driver="GTiff", count=1, dtype="float32", width=width, height=height, transform=raster_transform, crs=metric, nodata=-9999, compress="deflate", predictor=3) as output:
            output.write(heights, 1)
            output.update_tags(SURFACE_TYPE="DSM", VERTICAL_DATUM="EGM2008", SOURCE="Copernicus GLO-30 public")
        temporary.replace(directory / "dem.tif")
    finally:
        temporary.unlink(missing_ok=True)
    metadata = read_json(directory / "metadata.json")
    metadata.update(metric_crs=metric, height_reference="EGM2008", surface_model="Copernicus DSM", real_elevation=True)
    metadata["terrain"] = {"kind": "real_dsm", "surface_type": "DSM", "bare_earth": False, "vertical_datum": "EGM2008", "resolution_m": 30, "sources": sources, "attribution": "produced using Copernicus WorldDEM-30 \u00a9 DLR e.V. 2010-2014 and \u00a9 Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved", "license_url": "https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM", "limitations": ["DSM includes vegetation and structures, not bare earth", "Not a current obstacle inventory or flight authorization"]}
    write_json(directory / "metadata.json", metadata)
    return metadata["terrain"]
