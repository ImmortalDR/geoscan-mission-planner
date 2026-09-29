"""M1 inputs inventory — inspect, obstacles/temporal layers, run_h1_scene."""
from __future__ import annotations

import json

from shapely.geometry import Point, Polygon

from h1_coverage.config import CoverageConfig
from h1_coverage.io.inspect import inspect_scene_dir
from h1_coverage.io.manifest import ENTRY_POINTS, SCENE_INPUTS
from h1_coverage.io.scene import load_scene
from h1_coverage.models import Mission, PayloadProfile, Scene, Site, SurveyJob, Wind
from h1_coverage.pipeline import run_h1_scene
from h1_coverage.geo import CrsPipeline

from paths import scene


def test_manifest_covers_known_files():
    names = {s.filename for s in SCENE_INPUTS}
    assert "survey_areas.geojson" in names
    assert "obstacles.geojson" in names
    assert "temporal_airspace.geojson" in names
    assert any("run_h1" in how for _, how in ENTRY_POINTS)


def test_inspect_s00():
    r = inspect_scene_dir(scene("S00_smoke_rgb"))
    assert r["load_ok"] is True
    assert r["files"]["survey_areas"]["present"]
    assert r["files"]["landing_sites"]["present"]
    assert r["summary"]["jobs"] >= 1
    assert r["summary"]["sites"] >= 1


def test_inspect_empty_dir(tmp_path):
    r = inspect_scene_dir(tmp_path)
    assert r["load_ok"] is False
    assert r["gaps"]


def test_load_obstacles_and_temporal(tmp_path):
    src = scene("S00_smoke_rgb")
    import shutil

    for name in (
        "survey_areas.geojson",
        "landing_sites.geojson",
        "mission.json",
        "fleet.json",
        "payload_catalog.json",
        "metadata.json",
        "allowed_airspace.geojson",
        "no_fly_zones.geojson",
    ):
        p = src / name
        if p.exists():
            shutil.copy(p, tmp_path / name)

    # obstacle near survey (WGS) — soft cut
    (tmp_path / "obstacles.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "id": "OBS1",
                            "horizontal_buffer_m": 5,
                            "hard": False,
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [37.605, 55.752],
                                    [37.6055, 55.752],
                                    [37.6055, 55.7525],
                                    [37.605, 55.7525],
                                    [37.605, 55.752],
                                ]
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "temporal_airspace.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"id": "TMP1", "hard": False},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [37.60, 55.75],
                                    [37.61, 55.75],
                                    [37.61, 55.76],
                                    [37.60, 55.76],
                                    [37.60, 55.75],
                                ]
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    s = load_scene(tmp_path)
    assert len(s.obstacles) == 1
    assert len(s.temporal_airspace) == 1
    assert any("obstacle" in w.lower() for w in s.warnings)
    assert any("temporal" in w.lower() for w in s.warnings)

    from h1_coverage.coverage.exclusions import apply_exclusions

    apply_exclusions(s, CoverageConfig())
    reasons = []
    for job in s.jobs:
        # recompute via apply already stored; check report path
        pass
    report = apply_exclusions(s, CoverageConfig())
    for j in report["jobs"]:
        reasons.extend(r["reason"] for r in j["reasons"])
    assert "temporal_airspace_present" in reasons


def test_run_h1_scene_in_memory():
    crs = CrsPipeline.for_point(37.6, 55.75)
    x0, y0 = crs.lonlat_to_xy(37.60, 55.75)
    x1, y1 = crs.lonlat_to_xy(37.61, 55.76)
    poly = Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    scene_obj = Scene(
        id="mem",
        crs=crs,
        jobs=[
            SurveyJob(
                id="J1",
                survey_type="rgb",
                payload_profile_id="p1",
                geom=poly,
                mode="area",
            )
        ],
        fleet=[],
        sites=[Site(id="S1", point=Point(poly.centroid.x, poly.centroid.y), role="both")],
        payloads={
            "p1": PayloadProfile(id="p1", type="rgb", nominal_agl_m=120, gsd_cm=3.0, side_overlap=0.7)
        },
        mission=Mission(wind=Wind()),
    )
    r = run_h1_scene(scene_obj, CoverageConfig(strict_coverage=False))
    assert r.bundle.data["schema_version"] == "gmp.h1_h2.v2"
    assert len(r.coverage.tasks) >= 1
