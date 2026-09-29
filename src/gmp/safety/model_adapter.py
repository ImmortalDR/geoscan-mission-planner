"""Snapshot mutable legacy Scene/Plan objects for the independent H3 gate.

Only representation changes happen here; no route, time or resource is repaired.
In particular, in-memory recommendation variants are not replaced by source_dir.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

from shapely.geometry import mapping

from .h3_gate import recompute_metrics, validate_result


def validate_model_plan(scene, plan) -> dict:
    try:
        with tempfile.TemporaryDirectory(prefix="gmp-h3-check-") as temporary:
            directory = Path(temporary)

            def write(name, data):
                (directory / name).write_text(json.dumps(data, allow_nan=False), encoding="utf-8")

            def features(name, rows):
                write(name, {"type": "FeatureCollection", "features": [
                    {"type": "Feature", "geometry": mapping(scene.crs.to_geographic(geom)), "properties": props}
                    for geom, props in rows]})

            metadata = {**scene.metadata, "scenario_id": scene.id,
                        "metric_crs": scene.crs.metric_epsg, "temporal_altitude_reference": "AMSL"}
            write("metadata.json", metadata)
            policy = {"coverage_tolerance_fraction": 0.001, "min_agl_m": 40.0,
                      "altitude_tolerance_m": 3.0, "terrain_sample_step_m": 20.0,
                      **scene.mission.raw.get("validation_policy", {})}
            mission = {"objectives": scene.mission.objectives,
                       "mission_window": {"start": scene.mission.effective_start().isoformat(),
                                          "end": scene.mission.effective_end().isoformat()},
                       "wind": asdict(scene.mission.wind), "service_time_min": scene.mission.service_time_min or 0,
                       "require_complete_coverage": scene.mission.require_complete_coverage,
                       "validation_policy": policy}
            write("mission.json", mission)
            features("survey_areas.geojson", [(j.geom, {"id": j.id, "survey_type": j.survey_type,
                     "payload_profile": j.payload_profile_id}) for j in scene.jobs])
            features("allowed_airspace.geojson", [(z.geom, {"id": z.id}) for z in scene.allowed_airspace])
            features("no_fly_zones.geojson", [(z.geom, {"id": z.id}) for z in scene.no_fly_zones if z.hard])
            features("landing_sites.geojson", [(s.point, {"id": s.id, "role": s.role,
                     "candidate": s.candidate}) for s in scene.sites])
            features("obstacles.geojson", [(o.geom, {"id": o.id, "height_m": o.height_m,
                     "horizontal_buffer_m": o.horizontal_buffer_m, "vertical_buffer_m": o.vertical_buffer_m})
                     for o in scene.obstacles])
            features("temporal_airspace.geojson", [(z.geom, {"id": z.id,
                     "min_alt_m": z.min_alt_m if z.min_alt_m is not None else -1e9,
                     "max_alt_m": z.max_alt_m if z.max_alt_m is not None else 1e9,
                     "active_from": (z.active_from or scene.mission.effective_start()).isoformat(),
                     "active_to": (z.active_to or scene.mission.effective_end()).isoformat(),
                     "restriction_type": z.restriction_type}) for z in scene.airspace_constraints])
            fleet = []
            for u in scene.fleet:
                raw = asdict(u)
                raw["class"] = raw.pop("uav_class")
                raw["ground_speed_kmh"] = raw.pop("ground_speed_ms") * 3.6
                fleet.append(raw)
            write("fleet.json", {"uavs": fleet})
            payloads = []
            for p in scene.payloads.values():
                raw = asdict(p)
                raw["front_overlap"] = p.front_overlap if p.front_overlap is not None else 0
                raw["side_overlap"] = p.side_overlap if p.side_overlap is not None else 0
                # The model has no survey-speed cap field. The aircraft's declared
                # ground speed remains the conservative contract limit here.
                raw["survey_speed_factor"] = 1.0
                payloads.append(raw)
            write("payload_catalog.json", {"payload_profiles": payloads})
            if not scene.dem.path:
                raise ValueError("A raster DEM is required for independent terrain validation")
            shutil.copyfile(scene.dem.path, directory / "dem.tif")
            result = {"schema": "geoscan.h3.result.v1", "scene_id": scene.id,
                      "objective": plan.objective, "status": "SAFE",
                      "optimality": {"status": "unknown"}, "metrics": {}, "sorties": []}
            for s in plan.sorties:
                waypoints = []
                for w in s.waypoints:
                    lon, lat = scene.crs.xy_to_lonlat(w.x, w.y)
                    task = plan.tasks.get(w.task_id) if w.phase == "survey" else None
                    waypoints.append({"lon": lon, "lat": lat, "agl_m": w.agl_m,
                                      "amsl_m": w.amsl_m, "t": w.t.isoformat(), "phase": w.phase,
                                      "job_id": task.job_id if task else None, "speed_ms": w.speed_ms,
                                      "remaining_endurance_s": w.remaining_endurance_s})
                result["sorties"].append({"id": s.id, "uav_id": s.uav_id, "index": s.index,
                                          "start_site": s.start_site_id, "landing_site": s.landing_site_id,
                                          "t_start": s.t_start.isoformat(), "t_end": s.t_end.isoformat(),
                                          "flight_time_s": s.flight_time_s, "distance_m": s.distance_m,
                                          "waypoints": waypoints})
            preliminary = recompute_metrics(directory, result)
            result["metrics"] = preliminary["metrics"]
            return validate_result(directory, result)
    except (ValueError, TypeError, AttributeError, OSError) as exc:
        return {"passed": False, "status": "UNSAFE", "certificate": None, "metrics": {},
                "violations": [{"code": "INDEPENDENT_INPUT_UNAVAILABLE", "message": str(exc)}]}
