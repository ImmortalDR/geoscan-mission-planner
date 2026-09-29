"""M1 SceneLoader — contract h1.m1.scene_loader.v1

Reads a conformance scene directory (GeoJSON + optional DEM/mission/fleet)
into one validated ``Scene``. Primary formats: GeoJSON + GeoTIFF DEM.
KML in datasets is a companion / fallback when GeoJSON is absent.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from shapely.geometry import LineString as ShapelyLineString
from shapely.geometry import MultiPolygon, Point
from shapely.geometry import Polygon as ShapelyPolygon

from ..coverage.corridor import corridor_polygon
from ..geo import CrsPipeline, make_valid, union_all
from ..kb.catalog import KnowledgeBase, default_kb
from ..models import Dem, Mission, PayloadProfile, Scene, Site, SurveyJob, Wind, Zone
from .dem import load_dem
from .encoding import InputFileError, read_json_file
from .geojson import (
    GeoJsonError,
    assert_wgs84_lonlat,
    normalize_survey_geom,
    read_features_metric,
    read_features_wgs84,
)
from .kml import kml_survey_polygons, read_kml_features

_SITE_ROLES = frozenset({"both", "start", "landing", "reserve"})


def _hole_count(geom) -> int:
    if isinstance(geom, ShapelyPolygon):
        return len(geom.interiors)
    if isinstance(geom, MultiPolygon):
        return sum(len(p.interiors) for p in geom.geoms)
    return 0


class SceneError(ValueError):
    pass


LAYER = {
    "survey_areas": "survey_areas.geojson",
    "allowed_airspace": "allowed_airspace.geojson",
    "no_fly_zones": "no_fly_zones.geojson",
    "landing_sites": "landing_sites.geojson",
    "obstacles": "obstacles.geojson",
    "temporal_airspace": "temporal_airspace.geojson",
}


def _parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _read_json(path: Path, *, label: str) -> Any:
    try:
        return read_json_file(path, label=label)
    except InputFileError as exc:
        raise SceneError(str(exc)) from exc


def _dup_ids(ids: list[str], *, kind: str) -> None:
    seen: set[str] = set()
    dups: list[str] = []
    for i in ids:
        if i in seen and i not in dups:
            dups.append(i)
        seen.add(i)
    if dups:
        raise SceneError(f"duplicate {kind} id(s): {', '.join(dups)}")


def _wgs_survey_features(d: Path) -> tuple[list[tuple[dict, Any]], str, Path, list[str]]:
    extra_warns: list[str] = []
    survey_path = d / LAYER["survey_areas"]
    if survey_path.is_file():
        try:
            feats, warns = read_features_wgs84(survey_path, check_lonlat=True)
        except GeoJsonError as exc:
            raise SceneError(str(exc)) from exc
        extra_warns.extend(warns)
        normalized: list[tuple[dict, Any]] = []
        for props, geom in feats:
            try:
                assert_wgs84_lonlat(geom, label=f"survey id={props.get('id')}")
            except GeoJsonError as exc:
                raise SceneError(str(exc)) from exc
            ng = normalize_survey_geom(geom)
            if ng is None:
                continue
            normalized.append((props, ng))
        if normalized:
            return normalized, "geojson", survey_path, extra_warns
    kml_path = d / "scene.kml"
    if kml_path.is_file():
        try:
            feats = kml_survey_polygons(kml_path)
        except ValueError as exc:
            raise SceneError(str(exc)) from exc
        for props, geom in feats:
            try:
                assert_wgs84_lonlat(geom, label=f"kml survey id={props.get('id')}")
            except GeoJsonError as exc:
                raise SceneError(str(exc)) from exc
        if feats:
            return feats, "kml", kml_path, extra_warns
    raise SceneError(
        f"mandatory survey geometry missing: need {LAYER['survey_areas']} or scene.kml"
    )


def load_scene(directory: str | Path, scene_id: str | None = None, kb: KnowledgeBase | None = None) -> Scene:
    d = Path(directory)
    if not d.is_dir():
        raise SceneError(f"not a directory: {d}")

    kb = kb or default_kb()
    warnings: list[str] = []

    wgs_feats, survey_source, _src_path, load_warns = _wgs_survey_features(d)
    warnings.extend(load_warns)
    if survey_source == "kml":
        warnings.append("M1: survey loaded from scene.kml (GeoJSON absent)")
    survey_union_wgs = union_all([g for _, g in wgs_feats])
    if survey_union_wgs.is_empty:
        raise SceneError("survey geometry empty after normalize")
    try:
        assert_wgs84_lonlat(survey_union_wgs, label="survey union")
    except GeoJsonError as exc:
        raise SceneError(str(exc)) from exc
    lon = float(survey_union_wgs.centroid.x)
    lat = float(survey_union_wgs.centroid.y)
    crs = CrsPipeline.for_point(lon, lat)
    kml_path = d / "scene.kml"

    mission_raw = _read_json(d / "mission.json", label="mission.json") if (d / "mission.json").exists() else {}
    if not isinstance(mission_raw, dict):
        raise SceneError("mission.json: expected object")
    wind_raw = mission_raw.get("wind") or {}
    objectives = mission_raw.get("objectives") or [mission_raw.get("objective", "makespan")]
    objectives = [o for o in objectives if o in ("makespan", "total_flight")] or ["makespan", "total_flight"]
    window = mission_raw.get("mission_window") or {}
    mission = Mission(
        objectives=list(objectives),
        wind=Wind(
            speed_ms=float(wind_raw.get("speed_ms", 0)),
            direction_deg_from=float(wind_raw.get("direction_deg_from", 0)),
        ),
        window_start=_parse_dt(window.get("start")),
        window_end=_parse_dt(window.get("end")),
        require_complete_coverage=bool(mission_raw.get("require_complete_coverage", True)),
        require_schedule=bool(mission_raw.get("require_schedule", True)),
        allow_different_start_end=bool(mission_raw.get("allow_different_start_end", False)),
    )

    payloads: dict[str, PayloadProfile] = {}
    pc_path = d / "payload_catalog.json"
    if pc_path.exists():
        pc = _read_json(pc_path, label="payload_catalog.json")
        for p in (pc.get("payload_profiles") if isinstance(pc, dict) else None) or []:
            ptype = p["type"]
            payloads[p["id"]] = PayloadProfile(
                id=p["id"],
                type=ptype,
                nominal_agl_m=float(p.get("nominal_agl_m", 120)),
                gsd_cm=float(p["gsd_cm"]) if p.get("gsd_cm") is not None else None,
                side_overlap=float(p["side_overlap"]) if p.get("side_overlap") is not None else 0.7,
                front_overlap=float(p["front_overlap"]) if p.get("front_overlap") is not None else 0.8,
                line_spacing_m=float(p["line_spacing_m"]) if p.get("line_spacing_m") is not None else None,
                camera=kb.camera_for(ptype),
            )

    fleet = []
    fleet_path = d / "fleet.json"
    require_fleet = bool(mission_raw.get("require_fleet", False))
    if fleet_path.exists():
        fl = _read_json(fleet_path, label="fleet.json")
        entries = (fl.get("uavs") if isinstance(fl, dict) else None) or []
        if not entries:
            raise SceneError("fleet.json has no uavs (empty fleet not allowed when file is present)")
        for entry in entries:
            fleet.append(kb.resolve_uav(entry))
    else:
        warnings.append("no fleet.json: feasibility matrix will have empty eligible sets")
    if require_fleet and not fleet:
        raise SceneError("mission.require_fleet=true but fleet is empty")

    jobs: list[SurveyJob] = []
    for props, geom_wgs in wgs_feats:
        geom = make_valid(crs.to_metric(geom_wgs))
        # MultiLineString may remain after CRS; normalize again in metric
        if geom.geom_type == "MultiLineString":
            ng = normalize_survey_geom(geom)
            if ng is None:
                raise SceneError(f"unsupported survey geometry id={props.get('id')}")
            geom = ng
        if isinstance(geom, ShapelyLineString) or geom.geom_type == "LineString":
            half = float(props.get("half_width_m") or 40.0)
            geom = corridor_polygon(geom, half)
            props = dict(props)
            props["mode"] = props.get("mode") or "corridor"
            warnings.append(f"job {props.get('id')}: LineString → corridor buffer {half}m")
        if not geom.is_valid or geom.is_empty:
            raise SceneError(f"invalid survey polygon id={props.get('id')}")
        if geom.geom_type == "Point":
            raise SceneError(f"survey feature id={props.get('id')} is Point — need Polygon or LineString")
        holes = _hole_count(geom)
        if holes:
            warnings.append(f"job {props.get('id')}: {holes} hole(s) preserved")
        if props.pop("_repaired_invalid", None):
            warnings.append(f"job {props.get('id')}: geometry was invalid and repaired")
        pid = str(props.get("payload_profile") or "")
        if not pid and payloads:
            st = str(props.get("survey_type", "rgb"))
            pid = next((k for k, v in payloads.items() if v.type == st), next(iter(payloads), ""))
        if pid and pid not in payloads and payloads:
            warnings.append(f"unknown payload profile {pid}")
        mode = str(props.get("mode") or props.get("survey_mode") or "area")
        if mode not in ("area", "corridor"):
            mode = "corridor" if str(props.get("survey_type", "")).endswith("_corridor") else "area"
        jobs.append(
            SurveyJob(
                id=str(props.get("id", f"JOB_{len(jobs)+1}")),
                survey_type=str(props.get("survey_type", "rgb")),
                payload_profile_id=pid,
                geom=geom,
                mode=mode,
            )
        )
    if not jobs:
        raise SceneError("no survey jobs")
    _dup_ids([j.id for j in jobs], kind="survey job")

    survey_metric = union_all([j.geom for j in jobs])

    def zones(name: str, kind: str, *, default_hard: bool = True) -> list[Zone]:
        path = d / LAYER[name]
        if not path.exists():
            return []
        try:
            feats, zwarns = read_features_metric(path, crs)
        except GeoJsonError as exc:
            raise SceneError(str(exc)) from exc
        warnings.extend(zwarns)
        out: list[Zone] = []
        for props, geom in feats:
            geom = make_valid(geom)
            if geom.is_empty:
                continue
            hard = bool(props["hard"]) if "hard" in props else default_hard
            out.append(
                Zone(
                    id=str(props.get("id", f"{kind}_{len(out)+1}")),
                    geom=geom,
                    kind=kind,
                    hard=hard,
                )
            )
        return out

    def obstacles_as_zones() -> list[Zone]:
        path = d / LAYER["obstacles"]
        if not path.exists():
            return []
        try:
            feats, zwarns = read_features_metric(path, crs)
        except GeoJsonError as exc:
            raise SceneError(str(exc)) from exc
        warnings.extend(zwarns)
        out: list[Zone] = []
        for props, geom in feats:
            geom = make_valid(geom)
            if geom.is_empty:
                continue
            buf = float(props.get("horizontal_buffer_m") or 0.0)
            if buf > 0:
                geom = make_valid(geom.buffer(buf))
            out.append(
                Zone(
                    id=str(props.get("id", f"OBS_{len(out)+1}")),
                    geom=geom,
                    kind="obstacle",
                    hard=bool(props.get("hard", False)),
                )
            )
        return out

    sites: list[Site] = []
    sites_path = d / LAYER["landing_sites"]
    if sites_path.exists():
        try:
            site_feats, swarns = read_features_metric(sites_path, crs)
        except GeoJsonError as exc:
            raise SceneError(str(exc)) from exc
        warnings.extend(swarns)
        for props, geom in site_feats:
            pt = geom if isinstance(geom, Point) else geom.centroid
            role = str(props.get("role", "both"))
            if role not in _SITE_ROLES:
                warnings.append(f"site {props.get('id')}: unknown role {role!r}, coerced to both")
                role = "both"
            site = Site(
                id=str(props.get("id", f"SITE_{len(sites)+1}")),
                point=pt,
                role=role,
                candidate=str(props.get("id", "")).upper().startswith("CANDIDATE"),
            )
            sites.append(site)
            if not survey_metric.buffer(50.0).covers(pt):
                warnings.append(f"site {site.id}: outside survey area (+50m slack)")
    if not sites and kml_path.is_file():
        for props, geom in read_kml_features(kml_path):
            if not isinstance(geom, Point):
                continue
            role = str(props.get("role") or "both")
            if role not in _SITE_ROLES:
                role = "both"
            x, y = crs.lonlat_to_xy(geom.x, geom.y)
            sites.append(
                Site(id=str(props.get("id", f"SITE_{len(sites)+1}")), point=Point(x, y), role=role, candidate=False)
            )
        if sites:
            warnings.append("M1: landing sites loaded from scene.kml")
    if not sites:
        raise SceneError("landing_sites.geojson missing or empty: need ≥1 start/landing site")
    if not any(s.role in ("both", "start", "landing") for s in sites):
        raise SceneError("no site with role start/landing/both")
    _dup_ids([s.id for s in sites], kind="site")

    dem = Dem()
    dem_path = d / "dem.tif"
    if dem_path.exists():
        sampler = load_dem(str(dem_path), crs)
        if sampler:
            dem = Dem(path=str(dem_path), sampler=sampler, nominal_elevation_m=sampler.mean_elevation_m)
        else:
            warnings.append("dem.tif present but failed to open")
    else:
        warnings.append("no DEM: flat terrain assumed")

    meta: dict[str, Any] = {}
    if (d / "metadata.json").exists():
        meta_raw = _read_json(d / "metadata.json", label="metadata.json")
        if isinstance(meta_raw, dict):
            meta = meta_raw

    nfz = zones("no_fly_zones", "no_fly_zone", default_hard=True)
    allowed = zones("allowed_airspace", "allowed_airspace", default_hard=True)
    # KML NFZ fallback when GeoJSON layer absent/empty
    if not nfz and kml_path.is_file():
        for props, geom in read_kml_features(kml_path):
            if props.get("style") != "nfz":
                continue
            g = make_valid(crs.to_metric(geom))
            if g.is_empty:
                continue
            nfz.append(Zone(id=str(props.get("id", f"NFZ_{len(nfz)+1}")), geom=g, kind="no_fly_zone", hard=True))
        if nfz:
            warnings.append(f"M1: {len(nfz)} NFZ loaded from scene.kml")

    obstacles = obstacles_as_zones()
    temporal = zones("temporal_airspace", "airspace_constraint", default_hard=False)
    if obstacles:
        warnings.append(f"M1: {len(obstacles)} obstacle(s) loaded as soft exclusion")
    if temporal:
        warnings.append(
            f"M1: {len(temporal)} temporal airspace zone(s) loaded "
            "(H1 does not schedule around windows; H2/H3)"
        )

    return Scene(
        id=scene_id or meta.get("scenario_id") or d.name,
        crs=crs,
        jobs=jobs,
        fleet=fleet,
        sites=sites,
        payloads=payloads,
        mission=mission,
        allowed_airspace=allowed,
        no_fly_zones=nfz,
        obstacles=obstacles,
        temporal_airspace=temporal,
        dem=dem,
        warnings=warnings,
    )
