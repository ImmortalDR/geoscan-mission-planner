"""Build candidate witnesses through canonical H1 and the existing H2 planner.

Generation depends on the local service packages. Checking the resulting JSON
must remain independent of these packages and of the service's own verdict.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict
from pathlib import Path
from threading import RLock
from types import MethodType
from typing import Any


_SCHEDULER_LOCK = RLock()
_WAYPOINT_STEP_M = 20.0
_PLANNER_BUDGET_S = 5.0
_SEED = 20260918


def _load_sources() -> None:
    workspace = Path(__file__).resolve().parents[2]
    for source in (
        workspace / "h3" / "src",
        workspace / "h1" / "h1_coverage" / "src",
    ):
        if source.is_dir() and str(source) not in sys.path:
            sys.path.insert(0, str(source))


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _pixel_elevation(sampler, x: float, y: float) -> float:
    """Sample the raster cell containing the point, without half-cell shifts."""
    if sampler._tf is not None:
        x, y = sampler._tf.transform(x, y)
    column, row = ~sampler.transform * (x, y)
    column, row = math.floor(column), math.floor(row)
    if not (0 <= row < sampler.height and 0 <= column < sampler.width):
        raise ValueError("Candidate trajectory leaves the supplied DEM")
    value = float(sampler.band[row, column])
    if not math.isfinite(value) or (sampler.nodata is not None and value == sampler.nodata):
        raise ValueError("Candidate trajectory encounters DEM nodata")
    return value


def _prepare_scenes(input_dir: Path, objective: str):
    from pyproj import CRS

    from gmp.io.scene_loader import load_scene as load_gmp_scene
    from gmp.models import CameraModel as GmpCamera
    from h1_coverage.io.scene import load_scene as load_h1_scene
    from h1_coverage.models import CameraModel as H1Camera

    metadata = _read_json(input_dir / "metadata.json")
    scene_id = metadata["scenario_id"]
    scene = load_gmp_scene(input_dir, scene_id=scene_id)
    h1_scene = load_h1_scene(input_dir, scene_id=scene_id)
    expected_crs = CRS.from_user_input(metadata["metric_crs"])
    for loaded in (scene, h1_scene):
        if CRS.from_user_input(loaded.crs.metric_epsg) != expected_crs:
            raise ValueError("Loaded scene CRS differs from metadata.metric_crs")
        loaded.mission.objectives = [objective]
        if not loaded.dem.available:
            raise ValueError("Reference generation requires the supplied DEM")
        loaded.dem.sampler.elevation = MethodType(_pixel_elevation, loaded.dem.sampler)
    for site in scene.sites:
        site.elevation_m = scene.dem.elevation(*site.xy)

    # H1 has no separate daylight fields; give it the same effective window.
    h1_scene.mission.window_start = scene.mission.effective_start()
    h1_scene.mission.window_end = scene.mission.effective_end()
    if h1_scene.mission.window_end <= h1_scene.mission.window_start:
        raise ValueError("Mission and daylight windows do not intersect")

    profile_inputs = _read_json(input_dir / "payload_catalog.json")["payload_profiles"]
    planning_profiles = []
    for raw in profile_inputs:
        profile_id = raw["id"]
        h1_profile = h1_scene.payloads[profile_id]
        gmp_profile = scene.payloads[profile_id]
        camera_raw = raw.get("camera")
        if raw["type"] in ("rgb", "rgb_video", "rgb_mapping", "multispectral", "thermal"):
            if not isinstance(camera_raw, dict):
                raise ValueError(f"Profile {profile_id} needs an explicit camera snapshot")
            camera = {
                "id": str(camera_raw.get("id", f"{profile_id}_camera")),
                "image_width_px": int(camera_raw["image_width_px"]),
                "image_height_px": int(camera_raw["image_height_px"]),
                "focal_length_mm": float(camera_raw["focal_length_mm"]),
                "pixel_pitch_um": float(camera_raw["pixel_pitch_um"]),
            }
            if any(not math.isfinite(v) or v <= 0 for k, v in camera.items() if k != "id"):
                raise ValueError(f"Profile {profile_id} has invalid camera geometry")
            h1_profile.camera = H1Camera(**camera)
            gmp_profile.camera = GmpCamera(**camera)
        else:
            spacing = float(raw["line_spacing_m"])
            if not math.isfinite(spacing) or spacing <= 0:
                raise ValueError(f"Profile {profile_id} needs positive line_spacing_m")
            h1_profile.camera = None
            gmp_profile.camera = None

        planning_gsd = raw.get("planning_gsd_cm", raw.get("gsd_cm"))
        if planning_gsd is not None:
            planning_gsd = float(planning_gsd)
            limit = float(raw["gsd_cm"])
            if not 0 < planning_gsd <= limit:
                raise ValueError(f"Profile {profile_id}: planning GSD exceeds its limit")
            h1_profile.gsd_cm = planning_gsd
        planning_profiles.append(
            {
                "id": profile_id,
                "gsd_limit_cm": raw.get("gsd_cm"),
                "planning_gsd_cm": planning_gsd,
                "camera": camera_raw,
            }
        )

    fleet_inputs = _read_json(input_dir / "fleet.json")["uavs"]
    for raw in fleet_inputs:
        uav = scene.uav(raw["id"])
        if uav is None:
            raise ValueError(f"UAV {raw['id']} was not loaded")
        for name in ("takeoff_time_s", "landing_time_s", "service_time_s"):
            value = float(raw[name])
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"UAV {uav.id}: {name} must be nonnegative")
            setattr(uav, name, value)
    return scene, h1_scene, planning_profiles


def _transfer_coverage(scene, result):
    from gmp.coverage.engine import CoverageResult, _convert_task

    for profile_id, source in result.scene.payloads.items():
        target = scene.payloads[profile_id]
        for name in ("agl_m", "footprint_across_m", "footprint_along_m", "swath_spacing_m"):
            setattr(target, name, getattr(source, name))
        if target.camera is not None:
            target.gsd_cm_effective = target.camera.gsd_for_agl(target.agl_m) * 100.0
        if target.footprint_along_m is not None and target.front_overlap is not None:
            target.photo_interval_m = target.footprint_along_m * (1.0 - target.front_overlap)
        target.derivation = {"source": "canonical H1 with explicit input camera"}

    jobs = {job.id: job for job in result.scene.jobs}
    for target in scene.jobs:
        target.effective_geom = jobs[target.id].effective_geom

    coverage = result.coverage
    return CoverageResult(
        tasks={task_id: _convert_task(task) for task_id, task in coverage.tasks.items()},
        per_job=list(coverage.per_job),
        exclusions=dict(coverage.exclusions),
        payloads=[asdict(profile) for profile in result.scene.payloads.values()],
        candidates=dict(coverage.candidates),
        coverage_percent=float(coverage.coverage_percent),
        warnings=list(coverage.warnings),
    )


def _export_sortie(scene, plan, sortie, transformer) -> dict[str, Any]:
    uav = scene.uav(sortie.uav_id)
    if uav is None or not sortie.waypoints or sortie.t_start is None or sortie.t_end is None:
        raise ValueError(f"Cannot export incomplete sortie {sortie.id}")
    coordinates = transformer.transform(
        [wp.x for wp in sortie.waypoints], [wp.y for wp in sortie.waypoints]
    )
    waypoints = []
    distance = 0.0
    previous = None
    for index, waypoint in enumerate(sortie.waypoints):
        elapsed = (waypoint.t - sortie.t_start).total_seconds()
        speed = 0.0
        if previous is not None:
            segment_length = math.hypot(waypoint.x - previous.x, waypoint.y - previous.y)
            duration = (waypoint.t - previous.t).total_seconds()
            if duration <= 0:
                raise ValueError(f"Sortie {sortie.id} has non-increasing waypoint times")
            distance += segment_length
            speed = segment_length / duration
        task = plan.tasks.get(waypoint.task_id)
        # A waypoint labels the segment arriving at it, including survey entry.
        waypoints.append(
            {
                "lon": float(coordinates[0][index]),
                "lat": float(coordinates[1][index]),
                "agl_m": float(waypoint.agl_m),
                "amsl_m": float(waypoint.amsl_m),
                "t": waypoint.t.isoformat(),
                "phase": waypoint.phase,
                "job_id": task.job_id if task is not None and waypoint.phase == "survey" else None,
                "speed_ms": speed,
                "remaining_endurance_s": uav.usable_endurance_s - elapsed,
            }
        )
        previous = waypoint
    return {
        "id": sortie.id,
        "uav_id": sortie.uav_id,
        "index": sortie.index,
        "start_site": sortie.start_site_id,
        "landing_site": sortie.landing_site_id,
        "t_start": sortie.t_start.isoformat(),
        "t_end": sortie.t_end.isoformat(),
        "flight_time_s": (sortie.t_end - sortie.t_start).total_seconds(),
        "distance_m": distance,
        "waypoints": waypoints,
    }


def build_reference(input_dir: Path, objective: str) -> dict[str, Any]:
    """Return a candidate; the independent dataset checker must accept it.

    ``SAFE`` is the verdict to verify, not a certificate from this builder.
    No input files or H1/H2 sources are modified. CP-SAT and LNS each receive
    at most five seconds; loading, coverage, scheduling and validation add time.
    """
    if objective not in ("makespan", "total_flight"):
        raise ValueError("objective must be makespan or total_flight")
    _load_sources()
    from pyproj import Transformer

    import gmp.energy.scheduler as scheduler_module
    from gmp.planner import PlannerOptions, plan_mission
    from h1_coverage.config import CoverageConfig
    from h1_coverage.pipeline import run_h1_scene

    input_dir = Path(input_dir)
    scene, h1_scene, planning_profiles = _prepare_scenes(input_dir, objective)
    generation_options = _read_json(input_dir / "metadata.json").get("reference_generation", {})
    angle_step = float(generation_options.get("angle_step_deg", 15.0))
    if not math.isfinite(angle_step) or not 0 < angle_step <= 180:
        raise ValueError("reference_generation.angle_step_deg must be in (0, 180]")
    coverage_config = CoverageConfig(strict_coverage=False, angle_step_deg=angle_step)
    h1_result = run_h1_scene(h1_scene, coverage_config)
    coverage = _transfer_coverage(scene, h1_result)
    # Restore the service's default after this fixture-generation call.
    with _SCHEDULER_LOCK:
        previous_step = scheduler_module.WAYPOINT_STEP_M
        scheduler_module.WAYPOINT_STEP_M = _WAYPOINT_STEP_M
        try:
            plan = plan_mission(
                scene,
                PlannerOptions(
                    objective=objective,
                    time_budget_s=_PLANNER_BUDGET_S,
                    seed=_SEED,
                    recommend=False,
                ),
                coverage=coverage,
            )
        finally:
            scheduler_module.WAYPOINT_STEP_M = previous_step
    if not plan.sorties:
        raise ValueError(f"Planner produced no candidate sorties: {plan.diagnosis}")

    transformer = Transformer.from_crs(scene.crs.metric_epsg, "EPSG:4326", always_xy=True)
    return {
        "schema": "geoscan.h3.result.v1",
        "scene_id": scene.id,
        "objective": objective,
        "status": "SAFE",
        "optimality": {"status": "unknown"},
        "sorties": [_export_sortie(scene, plan, sortie, transformer) for sortie in plan.sorties],
        "metrics": {},
        "provenance": {
            "generator": "reference_builder.py: canonical H1 run_h1_scene + H2 plan_mission",
            "candidate_service_status": plan.status,
            "candidate_service_reasons": (plan.validation or {}).get("reasons", []),
            "candidate_requires_independent_check": True,
            "seed": _SEED,
            "planner_time_budget_s": _PLANNER_BUDGET_S,
            "waypoint_step_m": _WAYPOINT_STEP_M,
            "waypoint_phase_semantics": "incoming segment",
            "position_interpolation": "linear metric X/Y and AMSL between timestamps",
            "remaining_endurance": "operational endurance after reserve minus elapsed flight time",
            "dem_sampling": "containing raster cell (floor pixel indices), adapter correction to service round",
            "metric_crs": scene.crs.metric_epsg,
            "planning_profiles": planning_profiles,
            "h1_task_count": len(coverage.tasks),
            "h1_config": asdict(coverage_config),
            "h1_warnings": coverage.warnings,
        },
    }
