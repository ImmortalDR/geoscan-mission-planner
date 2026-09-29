"""Absolute-max backlog: S01 stress, corridor E2E, W-06/W-07, F2C status."""
from __future__ import annotations

import json
import time
from pathlib import Path

from shapely.geometry import LineString

from h1_coverage.config import CoverageConfig
from h1_coverage.coverage.backend import backend_status
from h1_coverage.coverage.corridor import corridor_polygon, preferred_corridor_angles
from h1_coverage.coverage.engine import build_coverage
from h1_coverage.export import export_angle_report, export_html_map
from h1_coverage.io.scene import load_scene
from h1_coverage.models import Mission, PayloadProfile, Scene, SurveyJob, Wind
from h1_coverage.geo import CrsPipeline
from h1_coverage.pipeline import run_h1

from paths import H1_FIX, scene


def test_a01_f2c_status_documented():
    st = backend_status()
    assert "active" in st and "fields2cover_importable" in st
    assert st["active"] == "diy_lawnmower_v1"
    assert st.get("fields2cover_bridge_wired") is False


def test_s01_100km2_stress(tmp_path: Path):
    sdir = scene("S01_full_customer_acceptance_100km2")
    cfg = CoverageConfig(
        strict_coverage=True,
        angle_step_deg=30.0,
        keep_candidates=3,
        headland_m=0.0,
        max_tasks_per_job=40,
    )
    t0 = time.time()
    r = run_h1(sdir, cfg)
    elapsed = time.time() - t0
    assert len(r.coverage.tasks) >= 1
    assert r.coverage.coverage_percent >= cfg.coverage_pass_percent
    assert all(job["coverage_percent"] >= cfg.coverage_pass_percent for job in r.coverage.per_job)
    assert elapsed < 180.0
    out = tmp_path
    r.bundle.write_json(out / "S01.bundle.json")
    export_html_map(r.scene, r.coverage, out / "S01_map.html")
    export_angle_report(r.scene, r.coverage, out / "S01_angles.md")


def test_t03_corridor_e2e_from_centerline():
    """LineString + half_width → corridor polygon → coverage with along angle preferred."""
    from h1_coverage.geo import CrsPipeline

    raw = json.loads((H1_FIX / "m_corridor_centerline.geojson").read_text())
    coords = raw["features"][0]["geometry"]["coordinates"]
    half = float(raw["features"][0]["properties"]["half_width_m"])
    crs = CrsPipeline.for_point(coords[0][0], coords[0][1])
    line_m = LineString([crs.lonlat_to_xy(lon, lat) for lon, lat in coords])
    poly = corridor_polygon(line_m, half)
    assert poly.area > 0
    pref = preferred_corridor_angles(poly)
    assert any(l.startswith("corridor") for _, l in pref)

    cam = None
    payload = PayloadProfile(id="rgb_default", type="rgb", nominal_agl_m=120, gsd_cm=5.0, side_overlap=0.6)
    from h1_coverage.coverage.survey import derive_payload_geometry
    from h1_coverage.models import CameraModel

    payload.camera = CameraModel("c", 6000, 4000, 16.0, 3.9)
    derive_payload_geometry(payload)
    job = SurveyJob(
        id="ROAD_CORRIDOR",
        survey_type="rgb",
        payload_profile_id="rgb_default",
        geom=poly,
        mode="corridor",
    )
    sc = Scene(
        id="corridor_e2e",
        crs=crs,
        jobs=[job],
        fleet=[],
        sites=[],
        payloads={"rgb_default": payload},
        mission=Mission(wind=Wind(0, 0)),
    )
    # need a site for nothing in build_coverage — OK without sites
    cov = build_coverage(sc, CoverageConfig(strict_coverage=False, keep_candidates=4, angle_step_deg=45))
    assert cov.tasks
    assert any("corridor" in w for w in cov.warnings)
    labels = [c["label"] for c in cov.candidates.get("ROAD_CORRIDOR", [])]
    # preferred corridor labels should appear among candidates when selected path used them
    assert cov.coverage_percent > 50


def test_w06_w07_badge_and_report(tmp_path: Path):
    r = run_h1(scene("S00_smoke_rgb"), CoverageConfig(strict_coverage=False))
    html = tmp_path / "m.html"
    md = tmp_path / "r.md"
    export_html_map(r.scene, r.coverage, html)
    export_angle_report(r.scene, r.coverage, md)
    h = html.read_text()
    assert "badge" in h and "%" in h
    text = md.read_text()
    assert "Coverage badge" in text and "top candidates" in text
