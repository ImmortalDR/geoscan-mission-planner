"""Vendored H3 checker; source: h3/data/h3check.py (2026-09-20).

Independent of H1/H2. Changes here require runtime mutation tests; the immutable
dataset checker remains the original regression oracle.
"""

from __future__ import annotations

from .trajectory_geometry import Segment, interval_linear, spatial_intervals, physical_swath, conflict

import argparse
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from pyproj.exceptions import ProjError
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.errors import ShapelyError
from shapely.ops import transform, unary_union


MODEL_SCOPE = {
    "model_scope": "Independent validation of the explicit demonstration trajectory model; not an operational flight certificate",
    "not_checked": ["discrete_photo_front_overlap", "side_overlap_quality", "aircraft_dynamics",
                    "off_track_sensor_terrain_occlusion", "real_operational_permissions", "global_optimality"],
}


class InvalidData(ValueError):
    pass


def required(obj: dict, key: str, context: str) -> Any:
    if not isinstance(obj, dict) or key not in obj:
        raise InvalidData(f"{context}: missing field {key}")
    return obj[key]


def number(value: Any, context: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidData(f"{context}: expected a finite number")
    if not math.isfinite(value) or (minimum is not None and value < minimum):
        raise InvalidData(f"{context}: invalid numeric value {value}")
    return float(value)


def identifier(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidData(f"{context}: expected a nonempty string")
    return value


def timestamp(value: Any, context: str) -> float:
    if not isinstance(value, str):
        raise InvalidData(f"{context}: expected ISO8601 timestamp with timezone")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidData(f"{context}: invalid timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise InvalidData(f"{context}: timestamp has no timezone")
    return dt.timestamp()


def finite_tree(value: Any, context: str = "result") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise InvalidData(f"{context}: nonfinite number")
    if isinstance(value, dict):
        for key, child in value.items():
            finite_tree(child, f"{context}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            finite_tree(child, f"{context}[{index}]")


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    finite_tree(data, path.name)
    return data


@dataclass
class Scene:
    directory: Path
    metadata: dict
    mission: dict
    jobs: dict
    sites: dict
    fleet: dict
    payloads: dict
    allowed: Any
    nfz: Any
    obstacles: list
    temporal: list
    project: Transformer
    dem: np.ndarray
    dem_transform: Any
    dem_nodata: float | None
    policy: dict
    issues: list = field(default_factory=list)
    _inverse_source: Any = field(default=None, init=False, repr=False)
    _inverse_coefficients: tuple | None = field(default=None, init=False, repr=False)

    def _pixel_coordinates(self, x, y):
        if self._inverse_source is not self.dem_transform:
            inverse = ~self.dem_transform
            self._inverse_coefficients = (inverse.a, inverse.b, inverse.c,
                                          inverse.d, inverse.e, inverse.f)
            self._inverse_source = self.dem_transform
        a, b, c, d, e, f = self._inverse_coefficients
        # Preserve AffineTransformer's operation order, including at pixel edges.
        return x * a + y * b + c, x * d + y * e + f

    def terrain(self, xy: np.ndarray) -> np.ndarray:
        xy = np.asarray(xy, dtype=float)
        if len(xy) == 1:
            col, row = self._pixel_coordinates(xy[0, 0], xy[0, 1])
            if not math.isfinite(row) or not math.isfinite(col):
                raise InvalidData("Trajectory leaves DEM bounds")
            row, col = math.floor(row), math.floor(col)
            if row < 0 or col < 0 or row >= self.dem.shape[0] or col >= self.dem.shape[1]:
                raise InvalidData("Trajectory leaves DEM bounds")
            value = float(self.dem[row, col])
            if not math.isfinite(value) or (self.dem_nodata is not None and value == self.dem_nodata):
                raise InvalidData("Trajectory intersects missing DEM values")
            return np.array([value])
        cols, rows = self._pixel_coordinates(xy[:, 0], xy[:, 1])
        if not np.all(np.isfinite(rows)) or not np.all(np.isfinite(cols)):
            raise InvalidData("Trajectory leaves DEM bounds")
        rows, cols = np.floor(rows), np.floor(cols)
        if np.any(rows < 0) or np.any(cols < 0) or np.any(rows >= self.dem.shape[0]) or np.any(cols >= self.dem.shape[1]):
            raise InvalidData("Trajectory leaves DEM bounds")
        rows, cols = rows.astype(np.intp), cols.astype(np.intp)
        values = self.dem[rows, cols].astype(float)
        if not np.all(np.isfinite(values)) or (self.dem_nodata is not None and np.any(values == self.dem_nodata)):
            raise InvalidData("Trajectory intersects missing DEM values")
        return values

    def terrain_clearances(self, segment, left: float = 0.0, right: float = 1.0) -> np.ndarray:
        """AGL extrema for every crossed constant-height raster cell.

        The DEM model uses the containing pixel, without bilinear interpolation.
        Splitting at row/column boundaries permits exact linear-Z extrema within
        each crossed cell. Interior probes avoid selecting the wrong boundary
        cell; boundary points themselves are checked as well.
        """
        start = segment.a + left * (segment.b - segment.a)
        end = segment.a + right * (segment.b - segment.a)
        pixel_start = self._pixel_coordinates(start[0], start[1])
        pixel_end = self._pixel_coordinates(end[0], end[1])
        distance = float(np.linalg.norm(end[:2] - start[:2]))
        fractions = [np.linspace(0, 1, max(2, math.ceil(distance / self.policy["terrain_sample_step_m"]) + 1))]
        for a, b in zip(pixel_start, pixel_end):
            if abs(b - a) > 1e-12:
                grid = np.arange(math.ceil(min(a, b)), math.floor(max(a, b)) + 1, dtype=float)
                crossed = (grid - a) / (b - a)
                fractions.append(crossed[(crossed > 0) & (crossed < 1)])
        breaks = np.unique(np.concatenate(fractions))
        boundary_points = start[None, :] + breaks[:, None] * (end - start)[None, :]
        boundary_agl = boundary_points[:, 2] - self.terrain(boundary_points[:, :2])
        midpoints = (breaks[:-1] + breaks[1:]) / 2
        cell_points = start[None, :] + midpoints[:, None] * (end - start)[None, :]
        cell_heights = self.terrain(cell_points[:, :2])
        return np.r_[boundary_agl, boundary_points[:-1, 2] - cell_heights,
                     boundary_points[1:, 2] - cell_heights]


def load_scene(directory: Path) -> Scene:
    metadata = read_json(directory / "metadata.json")
    identifier(required(metadata, "scenario_id", "metadata"), "metadata.scenario_id")
    if required(metadata, "temporal_altitude_reference", "metadata") != "AMSL":
        raise InvalidData("Only AMSL temporal altitude intervals are supported")
    crs = CRS.from_user_input(required(metadata, "metric_crs", "metadata"))
    if not crs.is_projected or any(abs(axis.unit_conversion_factor - 1) > 1e-9 for axis in crs.axis_info[:2]):
        raise InvalidData("metric_crs must be a projected CRS in metres")
    project = Transformer.from_crs("EPSG:4326", crs, always_xy=True)

    def features(name: str, geometry_types: set[str]) -> list:
        data = read_json(directory / name)
        if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
            raise InvalidData(f"{name}: expected GeoJSON FeatureCollection")
        result = []
        for f in data["features"]:
            if f.get("type") != "Feature" or not isinstance(f.get("properties"), dict):
                raise InvalidData(f"{name}: malformed feature")
            geo = shape(required(f, "geometry", name))
            if geo.geom_type not in geometry_types or not geo.is_valid or geo.is_empty:
                raise InvalidData(f"{name}: invalid geometry")
            if geo.bounds[0] < -180 or geo.bounds[2] > 180 or geo.bounds[1] < -90 or geo.bounds[3] > 90:
                raise InvalidData(f"{name}: coordinates must be WGS84 longitude/latitude")
            result.append({"geometry": transform(project.transform, geo), **f["properties"]})
        return result

    def keyed(items: list, context: str) -> dict:
        result = {}
        if not isinstance(items, list):
            raise InvalidData(f"{context}: expected list")
        for item in items:
            key = identifier(required(item, "id", context), f"{context}.id")
            if key in result:
                raise InvalidData(f"{context}: duplicate id {key}")
            result[key] = item
        return result

    polygons = {"Polygon", "MultiPolygon"}
    jobs = keyed(features("survey_areas.geojson", polygons), "survey areas")
    if not jobs:
        raise InvalidData("No required survey areas")
    allowed_items = features("allowed_airspace.geojson", polygons)
    if not allowed_items:
        raise InvalidData("No allowed airspace")
    nfz_items = features("no_fly_zones.geojson", polygons)
    sites = keyed(features("landing_sites.geojson", {"Point"}), "sites")
    fleet = keyed(required(read_json(directory / "fleet.json"), "uavs", "fleet"), "fleet")
    payloads = keyed(required(read_json(directory / "payload_catalog.json"), "payload_profiles", "payload catalog"), "payloads")
    mission = read_json(directory / "mission.json")
    if required(mission, "require_complete_coverage", "mission") is not True:
        raise InvalidData("This dataset requires complete coverage")
    if not isinstance(required(mission, "objectives", "mission"), list) or not mission["objectives"]:
        raise InvalidData("mission.objectives must be a nonempty list")
    window = required(mission, "mission_window", "mission")
    if timestamp(required(window, "end", "mission window"), "mission end") <= timestamp(required(window, "start", "mission window"), "mission start"):
        raise InvalidData("Empty mission window")
    if "daylight_window" in mission:
        daylight = mission["daylight_window"]
        if timestamp(required(daylight, "end", "daylight"), "daylight end") <= timestamp(required(daylight, "start", "daylight"), "daylight start"):
            raise InvalidData("Empty daylight window")
    for field in ("preparation_time_s", "data_download_time_s"):
        number(mission.get(field, 0), f"mission.{field}", 0)
    work_start = timestamp(window["start"], "mission start")
    work_end = timestamp(window["end"], "mission end")
    if "daylight_window" in mission:
        work_start = max(work_start, timestamp(mission["daylight_window"]["start"], "daylight start"))
        work_end = min(work_end, timestamp(mission["daylight_window"]["end"], "daylight end"))
    if work_end - work_start <= mission.get("preparation_time_s", 0) + mission.get("data_download_time_s", 0):
        raise InvalidData("Preparation and data download leave no flight time in the mission/daylight window")
    number(required(required(mission, "wind", "mission"), "speed_ms", "wind"), "wind.speed_ms", 0)
    number(required(mission, "service_time_min", "mission"), "service time", 0)
    policy = required(mission, "validation_policy", "mission")
    for key in ["coverage_tolerance_fraction", "min_agl_m", "altitude_tolerance_m", "terrain_sample_step_m"]:
        number(required(policy, key, "validation policy"), key, 0)
    if not 0 <= policy["coverage_tolerance_fraction"] < 1 or policy["terrain_sample_step_m"] <= 0:
        raise InvalidData("Invalid coverage or terrain sampling policy")
    for site in sites.values():
        if required(site, "role", "site") not in {"both", "start", "landing", "reserve"}:
            raise InvalidData("Unknown site role")
        if not isinstance(required(site, "candidate", "site"), bool):
            raise InvalidData("Site candidate must be boolean")
    for uav in fleet.values():
        for key in ["ground_speed_kmh", "operational_endurance_min", "max_wind_ms", "energy_reserve_fraction", "horizontal_separation_m", "vertical_separation_m", "turnaround_buffer_m", "takeoff_time_s", "landing_time_s", "service_time_s"]:
            number(required(uav, key, "uav"), f"uav.{key}", 0)
        if uav["ground_speed_kmh"] <= 0 or uav["operational_endurance_min"] <= 0 or not 0 <= uav["energy_reserve_fraction"] < 1:
            raise InvalidData("Invalid speed, endurance or reserve fraction")
        if not isinstance(required(uav, "payload_classes", "uav"), list):
            raise InvalidData("uav.payload_classes must be list")
        identifier(required(uav, "model", "uav"), "uav.model")
        if required(uav, "class", "uav") not in {"fixed_wing", "fixedwing", "airplane", "copter", "multirotor"}:
            raise InvalidData("Unsupported UAV flight class")
        for key in ["start_site", "landing_site"]:
            value = required(uav, key, "uav")
            if value is not None and value not in sites:
                raise InvalidData(f"uav: unknown {key}")
        if uav["landing_site"] is not None and sites[uav["landing_site"]]["role"] == "reserve":
            raise InvalidData("Reserve sites are emergency-only; choose a regular final landing site")
        if 'refuel_sites' in uav:
            value=uav['refuel_sites']
            if value is not None:
                if not isinstance(value,list) or any(not isinstance(sid,str) or sid not in sites for sid in value) or len(set(value))!=len(value):
                    raise InvalidData('uav: invalid refuel_sites')
                if any(sites[sid]['role']!='both' or sites[sid]['candidate'] for sid in value):
                    raise InvalidData('uav: refuel site must support landing and relaunch')
    for payload in payloads.values():
        identifier(required(payload, "type", "payload"), "payload.type")
        for key in ["nominal_agl_m", "survey_speed_factor"]:
            if number(required(payload, key, "payload"), key, 0) <= 0:
                raise InvalidData(f"payload.{key} must be positive")
        for key in ["front_overlap", "side_overlap"]:
            val = number(required(payload, key, "payload"), key, 0)
            if val >= 1:
                raise InvalidData(f"payload.{key} must be below one")
        if payload["survey_speed_factor"] > 1:
            raise InvalidData("survey_speed_factor cannot exceed maximum ground speed")
        if payload["type"] in {"lidar", "geophysics"}:
            if number(required(payload, "line_spacing_m", "payload"), "line_spacing_m", 0) <= 0:
                raise InvalidData("line_spacing_m must be positive")
        else:
            if number(required(payload, "gsd_cm", "payload"), "gsd_cm", 0) <= 0:
                raise InvalidData("gsd_cm must be positive")
            camera = required(payload, "camera", "payload")
            for key in ["image_width_px", "image_height_px", "focal_length_mm", "pixel_pitch_um"]:
                if number(required(camera, key, "camera"), key, 0) <= 0:
                    raise InvalidData(f"camera.{key} must be positive")
    for job in jobs.values():
        identifier(required(job, "survey_type", "job"), "survey_type")
        if required(job, "payload_profile", "job") not in payloads:
            raise InvalidData("job: unknown payload profile")
        if job["survey_type"] != payloads[job["payload_profile"]]["type"]:
            raise InvalidData("Job survey_type does not match its payload profile type")
    obstacles = features("obstacles.geojson", polygons | {"Point", "LineString"})
    for obstacle in obstacles:
        for key in ["height_m", "horizontal_buffer_m", "vertical_buffer_m"]:
            number(required(obstacle, key, "obstacle"), key, 0)
    temporal = features("temporal_airspace.geojson", polygons)
    for zone in temporal:
        identifier(required(zone, "id", "temporal zone"), "zone id")
        for key in ["min_alt_m", "max_alt_m"]:
            number(required(zone, key, "temporal zone"), key)
        if zone["max_alt_m"] < zone["min_alt_m"]:
            raise InvalidData("Temporal zone has inverted altitude interval")
        zone["start_s"] = timestamp(required(zone, "active_from", "temporal zone"), "active_from")
        zone["end_s"] = timestamp(required(zone, "active_to", "temporal zone"), "active_to")
        if zone["end_s"] < zone["start_s"]:
            raise InvalidData("Temporal zone has inverted time interval")
        if required(zone, "restriction_type", "temporal zone") not in {"prohibited", "no_fly", "forbidden", "restricted", "closed"}:
            raise InvalidData("Unsupported temporal restriction_type")
    with rasterio.open(directory / "dem.tif") as dataset:
        if dataset.crs != crs or dataset.count != 1:
            raise InvalidData("DEM must have one band in metric_crs")
        dem = dataset.read(1)
        affine, nodata = dataset.transform, dataset.nodata
    return Scene(directory, metadata, mission, jobs, sites, fleet, payloads,
                 unary_union([x["geometry"] for x in allowed_items]),
                 unary_union([x["geometry"] for x in nfz_items]), obstacles,
                 temporal, project, dem, affine, nodata, policy)












def compatible(scene: Scene, job: dict, uav: dict) -> bool:
    return scene.payloads[job["payload_profile"]]["type"] in uav["payload_classes"]


def usable_time(uav: dict) -> float:
    return uav["operational_endurance_min"] * 60 * (1 - uav["energy_reserve_fraction"])


def check_infeasible(scene: Scene, result: dict) -> tuple[list, dict]:
    diagnosis = required(result, "diagnosis", "INFEASIBLE result")
    proof = required(diagnosis, "proof", "diagnosis")
    kind = required(proof, "type", "proof")
    job_id = required(proof, "job_id", "proof")
    if job_id not in scene.jobs:
        raise InvalidData("INFEASIBLE proof refers to unknown job")
    job = scene.jobs[job_id]
    available = [u for u in scene.fleet.values() if compatible(scene, job, u)]
    wind = scene.mission["wind"]["speed_ms"]
    eligible = [u for u in available if wind <= u["max_wind_ms"]]
    if kind == "no_compatible_uav":
        valid = not available
        details = {"compatible_uavs": len(available)}
    elif kind == "wind_excludes_all":
        valid = bool(available) and not eligible
        details = {"compatible_uavs": len(available), "wind_eligible_uavs": len(eligible)}
    elif kind == "unreachable_required_point":
        point = proof.get("point", proof.get("p"))
        if not isinstance(point, list) or len(point) != 2:
            raise InvalidData("Reachability proof needs point [lon, lat]")
        lon, lat = [number(x, "proof point") for x in point]
        location = Point(scene.project.transform(lon, lat))
        if not job["geometry"].covers(location):
            raise InvalidData("Reachability witness is outside the required job")
        payload = scene.payloads[job["payload_profile"]]
        max_agl = payload["nominal_agl_m"] + scene.policy["altitude_tolerance_m"]
        if payload["type"] in {"lidar", "geophysics"}:
            half_diagonal = payload["line_spacing_m"] / 2
        else:
            camera = payload["camera"]
            max_agl = payload["gsd_cm"] * 10 * camera["focal_length_mm"] / camera["pixel_pitch_um"] + scene.policy["altitude_tolerance_m"]
            half_diagonal = max_agl * camera["pixel_pitch_um"] / 1000 / camera["focal_length_mm"] * math.hypot(camera["image_width_px"], camera["image_height_px"]) / 2
        bounds = []
        for uav in eligible:
            starts = (
                [scene.sites[uav["start_site"]]]
                if uav["start_site"] is not None
                else [site for site in scene.sites.values()
                      if not site["candidate"] and site["role"] in {"both", "start"}]
            )
            landings = (
                [scene.sites[uav["landing_site"]]]
                if uav["landing_site"] is not None
                else [site for site in scene.sites.values()
                      if not site["candidate"] and site["role"] in {"both", "landing", "reserve"}]
            )
            if 'refuel_sites' in uav:
                refuels=[site for sid,site in scene.sites.items() if site['role']=='both' and not site['candidate']
                         and (uav['refuel_sites'] is None or sid in uav['refuel_sites'])]
                # A necessary lower bound must allow service bases; otherwise
                # an initially distant pinned base yields a false impossibility.
                starts=starts+refuels
                landings=landings+refuels
            if not starts or not landings:
                raise InvalidData("Unpinned UAV has no compatible launch or landing site")
            # Prove the whole required area unreachable, so a permitted tiny
            # coverage gap cannot invalidate a single-point impossibility claim.
            lower_distance = min(
                max(0.0, start["geometry"].distance(job["geometry"]) + job["geometry"].distance(landing["geometry"]) - 2 * half_diagonal - 2.0)
                for start in starts for landing in landings
            )
            lower_time = lower_distance / (uav["ground_speed_kmh"] / 3.6 + 0.01)
            bounds.append({"uav_id": uav["id"], "minimum_time_s": lower_time, "usable_time_s": usable_time(uav)})
        valid = bool(eligible) and all(b["minimum_time_s"] > b["usable_time_s"] + 1e-6 for b in bounds)
        details = {"bounds": bounds, "sensor_half_diagonal_m": half_diagonal,
                   "proof_scope": "entire_required_job", "bound_model": "horizontal_distance_at_maximum_ground_speed"}
    else:
        raise InvalidData(f"Unknown infeasibility proof type {kind}")
    violations = [] if valid else [{"code": "UNPROVEN_INFEASIBLE", "message": "The supplied necessary-condition proof does not establish infeasibility", "proof_type": kind}]
    return violations, {"proof_type": kind, "proof_verified": valid, **details}


def check_result(input_dir: Path, result: dict, check_claims: bool = True) -> dict:
    """Validate a supplied result independently of the solver that produced it."""
    violations: list[dict] = []
    metrics: dict = {}
    status = result.get("status", "INVALID") if isinstance(result, dict) else "INVALID"

    def add(code: str, message: str, **context):
        violations.append({"code": code, "message": message, **context})

    try:
        finite_tree(result)
        scene = load_scene(Path(input_dir))
        if required(result, "schema", "result") != "geoscan.h3.result.v1":
            raise InvalidData("Unsupported result schema")
        if required(result, "scene_id", "result") != scene.metadata["scenario_id"]:
            raise InvalidData("Result scene_id does not match input")
        if required(result, "objective", "result") not in {"makespan", "total_flight"} or result["objective"] not in scene.mission["objectives"]:
            raise InvalidData("Unsupported or unrequested objective")
        if required(result, "status", "result") not in {"SAFE", "INFEASIBLE", "UNSAFE"}:
            raise InvalidData("Unsupported result status")
        if required(required(result, "optimality", "result"), "status", "optimality") != "unknown":
            raise InvalidData("This checker does not certify optimality")
        if not isinstance(required(result, "metrics", "result"), dict):
            raise InvalidData("Result metrics must be an object")
        sorties = required(result, "sorties", "result")
        if not isinstance(sorties, list):
            raise InvalidData("Result sorties must be a list")
        if result["status"] == "INFEASIBLE":
            if sorties:
                raise InvalidData("INFEASIBLE result cannot contain executable sorties")
            violations, metrics = check_infeasible(scene, result)
            return {"passed": not violations, "status": status, "violations": violations, "metrics": metrics, **MODEL_SCOPE}
        if result["status"] == "UNSAFE":
            add("UNSAFE_CANDIDATE", "Planner did not confirm a complete feasible plan; diagnostic checks only")
        if not sorties:
            add("EMPTY_SAFE", "SAFE result has no sorties")
        all_segments = []
        coverage = {key: [] for key in scene.jobs}
        scheduled = {key: [] for key in scene.fleet}
        all_ids = set()
        total_flight = 0.0
        total_distance = 0.0
        all_starts, all_ends = [], []
        mission_start = timestamp(scene.mission["mission_window"]["start"], "mission start")
        mission_end = timestamp(scene.mission["mission_window"]["end"], "mission end")
        effective_start = mission_start
        if "daylight_window" in scene.mission:
            effective_start = max(effective_start, timestamp(scene.mission["daylight_window"]["start"], "daylight start"))
        preparation = scene.mission.get("preparation_time_s", 0.)
        download = scene.mission.get("data_download_time_s", 0.)
        effective_end = min(mission_end, timestamp(scene.mission["daylight_window"]["end"], "daylight end")) if "daylight_window" in scene.mission else mission_end
        for sortie in sorties:
            sortie_id = identifier(required(sortie, "id", "sortie"), "sortie id")
            if sortie_id in all_ids:
                raise InvalidData("Duplicate sortie id")
            all_ids.add(sortie_id)
            uav_id = required(sortie, "uav_id", "sortie")
            if uav_id not in scene.fleet:
                raise InvalidData(f"Unknown uav_id {uav_id}")
            uav = scene.fleet[uav_id]
            ordinal = number(required(sortie, "index", "sortie"), "sortie.index", 0)
            if ordinal != int(ordinal):
                raise InvalidData("sortie.index must be integer")
            start = timestamp(required(sortie, "t_start", "sortie"), "sortie t_start")
            end = timestamp(required(sortie, "t_end", "sortie"), "sortie t_end")
            if end <= start:
                raise InvalidData("Sortie must have positive duration")
            duration = end - start
            if start < effective_start + preparation - 1e-6:
                add("PREPARATION_TIME", "Takeoff occurs before ground preparation finishes", sortie_id=sortie_id)
            if end + download > effective_end + 1e-6:
                add("DATA_DOWNLOAD_TIME", "Data download does not fit the mission/daylight window", sortie_id=sortie_id)
            if start < mission_start - 1e-6 or end > mission_end + 1e-6:
                add("MISSION_WINDOW", "Sortie leaves the mission time window", sortie_id=sortie_id)
            if "daylight_window" in scene.mission:
                daylight = scene.mission["daylight_window"]
                if start < timestamp(daylight["start"], "daylight") or end > timestamp(daylight["end"], "daylight"):
                    add("DAYLIGHT_WINDOW", "Sortie leaves the daylight time window", sortie_id=sortie_id)
            if scene.mission["wind"]["speed_ms"] > uav["max_wind_ms"]:
                add("WIND_LIMIT", "Wind exceeds UAV operating limit", sortie_id=sortie_id)
            if duration > usable_time(uav) + 1e-6:
                add("RESOURCE_EXCEEDED", "Flight duration consumes the required endurance reserve", sortie_id=sortie_id, flight_time_s=duration, usable_time_s=usable_time(uav))
            endpoints = []
            for key, roles in [("start_site", {"start", "both"}), ("landing_site", {"landing", "both"})]:
                site_id = required(sortie, key, "sortie")
                if site_id not in scene.sites:
                    raise InvalidData(f"Unknown {key} {site_id}")
                site = scene.sites[site_id]
                if site["role"] not in roles or site["candidate"]:
                    add("SITE_ROLE", "Flight uses a candidate or incompatible site", sortie_id=sortie_id, site_id=site_id)
                if "refuel_sites" not in uav and uav[key] is not None and site_id != uav[key]:
                    add("SITE_ASSIGNMENT", "Sortie differs from the UAV pinned site", sortie_id=sortie_id, site_id=site_id)
                endpoints.append(site)
            scheduled[uav_id].append((start, end, ordinal, sortie_id, sortie["start_site"], sortie["landing_site"]))
            all_starts.append(start)
            all_ends.append(end)
            total_flight += duration
            waypoints = required(sortie, "waypoints", "sortie")
            if not isinstance(waypoints, list) or len(waypoints) < 2:
                raise InvalidData("Sortie requires at least two waypoints")
            parsed = []
            for index, wp in enumerate(waypoints):
                lon = number(required(wp, "lon", "waypoint"), "waypoint.lon")
                lat = number(required(wp, "lat", "waypoint"), "waypoint.lat")
                if not -180 <= lon <= 180 or not -90 <= lat <= 90:
                    raise InvalidData("Invalid waypoint longitude/latitude")
                agl = number(required(wp, "agl_m", "waypoint"), "waypoint.agl_m", 0)
                amsl = number(required(wp, "amsl_m", "waypoint"), "waypoint.amsl_m")
                time = timestamp(required(wp, "t", "waypoint"), "waypoint.t")
                speed = number(required(wp, "speed_ms", "waypoint"), "waypoint.speed_ms", 0)
                remaining = number(required(wp, "remaining_endurance_s", "waypoint"), "remaining_endurance_s", 0)
                phase = required(wp, "phase", "waypoint")
                if phase not in {"takeoff", "transit", "survey", "landing", "loiter", "wait"}:
                    raise InvalidData(f"Unknown waypoint phase {phase}")
                job_id = required(wp, "job_id", "waypoint")
                if phase == "survey" and job_id not in scene.jobs:
                    raise InvalidData(f"Unknown survey job_id {job_id}")
                if phase != "survey" and job_id is not None:
                    raise InvalidData("Non-survey waypoint must have null job_id")
                xy = np.array(scene.project.transform(lon, lat))
                if not np.all(np.isfinite(xy)):
                    raise InvalidData("Waypoint projection failed")
                ground = scene.terrain(xy.reshape(1, 2))[0]
                if abs(amsl - ground - agl) > scene.policy["altitude_tolerance_m"] + 1e-6:
                    add("ALTITUDE_INCONSISTENT", "Waypoint AGL disagrees with AMSL minus DEM", sortie_id=sortie_id, waypoint=index)
                expected_remaining = usable_time(uav) - (time - start)
                if abs(remaining - expected_remaining) > 0.1:
                    add("REMAINING_ENDURANCE", "Claimed remaining endurance is inconsistent with elapsed time", sortie_id=sortie_id, waypoint=index)
                parsed.append({"xyz": np.r_[xy, amsl], "agl": agl, "time": time, "phase": phase, "job_id": job_id, "speed": speed})
            if abs(parsed[0]["time"] - start) > 1e-6 or abs(parsed[-1]["time"] - end) > 1e-6:
                add("SORTIE_TIMES", "Waypoint times disagree with sortie start/end", sortie_id=sortie_id)
            for point, site in zip([parsed[0], parsed[-1]], endpoints):
                if Point(point["xyz"][:2]).distance(site["geometry"]) > 1.0 or point["agl"] > scene.policy["altitude_tolerance_m"]:
                    add("SITE_ENDPOINT", "Flight endpoint does not match the ground-level landing/start site", sortie_id=sortie_id, site_id=site["id"])
            sortie_segments = []
            sortie_distance = 0.0
            phase_duration = {"takeoff": 0.0, "landing": 0.0}
            for index, (a, b) in enumerate(zip(parsed, parsed[1:])):
                if b["time"] <= a["time"]:
                    raise InvalidData("Waypoint times must strictly increase")
                segment = Segment(a["xyz"], b["xyz"], a["time"], b["time"], a["agl"], b["agl"], b["phase"], b["job_id"], uav_id, sortie_id, index)
                all_segments.append(segment)
                sortie_segments.append(segment)
                dt = segment.t1 - segment.t0
                distance_xy = float(np.linalg.norm(segment.b[:2] - segment.a[:2]))
                sortie_distance += distance_xy
                measured_speed = distance_xy / dt
                max_speed = uav["ground_speed_kmh"] / 3.6
                if segment.phase == "survey":
                    payload = scene.payloads[scene.jobs[segment.job_id]["payload_profile"]]
                    max_speed *= payload["survey_speed_factor"]
                if measured_speed > max_speed + 0.01:
                    add("SPEED_LIMIT", "Segment exceeds the operating ground speed", sortie_id=sortie_id, segment=index)
                if abs(b["speed"] - measured_speed) > max(0.05, measured_speed * 0.01):
                    add("SPEED_CLAIM", "Waypoint speed does not match its incoming segment", sortie_id=sortie_id, segment=index)
                if segment.phase in phase_duration:
                    phase_duration[segment.phase] += dt
                    site = endpoints[0 if segment.phase == "takeoff" else 1]
                    radius = number(scene.policy.get("takeoff_landing_corridor_radius_m", 100), "takeoff/landing corridor radius", 0)
                    if max(Point(segment.a[:2]).distance(site["geometry"]), Point(segment.b[:2]).distance(site["geometry"])) > radius + 1e-6:
                        add("PHASE_CORRIDOR", "Takeoff/landing phase leaves its site corridor", sortie_id=sortie_id, segment=index)
                line = segment.line
                if not scene.allowed.covers(line):
                    add("OUTSIDE_ALLOWED", "A full trajectory segment leaves allowed airspace", sortie_id=sortie_id, segment=index)
                if not scene.nfz.is_empty and scene.nfz.intersects(line):
                    add("NFZ_VIOLATION", "A trajectory segment intersects permanent prohibited airspace", sortie_id=sortie_id, segment=index)
                agl_samples = scene.terrain_clearances(segment)
                if np.any(agl_samples < -scene.policy["altitude_tolerance_m"]):
                    add("BELOW_SURFACE", "Trajectory intersects the modeled surface", sortie_id=sortie_id, segment=index)
                if segment.phase not in {"takeoff", "landing"} and np.any(agl_samples < scene.policy["min_agl_m"] - scene.policy["altitude_tolerance_m"]):
                    add("MIN_AGL", "Trajectory is below the configured surface clearance", sortie_id=sortie_id, segment=index)
                for obstacle in scene.obstacles:
                    footprint = obstacle["geometry"] if obstacle["horizontal_buffer_m"] == 0 else obstacle["geometry"].buffer(obstacle["horizontal_buffer_m"])
                    ranges = spatial_intervals(segment, footprint)
                    for left, right in ranges:
                        clearance = scene.terrain_clearances(segment, left, right)
                        if np.any(clearance < obstacle["height_m"] + obstacle["vertical_buffer_m"] - 1e-6):
                            add("OBSTACLE_CLEARANCE", "Trajectory violates obstacle clearance", sortie_id=sortie_id, segment=index, obstacle_id=obstacle.get("id"))
                            break
                for zone in scene.temporal:
                    time_range = interval_linear(segment.t0, segment.t1, zone["start_s"], zone["end_s"])
                    if time_range is None:
                        continue
                    altitude_range = interval_linear(segment.a[2], segment.b[2], zone["min_alt_m"], zone["max_alt_m"])
                    if altitude_range is None:
                        continue
                    for left, right in spatial_intervals(segment, zone["geometry"]):
                        if max(left, time_range[0], altitude_range[0]) <= min(right, time_range[1], altitude_range[1]):
                            add("TEMPORAL_AIRSPACE", "Trajectory intersects an active altitude/time restriction between waypoints", sortie_id=sortie_id, segment=index, zone_id=zone["id"])
                            break
                if segment.phase == "survey":
                    job = scene.jobs[segment.job_id]
                    payload = scene.payloads[job["payload_profile"]]
                    if not compatible(scene, job, uav):
                        add("PAYLOAD_INCOMPATIBLE", "UAV cannot perform the assigned survey type", sortie_id=sortie_id, job_id=segment.job_id)
                    if payload["type"] not in {"lidar", "geophysics"}:
                        camera = payload["camera"]
                        gsd = np.max(agl_samples) * camera["pixel_pitch_um"] / camera["focal_length_mm"] / 10
                        permitted_gsd = payload["gsd_cm"] + scene.policy["altitude_tolerance_m"] * camera["pixel_pitch_um"] / camera["focal_length_mm"] / 10
                        if gsd > permitted_gsd + 1e-8:
                            add("GSD_LIMIT", "Survey exceeds requested ground sample distance", sortie_id=sortie_id, segment=index)
                    coverage[segment.job_id].append(physical_swath(segment, payload, float(np.min(agl_samples))))
            if not sortie_segments or sortie_segments[0].phase != "takeoff" or sortie_segments[-1].phase != "landing":
                add("FLIGHT_PHASES", "Sortie must begin with takeoff and end with landing", sortie_id=sortie_id)
            seen_cruise = seen_landing = False
            for segment in sortie_segments:
                if segment.phase == "takeoff" and (seen_cruise or seen_landing):
                    add("FLIGHT_PHASES", "Takeoff must be a contiguous prefix of the flight", sortie_id=sortie_id)
                    break
                if seen_landing and segment.phase != "landing":
                    add("FLIGHT_PHASES", "Landing must be a contiguous suffix of the flight", sortie_id=sortie_id)
                    break
                seen_cruise |= segment.phase not in {"takeoff", "landing"}
                seen_landing |= segment.phase == "landing"
            for phase, minimum in [("takeoff", uav["takeoff_time_s"]), ("landing", uav["landing_time_s"])]:
                if phase_duration[phase] + 1e-6 < minimum:
                    add("PHASE_DURATION", "Takeoff/landing duration is too short", sortie_id=sortie_id, phase=phase)
            if uav["class"] in {"fixed_wing", "fixedwing", "airplane"} and uav["turnaround_buffer_m"] > 0:
                moving = [s for s in sortie_segments if np.linalg.norm(s.b[:2] - s.a[:2]) > 1e-8]
                for previous, following in zip(moving, moving[1:]):
                    if "survey" not in {previous.phase, following.phase}:
                        continue
                    heading0, heading1 = previous.b[:2] - previous.a[:2], following.b[:2] - following.a[:2]
                    cosine = float(np.dot(heading0, heading1) / np.linalg.norm(heading0) / np.linalg.norm(heading1))
                    if cosine >= math.cos(math.radians(5)):
                        continue
                    maneuver = Point(previous.b[:2]).buffer(uav["turnaround_buffer_m"])
                    if not scene.allowed.covers(maneuver) or (not scene.nfz.is_empty and scene.nfz.intersects(maneuver)):
                        add("FIXED_WING_MANEUVER", "Required survey-turn maneuver buffer intersects an airspace boundary", sortie_id=sortie_id, segment=previous.index)
            total_distance += sortie_distance
            for key, actual in [("flight_time_s", duration), ("distance_m", sortie_distance)]:
                claimed = number(required(sortie, key, "sortie"), f"sortie.{key}", 0)
                if check_claims and abs(claimed - actual) > max(0.05, abs(actual) * 1e-5):
                    add("CLAIMED_METRIC_MISMATCH", "Sortie metric differs from recomputed value", sortie_id=sortie_id, metric=key, actual=actual, claimed=claimed)
        for uav_id, schedule in scheduled.items():
            schedule.sort()
            indices = [entry[2] for entry in schedule]
            if len(indices) != len(set(indices)):
                add("SORTIE_INDEX", "Duplicate sortie index for one UAV", uav_id=uav_id)
            service = max(scene.fleet[uav_id]["service_time_s"], scene.mission["service_time_min"] * 60)
            uav=scene.fleet[uav_id]
            if 'refuel_sites' in uav and schedule:
                if uav['start_site'] is not None and schedule[0][4]!=uav['start_site']:
                    add('SITE_ASSIGNMENT','Initial takeoff differs from pinned site',uav_id=uav_id)
                if uav['landing_site'] is not None and schedule[-1][5]!=uav['landing_site']:
                    add('SITE_ASSIGNMENT','Final landing differs from pinned site',uav_id=uav_id)
                for previous in schedule[:-1]:
                    sid=previous[5]
                    if scene.sites[sid]['role']!='both' or scene.sites[sid]['candidate'] or (uav['refuel_sites'] is not None and sid not in uav['refuel_sites']):
                        add('REFUEL_SITE','Intermediate service uses a forbidden base',uav_id=uav_id,site_id=sid)
            for previous, following in zip(schedule, schedule[1:]):
                if following[0] < previous[1] + service - 1e-6:
                    add("AIRCRAFT_SERVICE", "Flights overlap or leave insufficient service time", uav_id=uav_id, sortie_ids=[previous[3], following[3]])
                if previous[5] != following[4]:
                    add("AIRCRAFT_REPOSITION", "Successive flights relocate the aircraft without a verified connecting route", uav_id=uav_id, sortie_ids=[previous[3], following[3]])
        covered_area, required_area = 0.0, 0.0
        per_job = {}
        for job_id, job in scene.jobs.items():
            swath = unary_union(coverage[job_id]) if coverage[job_id] else Polygon()
            area = job["geometry"].area
            covered = job["geometry"].intersection(swath).area
            fraction = max(0.0, min(1.0, covered / area))
            per_job[job_id] = {"required_area_m2": area, "covered_area_m2": covered, "coverage_percent": fraction * 100}
            required_area += area
            covered_area += covered
            if 1 - fraction > scene.policy["coverage_tolerance_fraction"] + 1e-12:
                add("COVERAGE_GAP", "Required survey area is not completely covered by physical sensor swaths", job_id=job_id, uncovered_area_m2=area - covered, coverage_percent=fraction * 100)
        active = []
        conflict_pairs = set()
        for current in sorted(all_segments, key=lambda s: s.t0):
            active = [s for s in active if s.t1 >= current.t0]
            for previous in active:
                if previous.uav_id == current.uav_id:
                    continue
                pair = tuple(sorted([previous.sortie_id, current.sortie_id]))
                if pair in conflict_pairs:
                    continue
                ua, ub = scene.fleet[current.uav_id], scene.fleet[previous.uav_id]
                encounter = conflict(previous, current, max(ua["horizontal_separation_m"], ub["horizontal_separation_m"]), max(ua["vertical_separation_m"], ub["vertical_separation_m"]))
                if encounter:
                    add("AIRCRAFT_CONFLICT", "Continuous trajectories violate separation between waypoints", sortie_ids=list(pair), **encounter)
                    conflict_pairs.add(pair)
            active.append(current)
        metrics = {"distance_m": total_distance, "total_flight_s": total_flight,
                   "makespan_s": max(all_ends) - effective_start + download if all_starts else 0.0,
                   "flight_completion_s": max(all_ends) - effective_start if all_starts else 0.0,
                   "preparation_time_s": preparation, "data_download_time_s": download,
                   "ground_operations_explicit": all(k in scene.mission for k in ("preparation_time_s", "data_download_time_s")),
                   "airborne_span_s": max(all_ends) - min(all_starts) if all_starts else 0.0,
                   "coverage_percent": covered_area / required_area * 100,
                   "required_area_m2": required_area, "covered_area_m2": covered_area,
                   "sortie_count": len(sorties), "per_job": per_job,
                   "terrain_model": "constant_height_per_raster_cell_with_linear_AMSL_segment"}
        if check_claims:
            for key in ["distance_m", "total_flight_s", "makespan_s", "coverage_percent"]:
                claimed = number(required(result["metrics"], key, "metrics"), f"metrics.{key}", 0)
                actual = metrics[key]
                if abs(claimed - actual) > max(0.05, abs(actual) * 1e-5):
                    add("CLAIMED_METRIC_MISMATCH", "Result metric differs from recomputed value", metric=key, actual=actual, claimed=claimed)
    except (InvalidData, ValueError, TypeError, KeyError, IndexError, AttributeError, OSError, OverflowError, ShapelyError, ProjError, rasterio.errors.RasterioError) as exc:
        add("INVALID_DATA", str(exc))
    if status == "SAFE":
        metrics["landing_reachability"] = "proved_by_resource_feasible_verified_suffix" if not violations else "not_proved"
    return {"passed": not violations, "status": status, "violations": violations, "metrics": metrics, **MODEL_SCOPE}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    report = check_result(args.input_dir, read_json(args.result))
    print(json.dumps(report, ensure_ascii=True, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
