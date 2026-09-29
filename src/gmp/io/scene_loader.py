"""Scene loading: dataset directory / uploaded files -> :class:`gmp.models.Scene`."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from shapely.geometry import Point

from ..geo import CrsPipeline, make_valid, polygon_hole_count, union_all
from ..kb.catalog import UavKnowledgeBase, default_kb
from ..kb.payloads import PayloadKb, default_payload_kb
from ..models import (
    Dem,
    Mission,
    Obstacle,
    PayloadProfile,
    Scene,
    Site,
    SurveyJob,
    Uav,
    Wind,
    Zone,
)
from .dem import load_dem
from .geojson_io import GeoJsonError, read_features, read_metric_features
from .kml_io import read_kml

LAYER_FILES = {
    "survey_areas": "survey_areas.geojson",
    "allowed_airspace": "allowed_airspace.geojson",
    "no_fly_zones": "no_fly_zones.geojson",
    "temporal_airspace": "temporal_airspace.geojson",
    "landing_sites": "landing_sites.geojson",
    "obstacles": "obstacles.geojson",
}


class SceneError(ValueError):
    pass


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _scene_centroid_lonlat(directory: Path) -> tuple[float, float]:
    """Pick the metric CRS from the survey area centroid (WGS-84 input)."""
    from shapely.geometry import shape

    path = directory / LAYER_FILES["survey_areas"]
    if not path.exists():
        raise SceneError(f"{directory}: survey_areas.geojson is mandatory")
    geoms = [shape(f["geometry"]) for f in read_features(path)]
    if not geoms:
        raise SceneError(f"{path}: no survey areas")
    c = union_all(geoms).centroid
    return c.x, c.y


def load_mission(path: Path) -> Mission:
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    objectives = raw.get("objectives")
    if not objectives:
        objectives = [raw.get("objective", "makespan")]
    window = raw.get("mission_window") or {}
    daylight = raw.get("daylight_window") or {}
    wind_raw = raw.get("wind") or {}
    return Mission(
        objectives=list(objectives),
        window_start=parse_dt(window.get("start")),
        window_end=parse_dt(window.get("end")),
        daylight_start=parse_dt(daylight.get("start")),
        daylight_end=parse_dt(daylight.get("end")),
        wind=Wind(
            speed_ms=float(wind_raw.get("speed_ms", 0.0)),
            direction_deg_from=float(wind_raw.get("direction_deg_from", 0.0)),
        ),
        dem_sampling_step_m=float(raw.get("dem_sampling_step_m", 30.0)),
        service_time_min=(
            float(raw["service_time_min"]) if raw.get("service_time_min") is not None else None
        ),
        require_schedule=bool(raw.get("require_schedule", True)),
        require_complete_coverage=bool(raw.get("require_complete_coverage", True)),
        allow_different_start_end=bool(raw.get("allow_different_start_end", False)),
        candidate_sites_allowed=bool(raw.get("candidate_sites_allowed", False)),
        force_simultaneous_initial_departure=bool(
            raw.get("force_simultaneous_initial_departure", False)
        ),
        wind_energy_model_enabled=bool(raw.get("wind_energy_model_enabled", False)),
        raw=raw,
    )


def load_payloads(path: Path, payload_kb: PayloadKb) -> dict[str, PayloadProfile]:
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    out: dict[str, PayloadProfile] = {}
    for p in raw.get("payload_profiles", []):
        ptype = p["type"]
        out[p["id"]] = PayloadProfile(
            id=p["id"],
            type=ptype,
            nominal_agl_m=float(p.get("nominal_agl_m", 120.0)),
            gsd_cm=(float(p["gsd_cm"]) if p.get("gsd_cm") is not None else None),
            front_overlap=(float(p["front_overlap"]) if p.get("front_overlap") is not None else None),
            side_overlap=(float(p["side_overlap"]) if p.get("side_overlap") is not None else None),
            strip_overlap=(float(p["strip_overlap"]) if p.get("strip_overlap") is not None else None),
            line_spacing_m=(
                float(p["line_spacing_m"]) if p.get("line_spacing_m") is not None else None
            ),
            camera=payload_kb.camera_for(ptype),
        )
    return out


def load_fleet(path: Path, kb: UavKnowledgeBase, mission: Mission) -> tuple[list[Uav], list[str]]:
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    fleet: list[Uav] = []
    warnings: list[str] = []
    for entry in raw.get("uavs", []):
        prof, notes = kb.resolve_instance(entry)
        service_s = prof.service_s
        if mission.service_time_min is not None:
            service_s = float(mission.service_time_min) * 60.0
        uav = Uav(
            id=entry["id"],
            model=entry.get("model", prof.model_id),
            uav_class=entry.get("class", prof.vehicle_class),
            ground_speed_ms=prof.planning_speed_ms,
            operational_endurance_min=prof.operational_endurance_min,
            max_wind_ms=prof.max_wind_ms,
            payload_classes=list(prof.payload_classes),
            energy_reserve_fraction=prof.energy_reserve_fraction,
            horizontal_separation_m=prof.horizontal_separation_m,
            vertical_separation_m=prof.vertical_separation_m,
            turnaround_buffer_m=prof.turnaround_buffer_m,
            start_site=entry.get("start_site"),
            landing_site=entry.get("landing_site"),
            takeoff_time_s=prof.takeoff_s,
            landing_time_s=prof.landing_s,
            service_time_s=service_s,
            kb_model_ref=prof.model_id,
            notes=notes,
        )
        fleet.append(uav)
        warnings.extend(f"{uav.id}: {n}" for n in notes)
    return fleet, warnings


def load_scene(
    directory: str | Path,
    scene_id: str | None = None,
    kb: UavKnowledgeBase | None = None,
    payload_kb: PayloadKb | None = None,
) -> Scene:
    """Load a conformance-style scenario directory into a :class:`Scene`."""
    d = Path(directory)
    if not d.is_dir():
        raise SceneError(f"{d} is not a directory")
    kb = kb or default_kb()
    payload_kb = payload_kb or default_payload_kb()

    lon, lat = _scene_centroid_lonlat(d)
    pipeline = CrsPipeline.for_point(lon, lat)
    warnings: list[str] = []

    mission = load_mission(d / "mission.json")
    payloads = load_payloads(d / "payload_catalog.json", payload_kb)
    fleet, fleet_warnings = load_fleet(d / "fleet.json", kb, mission)
    warnings.extend(fleet_warnings)

    jobs: list[SurveyJob] = []
    for props, geom in read_metric_features(d / LAYER_FILES["survey_areas"], pipeline):
        profile_id = props.get("payload_profile")
        if profile_id and profile_id not in payloads:
            warnings.append(f"job {props.get('id')}: unknown payload profile {profile_id}")
        holes = polygon_hole_count(geom)
        if holes:
            warnings.append(f"job {props.get('id')}: {holes} hole(s) preserved from input")
        jobs.append(
            SurveyJob(
                id=str(props.get("id", f"JOB_{len(jobs) + 1}")),
                survey_type=str(props.get("survey_type", "rgb")),
                payload_profile_id=str(profile_id) if profile_id else "",
                geom=make_valid(geom),
                properties=props,
            )
        )

    def _zones(layer: str, kind: str) -> list[Zone]:
        path = d / LAYER_FILES[layer]
        if not path.exists():
            return []
        out = []
        for props, geom in read_metric_features(path, pipeline):
            out.append(
                Zone(
                    id=str(props.get("id", f"{kind}_{len(out) + 1}")),
                    geom=make_valid(geom),
                    kind=kind,
                    hard=bool(props.get("hard", True)),
                    min_alt_m=(
                        float(props["min_alt_m"]) if props.get("min_alt_m") is not None else None
                    ),
                    max_alt_m=(
                        float(props["max_alt_m"]) if props.get("max_alt_m") is not None else None
                    ),
                    active_from=parse_dt(props.get("active_from")),
                    active_to=parse_dt(props.get("active_to")),
                    restriction_type=props.get("restriction_type"),
                )
            )
        return out

    allowed = _zones("allowed_airspace", "allowed_airspace")
    nfz = _zones("no_fly_zones", "no_fly_zone")
    temporal = _zones("temporal_airspace", "airspace_constraint")

    sites: list[Site] = []
    sites_path = d / LAYER_FILES["landing_sites"]
    if sites_path.exists():
        for props, geom in read_metric_features(sites_path, pipeline):
            pt = geom if isinstance(geom, Point) else geom.centroid
            site_id = str(props.get("id", f"SITE_{len(sites) + 1}"))
            sites.append(
                Site(
                    id=site_id,
                    point=pt,
                    role=str(props.get("role", "both")),
                    candidate=site_id.upper().startswith("CANDIDATE"),
                    properties=props,
                )
            )

    obstacles: list[Obstacle] = []
    obs_path = d / LAYER_FILES["obstacles"]
    if obs_path.exists():
        for props, geom in read_metric_features(obs_path, pipeline):
            obstacles.append(
                Obstacle(
                    id=str(props.get("id", f"OBS_{len(obstacles) + 1}")),
                    geom=make_valid(geom),
                    height_m=float(props.get("height_m", 50.0)),
                    horizontal_buffer_m=float(props.get("horizontal_buffer_m", 50.0)),
                    vertical_buffer_m=float(props.get("vertical_buffer_m", 30.0)),
                )
            )

    dem = Dem()
    dem_path = d / "dem.tif"
    if dem_path.exists():
        sampler = load_dem(str(dem_path), pipeline)
        if sampler is None:
            warnings.append("dem.tif present but could not be opened")
        else:
            dem = Dem(path=str(dem_path), sampler=sampler, nominal_elevation_m=sampler.mean_elevation_m)
    else:
        warnings.append("no DEM provided: flat terrain assumed")

    metadata: dict[str, Any] = {}
    meta_path = d / "metadata.json"
    if meta_path.exists():
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))

    # KML scene file is a redundant representation of the same vector data; we
    # read it to prove the KML import path works on customer-style input.
    kml_path = d / "scene.kml"
    if kml_path.exists():
        try:
            kml_items = read_kml(kml_path)
            metadata["scene_kml_placemarks"] = len(kml_items)
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(f"scene.kml could not be parsed: {exc}")

    for site in sites:
        if dem.available:
            site.elevation_m = dem.elevation(site.point.x, site.point.y)

    if not jobs:
        raise SceneError(f"{d}: no survey jobs loaded from survey_areas.geojson")
    if not sites:
        warnings.append("no landing_sites.geojson: H2 may be infeasible without pads")

    scene = Scene(
        id=scene_id or metadata.get("scenario_id") or d.name,
        crs=pipeline,
        jobs=jobs,
        allowed_airspace=allowed,
        no_fly_zones=nfz,
        airspace_constraints=temporal,
        sites=sites,
        obstacles=obstacles,
        fleet=fleet,
        payloads=payloads,
        mission=mission,
        dem=dem,
        source_dir=str(d),
        warnings=warnings,
        metadata=metadata,
    )
    return scene


def validate_scene(scene: Scene) -> dict[str, Any]:
    """Gate A style structural validation of a loaded scene."""
    issues: list[dict[str, str]] = []

    def err(code: str, message: str, severity: str = "error") -> None:
        issues.append({"code": code, "message": message, "severity": severity})

    if not scene.jobs:
        err("no_survey_areas", "scene contains no survey areas")
    if not scene.fleet:
        err("no_fleet", "scene contains no UAV instances")
    if not scene.sites:
        err("no_sites", "scene contains no launch/landing sites")
    if not any(s.can_start for s in scene.sites):
        err("no_start_site", "no site allows take-off")
    if not any(s.can_land for s in scene.sites):
        err("no_landing_site", "no site allows landing")
    if len(scene.fleet) > 10:
        err("fleet_too_large", f"{len(scene.fleet)} UAVs exceed the acceptance limit of 10", "warning")

    allowed = union_all([z.geom for z in scene.allowed_airspace])
    for job in scene.jobs:
        if not job.geom.is_valid:
            err("invalid_job_geometry", f"job {job.id} geometry is invalid")
        if job.payload_profile_id and job.payload_profile_id not in scene.payloads:
            err("unknown_payload_profile", f"job {job.id} references {job.payload_profile_id}")
        if not allowed.is_empty and job.geom.difference(allowed).area / max(job.geom.area, 1e-9) > 0.999:
            err("job_outside_allowed", f"job {job.id} lies fully outside allowed airspace")
        needed = job.survey_type
        if not any(u.supports(needed) for u in scene.fleet):
            err(
                "no_compatible_uav",
                f"no UAV in the fleet carries a {needed} payload (job {job.id})",
            )

    for uav in scene.fleet:
        if uav.start_site and scene.site(uav.start_site) is None:
            err("unknown_start_site", f"{uav.id} references missing site {uav.start_site}")
        if scene.mission.wind.speed_ms > uav.max_wind_ms:
            err(
                "wind_exceeds_limit",
                f"{uav.id}: wind {scene.mission.wind.speed_ms} m/s exceeds operational limit "
                f"{uav.max_wind_ms:.1f} m/s -> UAV excluded",
                "warning",
            )

    if scene.mission.window_start and scene.mission.window_end:
        if scene.mission.window_end <= scene.mission.window_start:
            err("bad_mission_window", "mission window end precedes start")

    errors = [i for i in issues if i["severity"] == "error"]
    return {
        "scene_id": scene.id,
        "ok": not errors,
        "crs": scene.crs.describe(),
        "counts": {
            "survey_jobs": len(scene.jobs),
            "fleet": len(scene.fleet),
            "sites": len(scene.sites),
            "no_fly_zones": len(scene.no_fly_zones),
            "airspace_constraints": len(scene.airspace_constraints),
            "obstacles": len(scene.obstacles),
            "payload_profiles": len(scene.payloads),
        },
        "survey_area_km2": round(sum(j.area_m2 for j in scene.jobs) / 1e6, 3),
        "dem": scene.dem.sampler.describe() if scene.dem.available else None,
        "issues": issues,
        "warnings": scene.warnings,
    }
