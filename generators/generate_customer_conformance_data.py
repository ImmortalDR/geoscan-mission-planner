#!/usr/bin/env python3
"""Generate deterministic synthetic customer-conformance data for the Geoscan hackathon case.

The generated data follow the external I/O assumptions fixed by the customer:
- WGS-84 for vector I/O;
- survey areas, allowed airspace, NFZ, landing/reserve sites, obstacles;
- RGB / multispectral / IR / LiDAR / geophysical survey jobs;
- up to 10 physical UAVs;
- wind, daylight/time windows, temporal/altitude airspace restrictions;
- DEM GeoTIFF with a 30 m sampling grid;
- known-answer assertions for CI.

Synthetic values are test fixtures, not manufacturer/customer operational data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Iterable

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import Point, Polygon, MultiPolygon, mapping
from shapely.ops import transform

BASE_EPSG = "EPSG:32637"          # metric workspace for deterministic geometry
OUTPUT_EPSG = "EPSG:4326"         # customer-facing WGS-84
DEFAULT_SEED = 20260918

TO_WGS = Transformer.from_crs(BASE_EPSG, OUTPUT_EPSG, always_xy=True).transform


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def fc(features):
    return {"type": "FeatureCollection", "features": features}


def feature(geom, props):
    return {"type": "Feature", "geometry": mapping(transform(TO_WGS, geom)), "properties": props}


def empty_fc(path: Path):
    write_json(path, fc([]))


def polygon_kml(name: str, geom, style_url: str = "") -> str:
    def coords(poly: Polygon):
        outer = " ".join(f"{x:.8f},{y:.8f},0" for x, y in transform(TO_WGS, poly).exterior.coords)
        inners = []
        for ring in transform(TO_WGS, poly).interiors:
            s = " ".join(f"{x:.8f},{y:.8f},0" for x, y in ring.coords)
            inners.append(f"<innerBoundaryIs><LinearRing><coordinates>{s}</coordinates></LinearRing></innerBoundaryIs>")
        return outer, "".join(inners)
    polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    out = []
    for i, p in enumerate(polys):
        outer, inners = coords(p)
        out.append(
            f"<Placemark><name>{name}{'' if len(polys)==1 else f'_{i+1}'}</name>{style_url}"
            f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{outer}</coordinates></LinearRing></outerBoundaryIs>"
            f"{inners}</Polygon></Placemark>"
        )
    return "\n".join(out)


def point_kml(name: str, p: Point, description: str = "") -> str:
    w = transform(TO_WGS, p)
    return f"<Placemark><name>{name}</name><description>{description}</description><Point><coordinates>{w.x:.8f},{w.y:.8f},0</coordinates></Point></Placemark>"


def write_scene_kml(path: Path, survey_geoms, allowed, nfzs, landing_sites, obstacles) -> None:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
        '<Style id="survey"><PolyStyle><color>5522aa22</color></PolyStyle></Style>',
        '<Style id="nfz"><PolyStyle><color>55aa2222</color></PolyStyle></Style>',
        '<Style id="allowed"><PolyStyle><color>222222aa</color></PolyStyle></Style>',
    ]
    for name, geom in survey_geoms:
        parts.append(polygon_kml(name, geom, '<styleUrl>#survey</styleUrl>'))
    if allowed is not None:
        parts.append(polygon_kml("ALLOWED_AIRSPACE", allowed, '<styleUrl>#allowed</styleUrl>'))
    for name, geom in nfzs:
        parts.append(polygon_kml(name, geom, '<styleUrl>#nfz</styleUrl>'))
    for name, p, role in landing_sites:
        parts.append(point_kml(name, p, role))
    for name, geom, h in obstacles:
        parts.append(polygon_kml(name, geom))
    parts.append('</Document></kml>')
    path.write_text("\n".join(parts), encoding="utf-8")


def write_dem(path: Path, origin_x: float, origin_y_top: float, width=400, height=400, res=30.0, seed=DEFAULT_SEED, mode="hilly"):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    if mode == "flat":
        arr = np.full((height, width), 160.0, dtype=np.float32)
    elif mode == "ridge":
        arr = 145 + 180*np.exp(-((xx-0.55*width)**2)/(2*(0.10*width)**2)) + 22*np.sin(yy/24.0)
        arr = arr.astype(np.float32)
    else:
        phases = rng.uniform(0, 2*np.pi, size=4)
        arr = (160
               + 32*np.sin(xx/29.0 + phases[0])
               + 24*np.cos(yy/41.0 + phases[1])
               + 14*np.sin((xx+yy)/19.0 + phases[2])
               + 8*np.cos((2*xx-yy)/37.0 + phases[3])).astype(np.float32)
    aff = from_origin(origin_x, origin_y_top, res, res)
    with rasterio.open(path, "w", driver="GTiff", height=height, width=width, count=1,
                       dtype="float32", crs=BASE_EPSG, transform=aff,
                       compress="deflate", predictor=2) as dst:
        dst.write(arr, 1)
        dst.update_tags(dataset_role="synthetic_customer_conformance_dem", resolution_m=str(res), seed=str(seed))


def default_fleet(n=10):
    templates = [
        ("GS201", "geoscan_201", "fixed_wing", 80, 144, 12, ["rgb", "thermal"]),
        ("GS701", "geoscan_701", "fixed_wing", 100, 480, 12, ["rgb", "multispectral"]),
        ("GS401", "geoscan_401_geo", "multirotor", 30, 48, 12, ["rgb", "lidar", "geophysics"]),
        ("GS801", "geoscan_801", "multirotor", 45, 32, 10, ["thermal", "rgb_video"]),
        ("GEMINI", "geoscan_gemini", "multirotor", 45, 32, 10, ["rgb", "multispectral"]),
    ]
    uavs=[]
    for i in range(n):
        pref, model, cls, speed, endurance, wind, payloads = templates[i % len(templates)]
        uavs.append({
            "id": f"{pref}_{i+1:02d}", "model": model, "class": cls,
            "ground_speed_kmh": speed, "operational_endurance_min": endurance,
            "max_wind_ms": wind, "payload_classes": payloads,
            "energy_reserve_fraction": 0.20,
            "horizontal_separation_m": 300 if cls == "fixed_wing" else 50,
            "vertical_separation_m": 50,
            "turnaround_buffer_m": 300 if cls == "fixed_wing" else 30,
        })
    return uavs


def common_payloads():
    return [
        {"id":"RGB_STD","type":"rgb","gsd_cm":3,"front_overlap":0.80,"side_overlap":0.70,"nominal_agl_m":150},
        {"id":"MS_STD","type":"multispectral","gsd_cm":8,"front_overlap":0.75,"side_overlap":0.65,"nominal_agl_m":120},
        {"id":"IR_STD","type":"thermal","gsd_cm":12,"front_overlap":0.65,"side_overlap":0.55,"nominal_agl_m":100},
        {"id":"LIDAR_STD","type":"lidar","strip_overlap":0.25,"nominal_agl_m":120},
        {"id":"GEO_STD","type":"geophysics","line_spacing_m":50,"nominal_agl_m":80},
    ]


def save_scenario(root: Path, sid: str, description: str, survey_items, allowed, nfzs, temporal_airspace,
                  landing_sites, obstacles, fleet, mission, assertions, dem_mode="hilly", seed=DEFAULT_SEED):
    d = root / sid
    d.mkdir(parents=True, exist_ok=True)
    surveys=[]
    survey_geoms=[]
    for item in survey_items:
        geom=item["geom"]
        props={k:v for k,v in item.items() if k!="geom"}
        surveys.append(feature(geom, props))
        survey_geoms.append((props.get("id","SURVEY"), geom))
    write_json(d/"survey_areas.geojson", fc(surveys))
    write_json(d/"allowed_airspace.geojson", fc([feature(allowed, {"id":"ALLOWED","layer":"allowed_airspace"})]))
    write_json(d/"no_fly_zones.geojson", fc([feature(g, {"id":n,"layer":"no_fly_zone","hard":True}) for n,g in nfzs]))
    write_json(d/"temporal_airspace.geojson", fc([feature(g, {"id":n,"layer":"airspace_constraint",**props}) for n,g,props in temporal_airspace]))
    write_json(d/"landing_sites.geojson", fc([feature(p, {"id":n,"layer":"landing_site","role":role}) for n,p,role in landing_sites]))
    write_json(d/"obstacles.geojson", fc([feature(g, {"id":n,"layer":"obstacle","height_m":h,"horizontal_buffer_m":50}) for n,g,h in obstacles]))
    write_json(d/"fleet.json", {"uavs":fleet})
    write_json(d/"payload_catalog.json", {"payload_profiles":common_payloads()})
    write_json(d/"mission.json", mission)
    write_json(d/"expected_assertions.json", assertions)
    write_json(d/"metadata.json", {
        "scenario_id":sid, "description":description, "seed":seed,
        "vector_crs":"EPSG:4326", "internal_generation_crs":BASE_EPSG,
        "synthetic":True, "not_operational_data":True
    })
    minx,miny,maxx,maxy=allowed.bounds
    margin=1000
    write_dem(d/"dem.tif", minx-margin, maxy+margin, width=math.ceil((maxx-minx+2*margin)/30), height=math.ceil((maxy-miny+2*margin)/30), seed=seed, mode=dem_mode)
    write_scene_kml(d/"scene.kml", survey_geoms, allowed, nfzs,
                    [(n,p,r) for n,p,r in landing_sites], obstacles)


def build_suite(out: Path, seed: int):
    rng=np.random.default_rng(seed)
    out.mkdir(parents=True, exist_ok=True)
    # Common anchor around UTM 37N. Coordinates are synthetic and only WGS84 is exposed to the app.
    x0,y0=412000.0,6176000.0

    # S00 simple RGB smoke
    area=Polygon([(x0,y0),(x0+2200,y0),(x0+2200,y0+1800),(x0,y0+1800)])
    allowed=area.buffer(1200)
    save_scenario(out,"S00_smoke_rgb","Simple one-UAV RGB mission; parser/coverage/export smoke test.",
        [{"geom":area,"id":"RGB_A","survey_type":"rgb","payload_profile":"RGB_STD"}], allowed, [], [],
        [("BASE_A",Point(x0-400,y0+200),"both"),("RES_A",Point(x0+2600,y0+1600),"reserve")], [],
        [dict(default_fleet(1)[0],start_site="BASE_A")],
        {"objective":"makespan","mission_window":{"start":"2026-06-15T09:00:00+03:00","end":"2026-06-15T17:00:00+03:00"},"wind":{"speed_ms":4,"direction_deg_from":260}},
        {"expected_status":"SAFE","coverage_required":1.0,"nfz_violations":0,"vehicle_conflicts":0,"reserve_landing_reachability":True}, "flat", seed)

    # S01 comprehensive ~100 km2, every original input class + all five survey types
    outer=Polygon([(x0,y0),(x0+10000,y0),(x0+10000,y0+10000),(x0,y0+10000)])
    hole=Polygon([(x0+4300,y0+4000),(x0+5700,y0+4000),(x0+5700,y0+5400),(x0+4300,y0+5400)])
    large=Polygon(outer.exterior.coords,[hole.exterior.coords])
    # five subjobs covering representative areas; collectively inside the project area
    # Five non-overlapping 2 km x 10 km bands cover the full ~100 km² project area.
    # The central geophysical band carries the same hole as the project polygon.
    jobs=[
        {"geom":Polygon([(x0,y0),(x0+2000,y0),(x0+2000,y0+10000),(x0,y0+10000)]),"id":"JOB_RGB","survey_type":"rgb","payload_profile":"RGB_STD"},
        {"geom":Polygon([(x0+2000,y0),(x0+4000,y0),(x0+4000,y0+10000),(x0+2000,y0+10000)]),"id":"JOB_MS","survey_type":"multispectral","payload_profile":"MS_STD"},
        {"geom":Polygon([(x0+4000,y0),(x0+6000,y0),(x0+6000,y0+10000),(x0+4000,y0+10000)],
                        [[(x0+4300,y0+4000),(x0+5700,y0+4000),(x0+5700,y0+5400),(x0+4300,y0+5400)]]),"id":"JOB_GEO","survey_type":"geophysics","payload_profile":"GEO_STD"},
        {"geom":Polygon([(x0+6000,y0),(x0+8000,y0),(x0+8000,y0+10000),(x0+6000,y0+10000)]),"id":"JOB_LIDAR","survey_type":"lidar","payload_profile":"LIDAR_STD"},
        {"geom":Polygon([(x0+8000,y0),(x0+10000,y0),(x0+10000,y0+10000),(x0+8000,y0+10000)]),"id":"JOB_IR","survey_type":"thermal","payload_profile":"IR_STD"},
    ]
    nfzs=[("NFZ_A",Polygon([(x0+2000,y0+3900),(x0+3200,y0+3900),(x0+3200,y0+5400),(x0+2000,y0+5400)])),
          ("NFZ_B",Polygon([(x0+7600,y0+4300),(x0+9000,y0+4300),(x0+9000,y0+5600),(x0+7600,y0+5600)]))]
    temporal=[("TNFZ_1",Polygon([(x0+6100,y0+1800),(x0+7200,y0+1800),(x0+7200,y0+3000),(x0+6100,y0+3000)]),
               {"min_alt_m":0,"max_alt_m":700,"active_from":"2026-06-15T11:00:00+03:00","active_to":"2026-06-15T12:30:00+03:00"}),
              ("ALT_CAP",Polygon([(x0+900,y0+7300),(x0+2300,y0+7300),(x0+2300,y0+8800),(x0+900,y0+8800)]),
               {"min_alt_m":0,"max_alt_m":130,"active_from":"2026-06-15T00:00:00+03:00","active_to":"2026-06-15T23:59:59+03:00"})]
    landing=[("BASE_W",Point(x0-500,y0+1400),"both"),("BASE_E",Point(x0+10500,y0+1500),"both"),("BASE_N",Point(x0+8800,y0+10500),"both"),
             ("RES_1",Point(x0+3500,y0+1700),"reserve"),("RES_2",Point(x0+6100,y0+8800),"reserve"),("RES_3",Point(x0+1700,y0+8200),"reserve")]
    obs=[("TOWER_1",Point(x0+3900,y0+2600).buffer(120),180),("STACK_2",Point(x0+7100,y0+7900).buffer(150),230),("MAST_3",Point(x0+5300,y0+8300).buffer(90),120)]
    fleet=default_fleet(10)
    for i,u in enumerate(fleet): u["start_site"]=["BASE_W","BASE_E","BASE_N"][i%3]
    save_scenario(out,"S01_full_customer_acceptance_100km2","Comprehensive acceptance scene covering every original input class and all five survey types.",
        jobs, outer.buffer(1200), nfzs, temporal, landing, obs, fleet,
        {"objectives":["makespan","total_flight"],"mission_window":{"start":"2026-06-15T08:30:00+03:00","end":"2026-06-15T18:00:00+03:00"},
         "wind":{"speed_ms":7,"direction_deg_from":250},"dem_sampling_step_m":30,"service_time_min":8,"require_schedule":True,"require_complete_coverage":True},
        {"expected_status":"SAFE_AFTER_OPTIMIZATION","coverage_required":1.0,"max_physical_uav":10,"nfz_violations":0,"obstacle_violations":0,
         "vehicle_conflicts":0,"airspace_time_violations":0,"reserve_landing_reachability":True,"required_exports":["kml","geojson"],
         "required_objectives":["makespan","total_flight"]}, "hilly", seed+1)

    # S02 holes / multipolygon
    p1=Polygon([(x0,y0),(x0+3500,y0),(x0+3500,y0+3000),(x0,y0+3000)],
               [[(x0+900,y0+900),(x0+1600,y0+900),(x0+1600,y0+1600),(x0+900,y0+1600)]])
    p2=Polygon([(x0+4500,y0+700),(x0+7200,y0+700),(x0+7200,y0+3200),(x0+4500,y0+3200)])
    mp=MultiPolygon([p1,p2])
    save_scenario(out,"S02_multipolygon_holes","MultiPolygon and hole geometry coverage test.",
        [{"geom":mp,"id":"MP_RGB","survey_type":"rgb","payload_profile":"RGB_STD"}], mp.convex_hull.buffer(1000), [], [],
        [("BASE",Point(x0-300,y0+200),"both"),("RES",Point(x0+7600,y0+1800),"reserve")], [], default_fleet(3),
        {"objective":"total_flight","mission_window":{"start":"2026-06-15T09:00:00+03:00","end":"2026-06-15T17:00:00+03:00"},"wind":{"speed_ms":3,"direction_deg_from":180}},
        {"expected_status":"SAFE","coverage_required":1.0,"preserve_holes":True,"preserve_multipolygon":True}, "flat", seed+2)

    # S03 temporal airspace/daylight
    area3=Polygon([(x0,y0),(x0+5000,y0),(x0+5000,y0+3500),(x0,y0+3500)])
    gate=Polygon([(x0+2100,y0-500),(x0+2900,y0-500),(x0+2900,y0+4000),(x0+2100,y0+4000)])
    save_scenario(out,"S03_temporal_airspace_daylight","Temporal restriction forces scheduling around an active interval.",
        [{"geom":area3,"id":"RGB_T","survey_type":"rgb","payload_profile":"RGB_STD"}], area3.buffer(1000), [],
        [("TIME_GATE",gate,{"min_alt_m":0,"max_alt_m":700,"active_from":"2026-06-15T10:30:00+03:00","active_to":"2026-06-15T12:00:00+03:00"})],
        [("BASE_W",Point(x0-500,y0+1800),"both"),("BASE_E",Point(x0+5500,y0+1800),"both")], [], default_fleet(4),
        {"objective":"makespan","mission_window":{"start":"2026-06-15T09:00:00+03:00","end":"2026-06-15T16:00:00+03:00"},"daylight_window":{"start":"2026-06-15T09:15:00+03:00","end":"2026-06-15T15:45:00+03:00"},"wind":{"speed_ms":5,"direction_deg_from":270}},
        {"expected_status":"SAFE","airspace_time_violations":0,"schedule_required":True,"must_respect_active_interval":True}, "hilly", seed+3)

    # S04 multi-sortie: small enough for one UAV across several sorties, too large for one sortie.
    area4=Polygon([(x0,y0),(x0+2400,y0),(x0+2400,y0+1400),(x0,y0+1400)])
    f4=default_fleet(1); f4[0].update({"operational_endurance_min":32,"start_site":"BASE"})
    save_scenario(out,"S04_multi_sortie_energy","Mission deliberately exceeds one-sortie endurance but is feasible with repeated sorties.",
        [{"geom":area4,"id":"RGB_LONG","survey_type":"rgb","payload_profile":"RGB_STD"}], area4.buffer(700), [], [],
        [("BASE",Point(x0-300,y0+700),"both"),("RES",Point(x0+2700,y0+700),"reserve")], [], f4,
        {"objective":"makespan","mission_window":{"start":"2026-06-15T08:30:00+03:00","end":"2026-06-15T18:00:00+03:00"},"service_time_min":7,"wind":{"speed_ms":3,"direction_deg_from":210}},
        {"expected_status":"SAFE","minimum_sorties":2,"each_sortie_resource_feasible":True,"energy_reserve_fraction":0.20}, "flat", seed+4)

    # S05 4D conflict trigger: same survey job, two bases on opposite sides; expected deconfliction
    area5=Polygon([(x0+1200,y0+1200),(x0+5200,y0+1200),(x0+5200,y0+4200),(x0+1200,y0+4200)])
    save_scenario(out,"S05_4d_deconfliction","Geometry is arranged to make naive opposite-base transits cross; final plan must be conflict-free.",
        [{"geom":area5,"id":"RGB_CROSS","survey_type":"rgb","payload_profile":"RGB_STD"}], area5.buffer(2200), [], [],
        [("BASE_W",Point(x0-800,y0+2700),"both"),("BASE_E",Point(x0+7200,y0+2700),"both"),("RES",Point(x0+3200,y0+5600),"reserve")], [], default_fleet(2),
        {"objective":"makespan","mission_window":{"start":"2026-06-15T09:00:00+03:00","end":"2026-06-15T14:00:00+03:00"},"force_simultaneous_initial_departure":True,"wind":{"speed_ms":2,"direction_deg_from":0}},
        {"expected_status":"SAFE_AFTER_DECONFLICTION","vehicle_conflicts_final":0,"deconfliction_action_required":True}, "flat", seed+5)

    # S06 recommendation: provided base too far for short-endurance UAV, candidate site solves it
    area6=Polygon([(x0+7200,y0+7800),(x0+8200,y0+7800),(x0+8200,y0+8600),(x0+7200,y0+8600)])
    f6=default_fleet(1); f6[0].update({"model":"geoscan_gemini","class":"multirotor","ground_speed_kmh":42,"operational_endurance_min":24,"max_wind_ms":10,"start_site":"BAD_BASE","payload_classes":["rgb","multispectral"]})
    save_scenario(out,"S06_recommend_alternative_site","Initial configuration infeasible by range; Recommendation Engine should propose a closer site.",
        [{"geom":area6,"id":"RGB_REMOTE","survey_type":"rgb","payload_profile":"RGB_STD"}], area6.buffer(4000), [], [],
        [("BAD_BASE",Point(x0-2500,y0-2500),"both"),("CANDIDATE_GOOD_BASE",Point(x0+6600,y0+8000),"both"),("RES",Point(x0+9600,y0+8200),"reserve")], [], f6,
        {"objective":"makespan","mission_window":{"start":"2026-06-15T09:00:00+03:00","end":"2026-06-15T17:00:00+03:00"},"candidate_sites_allowed":True,"wind":{"speed_ms":3,"direction_deg_from":180}},
        {"expected_initial_status":"INFEASIBLE","expected_recommendation_type":"change_start_site","expected_candidate":"CANDIDATE_GOOD_BASE","recommended_replan_must_be_safe":True}, "flat", seed+6)

    # S07 reserve landing reachability failure
    area7=Polygon([(x0,y0),(x0+9000,y0),(x0+9000,y0+1800),(x0,y0+1800)])
    f7=default_fleet(1); f7[0].update({"operational_endurance_min":22,"start_site":"BASE","payload_classes":["rgb","thermal","geophysics"]})
    save_scenario(out,"S07_reserve_landing_failure","A long corridor has a middle portion without a reachable landing site under conservative reserve.",
        [{"geom":area7,"id":"CORRIDOR","survey_type":"geophysics","payload_profile":"GEO_STD"}], area7.buffer(700), [], [],
        [("BASE",Point(x0-300,y0+900),"both")], [], f7,
        {"objective":"total_flight","mission_window":{"start":"2026-06-15T09:00:00+03:00","end":"2026-06-15T17:00:00+03:00"},"wind":{"speed_ms":2,"direction_deg_from":90}},
        {"expected_status":"INFEASIBLE","reason_contains":"reserve_landing_reachability","expected_recommendation_type":"add_landing_site"}, "flat", seed+7)

    # S08 fixed-wing turn buffer intersects NFZ
    area8=Polygon([(x0+800,y0+1200),(x0+6200,y0+1200),(x0+6200,y0+3600),(x0+800,y0+3600)])
    edge_nfz=Polygon([(x0+6200,y0+900),(x0+6800,y0+900),(x0+6800,y0+3900),(x0+6200,y0+3900)])
    f8=[dict(default_fleet(1)[0],start_site="BASE")]
    save_scenario(out,"S08_fixed_wing_turnaround","Raw transects fit, but a fixed-wing turnaround protection buffer conflicts with NFZ.",
        [{"geom":area8,"id":"FW_RGB","survey_type":"rgb","payload_profile":"RGB_STD"}], area8.buffer(1200), [("EDGE_NFZ",edge_nfz)], [],
        [("BASE",Point(x0+300,y0+2300),"both"),("RES",Point(x0+3500,y0+4400),"reserve")], [], f8,
        {"objective":"total_flight","mission_window":{"start":"2026-06-15T09:00:00+03:00","end":"2026-06-15T15:00:00+03:00"},"wind":{"speed_ms":6,"direction_deg_from":270}},
        {"expected_status":"SAFE_OR_EXPLAINED_INFEASIBLE","fixed_wing_maneuver_buffer_must_be_checked":True,"nfz_violations":0}, "flat", seed+8)

    # S09 wind feasibility + direction scoring
    area9=Polygon([(x0,y0),(x0+6000,y0),(x0+6000,y0+2600),(x0,y0+2600)])
    f9=default_fleet(5)
    save_scenario(out,"S09_wind_feasibility","Wind makes lower-limit UAVs unavailable; direction must affect sweep scoring, not battery model.",
        [{"geom":area9,"id":"WIND_RGB","survey_type":"rgb","payload_profile":"RGB_STD"}], area9.buffer(900), [], [],
        [("BASE",Point(x0-350,y0+1300),"both"),("RES",Point(x0+6400,y0+1300),"reserve")], [], f9,
        {"objective":"makespan","mission_window":{"start":"2026-06-15T09:00:00+03:00","end":"2026-06-15T17:00:00+03:00"},"wind":{"speed_ms":11,"direction_deg_from":270},"wind_energy_model_enabled":False},
        {"expected_status":"SAFE_IF_COMPATIBLE_AIRCRAFT_AVAILABLE","wind_limit_must_filter_fleet":True,"wind_direction_must_affect_sweep_score":True,"wind_must_not_modify_energy_model":True}, "flat", seed+9)

    # S10 different start/end
    area10=Polygon([(x0+1800,y0+1200),(x0+7200,y0+1200),(x0+7200,y0+3200),(x0+1800,y0+3200)])
    f10=[dict(default_fleet(1)[0],start_site="START_W",landing_site="LAND_E",operational_endurance_min=180)]
    save_scenario(out,"S10_different_start_end","Tests explicitly permitted different take-off and landing sites.",
        [{"geom":area10,"id":"RGB_POINT_TO_POINT","survey_type":"rgb","payload_profile":"RGB_STD"}], area10.buffer(1100), [], [],
        [("START_W",Point(x0+700,y0+2200),"start"),("LAND_E",Point(x0+8300,y0+2200),"landing"),("RES",Point(x0+4600,y0+4100),"reserve")], [], f10,
        {"objective":"total_flight","mission_window":{"start":"2026-06-15T09:00:00+03:00","end":"2026-06-15T16:00:00+03:00"},"allow_different_start_end":True,"wind":{"speed_ms":4,"direction_deg_from":200}},
        {"expected_status":"SAFE","start_site":"START_W","landing_site":"LAND_E"}, "flat", seed+10)

    # S11 payload compatibility
    area11=Polygon([(x0,y0),(x0+5500,y0),(x0+5500,y0+3000),(x0,y0+3000)])
    jobs11=[{"geom":Polygon([(x0+100,y0+100),(x0+2600,y0+100),(x0+2600,y0+2900),(x0+100,y0+2900)]),"id":"LIDAR_JOB","survey_type":"lidar","payload_profile":"LIDAR_STD"},
            {"geom":Polygon([(x0+2900,y0+100),(x0+5400,y0+100),(x0+5400,y0+2900),(x0+2900,y0+2900)]),"id":"IR_JOB","survey_type":"thermal","payload_profile":"IR_STD"}]
    save_scenario(out,"S11_payload_compatibility","Only compatible UAV/payload combinations may receive each survey task.",
        jobs11, area11.buffer(800), [], [], [("BASE",Point(x0-300,y0+1500),"both"),("RES",Point(x0+5900,y0+1500),"reserve")], [], default_fleet(5),
        {"objective":"makespan","mission_window":{"start":"2026-06-15T09:00:00+03:00","end":"2026-06-15T16:00:00+03:00"},"wind":{"speed_ms":4,"direction_deg_from":220}},
        {"expected_status":"SAFE","payload_compatibility_violations":0,"all_jobs_assigned_exactly_once":True}, "hilly", seed+11)

    suite={"suite":"geoscan_customer_conformance_v1","seed":seed,"scenario_count":12,
           "scenarios":[p.name for p in sorted(out.iterdir()) if p.is_dir()]}
    write_json(out/"suite.json",suite)


def sha_manifest(root: Path):
    items=[]
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h=hashlib.sha256(p.read_bytes()).hexdigest()
            items.append({"path":str(p.relative_to(root)),"size_bytes":p.stat().st_size,"sha256":h})
    write_json(root/"MANIFEST.json",items)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",type=Path,default=Path("datasets/geoscan_customer_conformance"))
    ap.add_argument("--seed",type=int,default=DEFAULT_SEED)
    ap.add_argument("--force",action="store_true")
    args=ap.parse_args()
    if args.output.exists() and args.force:
        shutil.rmtree(args.output)
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit(f"Output is not empty: {args.output}; use --force")
    build_suite(args.output,args.seed)
    sha_manifest(args.output)
    print(f"Generated {args.output} with deterministic seed {args.seed}")

if __name__ == "__main__":
    main()
