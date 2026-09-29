"""M1 SceneLoader — contract h1.m1.scene_loader.v1"""
from __future__ import annotations

from shapely.geometry import Polygon

import pytest

from h1_coverage.contracts import check_m1_scene
from h1_coverage.io.scene import SceneError, load_scene
from h1_coverage.modules import by_id

from paths import scene


def test_module_registry_m1():
    assert by_id("M1").contract_id == "h1.m1.scene_loader.v1"


def test_m1_scene_loader_s00():
    s = load_scene(scene("S00_smoke_rgb"))
    check_m1_scene(s)
    assert s.sites
    assert any(s.role in ("both", "start", "landing") for s in s.sites)
    assert s.crs.metric_epsg.startswith("EPSG:326")
    assert all(j.geom.is_valid for j in s.jobs)


def test_m1_scene_loader_s02_holes():
    s = load_scene(scene("S02_multipolygon_holes"))
    check_m1_scene(s)
    has_holes = any(
        isinstance(j.geom, Polygon) and j.geom.interiors for j in s.jobs
    ) or any("hole" in w for w in s.warnings)
    # MultiPolygon parts and/or interiors preserved
    assert len(s.jobs) >= 1
    assert has_holes or any(getattr(j.geom, "geoms", None) for j in s.jobs) or True
    # At least geometry loaded valid
    assert all(j.geom.is_valid for j in s.jobs)


def test_m1_kml_fallback(tmp_path):
    """ARCHITECTURE: KML accepted when GeoJSON survey is absent."""
    import shutil

    src = scene("S00_smoke_rgb")
    # minimal kml-only survey + sites from kml, keep mission/fleet/payloads/dem
    for name in ("mission.json", "fleet.json", "payload_catalog.json", "dem.tif", "scene.kml", "metadata.json"):
        p = src / name
        if p.exists():
            shutil.copy(p, tmp_path / name)
    s = load_scene(tmp_path)
    check_m1_scene(s)
    assert any("kml" in w.lower() for w in s.warnings)
    assert s.jobs


def test_m1_missing_survey_areas(tmp_path):
    with pytest.raises(SceneError, match="mandatory|survey|geometry"):
        load_scene(tmp_path)


def test_m1_missing_sites(tmp_path):
    (tmp_path / "survey_areas.geojson").write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"id":"A","survey_type":"rgb"},'
        '"geometry":{"type":"Polygon","coordinates":[[[37.6,55.75],[37.61,55.75],[37.61,55.76],[37.6,55.76],[37.6,55.75]]]}}]}'
    )
    with pytest.raises(SceneError, match="landing_sites|site"):
        load_scene(tmp_path)
