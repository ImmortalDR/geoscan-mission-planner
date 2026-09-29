"""Translate file-backed H3 inputs and standalone H2 output without planning."""

from __future__ import annotations

from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def prepare_h1_fleet(h1_scene, input_dir: Path) -> None:
    """Keep explicit aircraft speed/times ahead of catalog fallback values."""
    raw_fleet = json.loads((input_dir / "fleet.json").read_text())["uavs"]
    mission = json.loads((input_dir / "mission.json").read_text())
    service_minimum = float(mission["service_time_min"]) * 60.0
    fleet = {uav.id: uav for uav in h1_scene.fleet}
    for raw in raw_fleet:
        target = fleet[raw["id"]]
        target.uav_class = {"airplane": "fixed_wing", "fixedwing": "fixed_wing",
                            "copter": "multirotor"}.get(raw["class"], raw["class"])
        speed = float(raw["ground_speed_kmh"]) / 3.6
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError("Invalid explicit aircraft ground_speed_kmh")
        target.ground_speed_ms = speed
        for key in ("takeoff_time_s", "landing_time_s", "service_time_s"):
            value = float(raw[key])
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid explicit aircraft parameter {key}")
            setattr(target, key, max(value, service_minimum) if key == "service_time_s" else value)


def apply_h2_site_preferences(bundle: dict, input_dir: Path) -> None:
    """Restore explicit or unpinned sites after H1's compatibility fallback."""
    raw_fleet = {
        uav["id"]: uav
        for uav in json.loads((input_dir / "fleet.json").read_text())["uavs"]
    }
    for uav in bundle["fleet"]:
        raw = raw_fleet.get(uav["id"])
        if raw is None:
            raise ValueError(f"H1 emitted an unknown UAV: {uav['id']}")
        for key in ("start_site", "landing_site"):
            value = raw.get(key)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"Invalid {key} for {uav['id']}")
            # None lets H2 choose any non-candidate site with a suitable role.
            uav[key] = value
        if 'refuel_sites' in raw:
            value=raw['refuel_sites']
            if value is not None and (not isinstance(value,list) or any(not isinstance(x,str) for x in value)):
                raise ValueError(f"Invalid refuel_sites for {uav['id']}")
            uav['refuel_sites']=value


def build_scene_sidecar(input_dir: Path, bundle: dict) -> dict:
    """Supply actual geography/DEM; obstacles are conservatively hard blocked."""
    from h2.contract import mission_clock
    from shapely.geometry import mapping
    from gmp.safety.h3_core import load_scene

    scene = load_scene(input_dir)
    origin, horizon = mission_clock(bundle)
    temporal = []
    for zone in scene.temporal:
        start = zone["start_s"] - origin.timestamp()
        end = zone["end_s"] - origin.timestamp()
        if end < 0 or (horizon is not None and start > horizon):
            continue
        temporal.append({"geometry": mapping(zone["geometry"]),
                         "start_s": max(0.0, start), "end_s": end,
                         "min_alt_m": zone["min_alt_m"], "max_alt_m": zone["max_alt_m"],
                         "id": zone["id"]})
    forbidden = [] if scene.nfz.is_empty else [mapping(scene.nfz)]
    for obstacle in scene.obstacles:
        footprint = obstacle["geometry"].buffer(obstacle["horizontal_buffer_m"])
        if footprint.is_empty:
            footprint = obstacle["geometry"].buffer(1e-6)
        forbidden.append(mapping(footprint))
    # H1 already intersects mission and daylight windows; reserve ground work
    # inside that interval without changing its epoch or task geometry.
    operating = {"operating_window": {
        "start_s": float(scene.mission.get("preparation_time_s", 0.)),
        "end_s": horizon - float(scene.mission.get("data_download_time_s", 0.))}}
    return {
        **operating,
        "schema_version": "h2.scene.v1", "crs": bundle["crs"],
        "allowed": mapping(scene.allowed), "forbidden": forbidden, "temporal": temporal,
        "terrain": {"kind": "raster", "path": str((input_dir / "dem.tif").resolve())},
        "assumptions": ["Obstacles are avoided horizontally at every altitude",
                        "Temporal restrictions conservatively block the full sortie interval at every altitude"],
    }


