"""Buggy / adversarial input-file cases for M1."""
from __future__ import annotations

import json

import pytest

from h1_coverage.io import encoding as enc
from h1_coverage.io import geojson as gj
from h1_coverage.io.scene import SceneError, load_scene

_LON0, _LAT0 = 37.60, 55.75
_LON1, _LAT1 = 37.61, 55.76


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def _poly(coords=None, **props):
    ring = coords or [
        [_LON0, _LAT0],
        [_LON1, _LAT0],
        [_LON1, _LAT1],
        [_LON0, _LAT1],
        [_LON0, _LAT0],
    ]
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


def _point(lon, lat, **props):
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }


def _base(d, *, survey=None, sites=None, extras=None, fleet=None, mission=None):
    d.mkdir(parents=True, exist_ok=True)
    if survey is not None:
        (d / "survey_areas.geojson").write_text(json.dumps(_fc(survey)), encoding="utf-8")
    if sites is not None:
        (d / "landing_sites.geojson").write_text(json.dumps(_fc(sites)), encoding="utf-8")
    (d / "mission.json").write_text(json.dumps(mission or {}), encoding="utf-8")
    (d / "payload_catalog.json").write_text(
        json.dumps(
            {
                "payload_profiles": [
                    {"id": "RGB", "type": "rgb", "nominal_agl_m": 120, "gsd_cm": 3.0, "side_overlap": 0.7}
                ]
            }
        ),
        encoding="utf-8",
    )
    if fleet is not None:
        (d / "fleet.json").write_text(json.dumps(fleet), encoding="utf-8")
    if extras:
        for name, content in extras.items():
            path = d / name
            if isinstance(content, bytes):
                path.write_bytes(content)
            elif isinstance(content, (dict, list)):
                path.write_text(json.dumps(content), encoding="utf-8")
            else:
                path.write_text(str(content), encoding="utf-8")


def test_bug_metres_as_lonlat(tmp_path):
    """UTM-like metres dumped into GeoJSON → hard fail."""
    ring = [[500000.0, 6180000.0], [500200.0, 6180000.0], [500200.0, 6180200.0], [500000.0, 6180200.0], [500000.0, 6180000.0]]
    _base(
        tmp_path,
        survey=[_poly(ring, id="BAD", survey_type="rgb", payload_profile="RGB")],
        sites=[_point(500100.0, 6180100.0, id="S1", role="both")],
    )
    with pytest.raises(SceneError, match="metres|projected|lon/lat"):
        load_scene(tmp_path)


def test_bug_self_intersecting_repaired(tmp_path):
    # classic bowtie
    ring = [
        [_LON0, _LAT0],
        [_LON1, _LAT1],
        [_LON1, _LAT0],
        [_LON0, _LAT1],
        [_LON0, _LAT0],
    ]
    _base(
        tmp_path,
        survey=[_poly(ring, id="BOWTIE", survey_type="rgb", payload_profile="RGB")],
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
    )
    s = load_scene(tmp_path)
    assert s.jobs
    assert any("invalid" in w.lower() or "repaired" in w.lower() for w in s.warnings)


def test_bug_duplicate_job_ids(tmp_path):
    _base(
        tmp_path,
        survey=[
            _poly(id="SAME", survey_type="rgb", payload_profile="RGB"),
            _poly(
                [
                    [_LON0 + 0.02, _LAT0],
                    [_LON1 + 0.02, _LAT0],
                    [_LON1 + 0.02, _LAT1],
                    [_LON0 + 0.02, _LAT1],
                    [_LON0 + 0.02, _LAT0],
                ],
                id="SAME",
                survey_type="rgb",
                payload_profile="RGB",
            ),
        ],
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
    )
    with pytest.raises(SceneError, match="duplicate survey job"):
        load_scene(tmp_path)


def test_bug_duplicate_site_ids(tmp_path):
    _base(
        tmp_path,
        survey=[_poly(id="A", survey_type="rgb", payload_profile="RGB")],
        sites=[
            _point(_LON0 + 0.001, _LAT0 + 0.001, id="DUP", role="both"),
            _point(_LON0 + 0.002, _LAT0 + 0.002, id="DUP", role="landing"),
        ],
    )
    with pytest.raises(SceneError, match="duplicate site"):
        load_scene(tmp_path)


