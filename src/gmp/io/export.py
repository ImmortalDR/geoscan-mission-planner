"""Plan export: GeoJSON, KML, Mission JSON, QGC .plan."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, Point

from ..models import Plan, Scene
from .geojson_io import feature, feature_collection, write_feature_collection
from .kml_io import KmlDocument


UAV_COLORS_ABGR = [
    "ff3c8ce6",
    "ff4caf62",
    "ffc45c2b",
    "ff7a4cc4",
    "ff2b9ea8",
    "ffc4a02b",
    "ffc42b6e",
    "ff2b6ec4",
    "ff6ec42b",
    "ffa82b9e",
]


def plan_geojson(scene: Scene, plan: Plan) -> dict[str, Any]:
    feats = []
    for i, sortie in enumerate(plan.sorties):
        if len(sortie.waypoints) < 2:
            continue
        coords = [scene.crs.xy_to_lonlat(w.x, w.y) for w in sortie.waypoints]
        line = LineString([(lon, lat) for lon, lat in coords])
        feats.append(
            feature(
                line,
                {
                    "layer": "sortie",
                    "sortie_id": sortie.id,
                    "uav_id": sortie.uav_id,
                    "index": sortie.index,
                    "start_site": sortie.start_site_id,
                    "landing_site": sortie.landing_site_id,
                    "t_start": sortie.t_start.isoformat() if sortie.t_start else None,
                    "t_end": sortie.t_end.isoformat() if sortie.t_end else None,
                    "flight_time_min": round(sortie.flight_time_s / 60.0, 2),
                    "tasks": sortie.task_ids,
                    "status": plan.status,
                },
            )
        )
        start = sortie.waypoints[0]
        end = sortie.waypoints[-1]
        slon, slat = scene.crs.xy_to_lonlat(start.x, start.y)
        elon, elat = scene.crs.xy_to_lonlat(end.x, end.y)
        feats.append(
            feature(
                Point(slon, slat),
                {"layer": "takeoff", "sortie_id": sortie.id, "uav_id": sortie.uav_id},
            )
        )
        feats.append(
            feature(
                Point(elon, elat),
                {"layer": "landing", "sortie_id": sortie.id, "uav_id": sortie.uav_id},
            )
        )
    for site in scene.sites:
        lon, lat = scene.crs.xy_to_lonlat(*site.xy)
        feats.append(
            feature(
                Point(lon, lat),
                {"layer": "site", "id": site.id, "role": site.role, "candidate": site.candidate},
            )
        )
    return feature_collection(
        feats,
        name=f"plan:{scene.id}",
        crs={"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::4326"}},
        plan_status=plan.status,
        objective=plan.objective,
        metrics=plan.metrics,
    )


def plan_kml(scene: Scene, plan: Plan) -> str:
    doc = KmlDocument(
        f"GMP {scene.id} {plan.objective}",
        description=f"status={plan.status} coverage={plan.metrics.get('coverage_percent')}",
    )
    routes = doc.folder("Sorties")
    sites_f = doc.folder("Sites")
    for i, uav_id in enumerate(plan.used_uavs):
        color = UAV_COLORS_ABGR[i % len(UAV_COLORS_ABGR)]
        doc.style(f"uav_{i}", color, width=3.0)
        for sortie in plan.sorties_of(uav_id):
            if len(sortie.waypoints) < 2:
                continue
            coords3d = [
                (*scene.crs.xy_to_lonlat(w.x, w.y), w.amsl_m) for w in sortie.waypoints
            ]
            line = LineString([(c[0], c[1]) for c in coords3d])
            tspan = None
            if sortie.t_start and sortie.t_end:
                tspan = (sortie.t_start.isoformat(), sortie.t_end.isoformat())
            doc.placemark(
                routes,
                sortie.id,
                line,
                props={
                    "uav": uav_id,
                    "start": sortie.start_site_id,
                    "landing": sortie.landing_site_id,
                    "tasks": ",".join(sortie.task_ids),
                    "flight_min": round(sortie.flight_time_s / 60.0, 2),
                },
                style_id=f"uav_{i}",
                coords3d=coords3d,
                time_span=tspan,
            )
    doc.style("site", "ff2222aa", fill=True)
    for site in scene.sites:
        lon, lat = scene.crs.xy_to_lonlat(*site.xy)
        doc.placemark(
            sites_f,
            site.id,
            Point(lon, lat),
            props={"role": site.role},
            style_id="site",
            altitude_mode="clampToGround",
        )
    return doc.tostring()


def mission_json(scene: Scene, plan: Plan) -> dict[str, Any]:
    sorties = []
    for s in plan.sorties:
        sorties.append(
            {
                "id": s.id,
                "uav_id": s.uav_id,
                "index": s.index,
                "start_site": s.start_site_id,
                "landing_site": s.landing_site_id,
                "task_ids": s.task_ids,
                "t_start": s.t_start.isoformat() if s.t_start else None,
                "t_end": s.t_end.isoformat() if s.t_end else None,
                "flight_time_s": s.flight_time_s,
                "distance_m": s.distance_m,
                "energy": s.energy,
                "waypoints": [
                    {
                        "lon": scene.crs.xy_to_lonlat(w.x, w.y)[0],
                        "lat": scene.crs.xy_to_lonlat(w.x, w.y)[1],
                        "agl_m": w.agl_m,
                        "amsl_m": w.amsl_m,
                        "t": w.t.isoformat() if w.t else None,
                        "phase": w.phase,
                        "task_id": w.task_id,
                        "speed_ms": w.speed_ms,
                        "remaining_endurance_s": w.remaining_endurance_s,
                    }
                    for w in s.waypoints
                ],
            }
        )
    return {
        "format": "gmp.mission.json",
        "version": "1.0",
        "scene_id": scene.id,
        "objective": plan.objective,
        "status": plan.status,
        "created_at": (plan.created_at or datetime.now()).isoformat(),
        "metrics": plan.metrics,
        "validation": plan.validation,
        "certificate": plan.certificate,
        "diagnosis": plan.diagnosis,
        "recommendations": plan.recommendations,
        "deconfliction": plan.deconfliction,
        "coverage": plan.coverage,
        "sorties": sorties,
        "crs": scene.crs.describe(),
    }


def qgc_plan(scene: Scene, plan: Plan, uav_id: str | None = None) -> dict[str, Any]:
    """QGroundControl mission (.plan) for one UAV (first used if omitted)."""
    uid = uav_id or (plan.used_uavs[0] if plan.used_uavs else None)
    items = []
    if uid:
        seq = 0
        for sortie in plan.sorties_of(uid):
            for w in sortie.waypoints:
                lon, lat = scene.crs.xy_to_lonlat(w.x, w.y)
                cmd = 16  # NAV_WAYPOINT
                if w.phase == "takeoff":
                    cmd = 22
                elif w.phase == "landing":
                    cmd = 21
                items.append(
                    {
                        "autoContinue": True,
                        "command": cmd,
                        "doJumpId": seq,
                        "frame": 3,
                        "params": [0, 0, 0, None, lat, lon, w.agl_m],
                        "type": "SimpleItem",
                    }
                )
                seq += 1
    return {
        "fileType": "Plan",
        "groundStation": "gmp",
        "version": 1,
        "geoFence": {"circles": [], "polygons": [], "version": 2},
        "rallyPoints": {"points": [], "version": 2},
        "mission": {
            "cruiseSpeed": 15,
            "hoverSpeed": 5,
            "items": items,
            "plannedHomePosition": items[0]["params"][4:7] if items else [0, 0, 0],
            "vehicleType": 2,
            "version": 2,
        },
    }


def write_exports(scene: Scene, plan: Plan, directory: str | Path) -> dict[str, str]:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    geo = plan_geojson(scene, plan)
    write_feature_collection(d / "plan.geojson", geo)
    (d / "plan.kml").write_text(plan_kml(scene, plan), encoding="utf-8")
    (d / "mission.json").write_text(
        json.dumps(mission_json(scene, plan), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    if plan.used_uavs:
        (d / "qgc.plan").write_text(
            json.dumps(qgc_plan(scene, plan), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if plan.certificate:
        (d / "safety_certificate.json").write_text(
            json.dumps(plan.certificate, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    return {
        "geojson": str(d / "plan.geojson"),
        "kml": str(d / "plan.kml"),
        "mission_json": str(d / "mission.json"),
    }