def export_sorties(bundle: dict, plan: dict) -> list[dict]:
    """Preserve H2 timestamps/AMSL and convert only coordinates and wire names."""
    from h2.contract import fingerprint, timestamp
    from pyproj import Transformer

    if plan.get("schema_version") != "h2.plan.v1" or plan.get("input_sha256") != fingerprint(bundle):
        raise ValueError("Standalone H2 output does not match the supplied H1 bundle")
    transformer = Transformer.from_crs(bundle["crs"]["metric_epsg"], "EPSG:4326", always_xy=True)
    origin = timestamp(plan["time_origin"])
    tasks = {task["id"]: task for task in bundle["tasks"]}
    fleet = {uav["id"]: uav for uav in bundle["fleet"]}
    result = []
    for sortie in plan["sorties"]:
        uav = fleet[sortie["uav_id"]]
        usable = uav["operational_endurance_min"] * 60 * (1.0 - uav["energy_reserve_fraction"])
        points = []
        distance = 0.0
        previous = None
        for waypoint in sortie["waypoints"]:
            instant = (origin + timedelta(seconds=waypoint["t_s"])).isoformat()
            if previous is not None and (waypoint["t_s"] <= previous["t_s"] or instant == points[-1]["t"]):
                # Redundant phase-boundary records do not describe a segment.
                # Retain the incoming label of the existing endpoint.
                if (abs(waypoint["t_s"] - previous["t_s"]) < 1e-6 and
                        all(math.isclose(float(waypoint[key]), float(previous[key]), rel_tol=0.0, abs_tol=1e-9)
                            for key in ("x", "y", "z_m", "agl_m"))):
                    continue
                raise ValueError("H2 emitted a non-increasing or instantaneous trajectory segment")
            length = 0.0 if previous is None else math.hypot(waypoint["x"] - previous["x"], waypoint["y"] - previous["y"])
            speed = 0.0 if previous is None else length / (waypoint["t_s"] - previous["t_s"])
            lon, lat = transformer.transform(waypoint["x"], waypoint["y"])
            task = tasks.get(waypoint.get("task_id"))
            if waypoint["phase"] == "survey" and task is None:
                raise ValueError("H2 survey waypoint lacks a canonical H1 task")
            points.append({
                "lon": lon, "lat": lat, "agl_m": waypoint["agl_m"], "amsl_m": waypoint["z_m"],
                "t": instant,
                "phase": waypoint["phase"],
                "job_id": task["job_id"] if waypoint["phase"] == "survey" else None,
                "speed_ms": speed,
                "remaining_endurance_s": usable - (waypoint["t_s"] - sortie["start_s"]),
            })
            distance += length
            previous = waypoint
        result.append({
            "id": sortie["id"], "uav_id": sortie["uav_id"], "index": sortie["index"],
            "start_site": sortie["start_site_id"], "landing_site": sortie["landing_site_id"],
            "t_start": (origin + timedelta(seconds=sortie["start_s"])).isoformat(),
            "t_end": (origin + timedelta(seconds=sortie["end_s"])).isoformat(),
            "flight_time_s": sortie["end_s"] - sortie["start_s"], "distance_m": distance,
            "task_ids": list(sortie["task_ids"]), "waypoints": points,
            "parent_task_ids": [tasks[tid].get("parent_task_id",tid) for tid in sortie["task_ids"]],
            "task_variants": list(sortie.get("task_variants", ["primary"] * len(sortie["task_ids"]))),
            "task_reversed": list(sortie.get("task_reversed", [False] * len(sortie["task_ids"]))),
        })
    return result