def test_bug_empty_fleet_file(tmp_path):
    _base(
        tmp_path,
        survey=[_poly(id="A", survey_type="rgb", payload_profile="RGB")],
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
        fleet={"uavs": []},
    )
    with pytest.raises(SceneError, match="fleet.json has no uavs"):
        load_scene(tmp_path)


def test_bug_require_fleet_missing(tmp_path):
    _base(
        tmp_path,
        survey=[_poly(id="A", survey_type="rgb", payload_profile="RGB")],
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
        mission={"require_fleet": True},
    )
    with pytest.raises(SceneError, match="require_fleet"):
        load_scene(tmp_path)


def test_bug_corrupt_dem(tmp_path):
    _base(
        tmp_path,
        survey=[_poly(id="A", survey_type="rgb", payload_profile="RGB")],
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
        extras={"dem.tif": b"not-a-geotiff-at-all"},
    )
    s = load_scene(tmp_path)
    assert any("dem.tif" in w.lower() and "fail" in w.lower() for w in s.warnings)
    assert not s.dem.available


def test_bug_feature_missing_type_skipped(tmp_path):
    feats = [
        {
            # no "type": "Feature"
            "properties": {"id": "NO_TYPE", "survey_type": "rgb", "payload_profile": "RGB"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [_LON0, _LAT0],
                        [_LON1, _LAT0],
                        [_LON1, _LAT1],
                        [_LON0, _LAT1],
                        [_LON0, _LAT0],
                    ]
                ],
            },
        },
        _poly(id="OK", survey_type="rgb", payload_profile="RGB"),
    ]
    _base(
        tmp_path,
        survey=feats,
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
    )
    s = load_scene(tmp_path)
    assert len(s.jobs) == 1
    assert s.jobs[0].id == "OK"
    assert any("missing type" in w for w in s.warnings)


def test_bug_utf16_bom_mission(tmp_path):
    _base(
        tmp_path,
        survey=[_poly(id="A", survey_type="rgb", payload_profile="RGB")],
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
    )
    payload = json.dumps({"wind": {"speed_ms": 2.5, "direction_deg_from": 90}}).encode("utf-16")
    (tmp_path / "mission.json").write_bytes(payload)
    s = load_scene(tmp_path)
    assert s.mission.wind.speed_ms == 2.5


def test_bug_utf8_bom_geojson(tmp_path):
    body = json.dumps(
        _fc([_poly(id="A", survey_type="rgb", payload_profile="RGB")])
    ).encode("utf-8")
    (tmp_path / "survey_areas.geojson").write_bytes(b"\xef\xbb\xbf" + body)
    (tmp_path / "landing_sites.geojson").write_text(
        json.dumps(_fc([_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")])),
        encoding="utf-8",
    )
    (tmp_path / "mission.json").write_text("{}", encoding="utf-8")
    (tmp_path / "payload_catalog.json").write_text(
        json.dumps({"payload_profiles": [{"id": "RGB", "type": "rgb", "nominal_agl_m": 120, "gsd_cm": 3}]}),
        encoding="utf-8",
    )
    s = load_scene(tmp_path)
    assert s.jobs[0].id == "A"


def test_bug_giant_geojson(tmp_path, monkeypatch):
    monkeypatch.setattr(enc, "MAX_GEOJSON_BYTES", 200)
    monkeypatch.setattr(gj, "MAX_GEOJSON_BYTES", 200)
    # also used via encoding.read_json_file default — force through geojson path
    _base(
        tmp_path,
        survey=[_poly(id="A", survey_type="rgb", payload_profile="RGB")],
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
    )
    # inflate survey file past 200 bytes
    (tmp_path / "survey_areas.geojson").write_text("{" + ("x" * 500) + "}", encoding="utf-8")
    with pytest.raises(SceneError, match="too large"):
        load_scene(tmp_path)
