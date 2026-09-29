"""Deterministic H3 inputs. Reference answers are never stored inside input/."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import MultiPolygon, Point, Polygon, box, mapping
from shapely.ops import transform

ROOT = Path(__file__).resolve().parent
TO_WGS = Transformer.from_crs(32637, 4326, always_xy=True).transform
TO_METRIC = Transformer.from_crs(4326, 32637, always_xy=True).transform
CAMERAS = {
    "rgb": dict(image_width_px=6000, image_height_px=4000, focal_length_mm=16, pixel_pitch_um=3.9),
    "multispectral": dict(image_width_px=1280, image_height_px=960, focal_length_mm=5.4, pixel_pitch_um=3.75),
    "thermal": dict(image_width_px=640, image_height_px=512, focal_length_mm=13, pixel_pitch_um=12),
}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def feature(geom, **properties):
    return {"type": "Feature", "geometry": mapping(transform(TO_WGS, geom)), "properties": properties}


def layer(path, features):
    write_json(path, {"type": "FeatureCollection", "features": features})


def profile(kind="rgb", agl=123.07692307692308, real=False):
    p = dict(id=kind.upper(), type=kind, nominal_agl_m=agl,
             front_overlap=.8, side_overlap=.5, survey_speed_factor=1.0)
    if kind in CAMERAS:
        c = CAMERAS[kind]
        target = agl * c["pixel_pitch_um"] / c["focal_length_mm"] / 10
        p.update(camera=c, planning_gsd_cm=target, gsd_cm=target * (1.20 if real else 1.0))
    else:
        p.update(line_spacing_m=80.0, strip_overlap=0.0,
                 footprint_model="continuous_strip_width_equals_line_spacing")
    return p


def uav(uid="U01", kind="rgb", model="geoscan_201", start="BASE", landing="BASE", endurance=None):
    fixed = model in {"geoscan_201", "geoscan_701"}
    default_minutes = {"geoscan_201":144, "geoscan_701":480, "geoscan_401":48,
                       "geoscan_801":32, "gemini":32}[model]
    return dict(id=uid, model=model, **{"class":"fixed_wing" if fixed else "multirotor"},
                ground_speed_kmh=100.8 if model == "geoscan_701" else (79.2 if fixed else 43.2),
                operational_endurance_min=endurance or default_minutes, max_wind_ms=12 if fixed else 10,
                payload_classes=[kind], energy_reserve_fraction=.2,
                horizontal_separation_m=300 if fixed else 50, vertical_separation_m=50,
                turnaround_buffer_m=300 if fixed else 30,
                start_site=start, landing_site=landing, takeoff_time_s=180 if fixed else 60,
                landing_time_s=240 if fixed else 60, service_time_s=420,
                parameter_status="explicit_synthetic_operational_profile_not_manufacturer_guarantee")


def specs():
    ax, ay = TO_METRIC(37.53, 55.70)
    def rect(w, h, x=0, y=0):
        return box(ax+x-w/2, ay+y-h/2, ax+x+w/2, ay+y+h/2)
    def point(x, y):
        return Point(ax+x, ay+y)
    def base(sid, title, geom, profiles=None, fleet=None):
        return dict(id=sid, title=title, jobs=[(geom, "JOB_RGB", "rgb")],
                    profiles=profiles or [profile()], fleet=fleet or [uav()],
                    allowed=geom.envelope.buffer(1500, join_style=2),
                    sites=[("BASE", Point(geom.bounds[0]-500, geom.centroid.y), "both"),
                           ("RESERVE", Point(geom.bounds[2]+600, geom.centroid.y), "reserve")],
                    nfz=[], obstacles=[], temporal=[], objectives=["makespan"], expected="SAFE",
                    checks={}, real=False)
    s = base("S00_smoke_rgb", "Basic RGB end-to-end", rect(1000, 800))
    yield s

    ax, ay = TO_METRIC(37.530556, 55.703056)
    s = base("S01_msu_100km2", "MSU surroundings, 100 square km", rect(10000,10000),
             [profile(agl=492.3076923076923, real=True)],
             [uav(f"U{i+1:02}", model="geoscan_701", start=f"BASE_{i}", landing=f"BASE_{i}") for i in range(4)])
    s.update(real=True, objectives=["makespan", "total_flight"],
             sites=[(f"BASE_{i}", point(-5500, -3000+i*2000), "both") for i in range(4)] +
                   [("RESERVE", point(5500,0), "reserve")],
             checks={"survey_area_m2":100000000, "max_fleet":10})
    yield s

    ax, ay = TO_METRIC(37.34,55.65)
    west = Polygon(rect(1800,2200,-1400,300).exterior.coords,
                   [rect(400,400,-1400,300).exterior.coords])
    geom = MultiPolygon([west, rect(1200,1400,1500,-800)])
    s = base("S02_peredelkino", "Peredelkino and Lukinskaya, multipolygon with hole", geom,
             [profile(agl=246.15384615384616, real=True)], [uav(model="geoscan_701")])
    s.update(real=True, objectives=["total_flight"], checks={"multipolygon":True,"holes":True})
    yield s

    ax, ay = TO_METRIC(37.7065,55.612)
    s = base("S03_orekhovo_domodedovskaya", "Орехово — Домодедовская: световое окно и перерыв в полётах. Световое окно 09:00–19:00; запрет полётов 09:30–10:30. Разрешено летать 09:00–09:30 и 10:30–19:00 (МСК).", rect(4000,3000),
             [profile(agl=246.15384615384616, real=True)], [uav(model="geoscan_701")])
    s.update(real=True, daylight=True, checks={})
    s["temporal"]=[("MID_WINDOW_CLOSURE", s["allowed"], dict(min_alt_m=0,max_alt_m=2000,
                    active_from="2026-06-15T09:30:00+03:00",active_to="2026-06-15T10:30:00+03:00"))]
    yield s

    ax, ay = TO_METRIC(37.53,55.70)
    s = base("S04_multi_sortie", "Repeated sorties with service time", rect(2400,1400),
             fleet=[uav(model="gemini",endurance=24)])
    s["profiles"][0]["side_overlap"] = .7
    s["checks"]={"minimum_sorties":2}
    yield s

    s = base("S05_multi_uav", "Two payloads and opposing launch sites", rect(1800,600),
             [profile(),profile("multispectral")],
             [uav("U01",model="gemini",start="WEST",landing="WEST"),
              uav("U02",kind="multispectral",model="gemini",start="EAST",landing="EAST")])
    s.update(jobs=[(rect(450,450,650,0),"JOB_RGB","rgb"),
                   (rect(450,450,-650,0),"JOB_MS","multispectral")],
             sites=[("WEST",point(-1400,0),"both"),("EAST",point(1400,0),"both")],
             checks={"minimum_used_uavs":2})
    yield s

    s = base("S06_alternate_site", "Distant launch cannot reach task; alternative provided separately",rect(500,500),
             fleet=[uav(model="gemini",endurance=16)])
    s.update(allowed=box(ax-13500,ay-2000,ax+2000,ay+2000),
             sites=[("BASE",point(-12000,0),"both"),("CANDIDATE_NEAR",point(-600,0),"candidate")],
             expected="INFEASIBLE", proof={"type":"unreachable_required_point","job_id":"JOB_RGB","point":list(TO_WGS(ax,ay))},
             alternative=True)
    yield s

    s=base("S07_unreachable_landing", "Only permitted landing exceeds the available resource",rect(500,500),
           fleet=[uav(model="gemini",endurance=20,start="START",landing="END")])
    s.update(allowed=box(ax-2000,ay-2000,ax+22500,ay+2000),different=True,
             sites=[("START",point(-600,0),"start"),("END",point(21000,0),"landing")],
             expected="INFEASIBLE",proof={"type":"unreachable_required_point","job_id":"JOB_RGB","point":list(TO_WGS(ax,ay))})
    yield s

    s=base("S08_fixed_wing_nfz", "Fixed-wing turn buffers, NFZ and obstacle",rect(1200,1000))
    s["nfz"]=[("NFZ",rect(300,600,1200,0))]
    s["obstacles"]=[("TEST_MAST",point(0,1100).buffer(50),180)]
    yield s

    s=base("S09_wind_refusal", "Wind excludes every compatible aircraft",rect(500,500),fleet=[uav(model="gemini")])
    s.update(wind=14,expected="INFEASIBLE",proof={"type":"wind_excludes_all","job_id":"JOB_RGB"})
    yield s

    s=base("S10_different_start_end", "Different permitted takeoff and landing sites",rect(1600,600),
           fleet=[uav(start="START",landing="END")])
    s.update(sites=[("START",point(-1300,0),"start"),("END",point(1300,0),"landing")],
             different=True,checks={"different_start_end":True})
    yield s

    kinds=["rgb","multispectral","thermal","lidar","geophysics"]
    s=base("S11_payload_compatibility", "Five survey classes and explicit compatibility — wind",rect(5500,800),
           [profile(k,agl=120) for k in kinds],
           [uav(f"U{i+1:02}",kind=k,model="geoscan_401",start=f"BASE_{i}",landing=f"BASE_{i}") for i,k in enumerate(kinds)])
    s.update(jobs=[(rect(320,320,-2200+i*1100,0),"JOB_"+k.upper(),k) for i,k in enumerate(kinds)],
             sites=[(f"BASE_{i}",point(-2200+i*1100,-550),"both") for i in range(5)],
             checks={"minimum_used_uavs":5,"survey_types":kinds})
    yield s


def scene_kml(path, features):
    ns="http://www.opengis.net/kml/2.2"
    ET.register_namespace("",ns)
    k=ET.Element(f"{{{ns}}}kml"); doc=ET.SubElement(k,"Document")
    for f in features:
        pm=ET.SubElement(doc,"Placemark"); ET.SubElement(pm,"name").text=f["properties"]["id"]
        geom=f["geometry"]
        if geom["type"]=="Point":
            el=ET.SubElement(pm,"Point"); ET.SubElement(el,"coordinates").text=",".join(map(str,geom["coordinates"]))
        else:
            polys=[geom["coordinates"]] if geom["type"]=="Polygon" else geom["coordinates"]
            container=ET.SubElement(pm,"MultiGeometry") if len(polys)>1 else pm
            for rings in polys:
                poly=ET.SubElement(container,"Polygon")
                for n,ring in enumerate(rings):
                    boundary=ET.SubElement(poly,"outerBoundaryIs" if n==0 else "innerBoundaryIs")
                    lr=ET.SubElement(boundary,"LinearRing")
                    ET.SubElement(lr,"coordinates").text=" ".join(f"{x},{y},0" for x,y in ring)
    ET.ElementTree(k).write(path,encoding="utf-8",xml_declaration=True)


def save(spec, directory):
    d=directory/"input"; d.mkdir(parents=True,exist_ok=True)
    surveys=[feature(g,id=j,survey_type=k,payload_profile=k.upper()) for g,j,k in spec["jobs"]]
    sites=[feature(p,id=n,role="both" if r=="candidate" else r,candidate=r=="candidate") for n,p,r in spec["sites"]]
    layer(d/"survey_areas.geojson",surveys)
    layer(d/"landing_sites.geojson",sites)
    layer(d/"allowed_airspace.geojson",[feature(spec["allowed"],id="ALLOWED")])
    layer(d/"no_fly_zones.geojson",[feature(g,id=n,hard=True) for n,g in spec["nfz"]])
    layer(d/"temporal_airspace.geojson",[feature(g,id=n,restriction_type="prohibited",**p) for n,g,p in spec["temporal"]])
    layer(d/"obstacles.geojson",[feature(g,id=n,height_m=h,horizontal_buffer_m=50,vertical_buffer_m=20) for n,g,h in spec["obstacles"]])
    write_json(d/"fleet.json",{"uavs":spec["fleet"]})
    write_json(d/"payload_catalog.json",{"payload_profiles":spec["profiles"]})
    mission=dict(objectives=spec["objectives"],
                 mission_window={"start":"2026-06-15T08:30:00+03:00","end":"2026-06-15T20:00:00+03:00"},
                 wind={"speed_ms":spec.get("wind",3),"direction_deg_from":270},
                 require_complete_coverage=True,require_schedule=True,service_time_min=7,
                 allow_different_start_end=spec.get("different",False),candidate_sites_allowed=False,
                 dem_sampling_step_m=20,wind_energy_model_enabled=False,
                 validation_policy=dict(coverage_tolerance_fraction=.001,min_agl_m=40,
                                        altitude_tolerance_m=3,terrain_sample_step_m=20,
                                        takeoff_landing_corridor_radius_m=100))
    if spec.get("daylight"):
        mission["daylight_window"]={"start":"2026-06-15T09:00:00+03:00","end":"2026-06-15T19:00:00+03:00"}
    write_json(d/"mission.json",mission)
    bounds=spec["allowed"].buffer(200).bounds
    if spec["real"]:
        from terrain_sources import prepare_real_dem
        provenance=prepare_real_dem(d/"dem.tif",bounds)
    else:
        minx,miny,maxx,maxy=bounds
        w=math.ceil((maxx-minx)/30); h=math.ceil((maxy-miny)/30)
        with rasterio.open(d/"dem.tif","w",driver="GTiff",height=h,width=w,count=1,dtype="float32",
                           crs="EPSG:32637",transform=from_origin(minx,maxy,30,30),compress="deflate") as dst:
            dst.write(np.full((h,w),160,dtype="float32"),1)
        provenance=dict(kind="synthetic_flat_surface",height_m=160,resolution_m=30)
    write_json(d/"metadata.json",dict(scenario_id=directory.name if directory.name=="alternative" else spec["id"],
               description=spec["title"],vector_crs="EPSG:4326",metric_crs="EPSG:32637",
               temporal_altitude_reference="AMSL",height_reference="EGM2008" if spec["real"] else "synthetic_datum",
               surface_model="DSM" if spec["real"] else "synthetic_flat",terrain=provenance,
               real_elevation=spec["real"],synthetic_mission=True,not_operational_data=True,
               reference_generation={"angle_step_deg":180 if spec["id"].startswith("S11") else 15},
               trajectory_model="piecewise_linear_XYZ_AMSL_time",seed=20260920))
    scene_kml(d/"scene.kml",surveys+sites)
    write_json(directory/"scenario.json",dict(id=spec["id"],title=spec["title"],expected_status=spec["expected"],
               objectives=spec["objectives"],real_elevation=spec["real"],checks=spec["checks"],proof=spec.get("proof")))


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--only",nargs="*")
    args=parser.parse_args()
    for s in specs():
        if args.only and not any(s["id"].startswith(x) for x in args.only):
            continue
        d=ROOT/"scenarios"/s["id"]
        save(s,d)
        if s.get("alternative"):
            alt=dict(s,expected="SAFE",proof=None,sites=[(n,p,"both" if n=="CANDIDATE_NEAR" else r) for n,p,r in s["sites"]],
                     fleet=[dict(s["fleet"][0],start_site="CANDIDATE_NEAR",landing_site="CANDIDATE_NEAR")])
            # Activated site has a normal identifier, avoiding implicit candidate semantics.
            alt["sites"]=[("NEAR" if n=="CANDIDATE_NEAR" else n,p,r) for n,p,r in alt["sites"]]
            alt["fleet"][0].update(start_site="NEAR",landing_site="NEAR")
            save(alt,d/"alternative")
        print(s["id"],flush=True)


if __name__=="__main__":
    main()
