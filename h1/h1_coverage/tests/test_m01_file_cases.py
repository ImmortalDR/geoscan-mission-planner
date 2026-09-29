"""Matrix of input-file cases for M1 SceneLoader.

Each case builds a minimal scene directory under tmp_path and asserts load
behaviour (ok / SceneError / warnings / structure).
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from h1_coverage.io.scene import SceneError, load_scene


# Roughly Moscow — matches conformance lon/lat band
_LON0, _LAT0 = 37.60, 55.75
_LON1, _LAT1 = 37.61, 55.76


def _fc(features: list[dict]) -> dict:
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


def _line(**props):
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {
            "type": "LineString",
            "coordinates": [[_LON0, _LAT0], [_LON1, _LAT0], [_LON1, _LAT1]],
        },
    }


def _write_base(d: Path, *, survey=None, sites=None, extras: dict | None = None):
    d.mkdir(parents=True, exist_ok=True)
    if survey is not None:
        (d / "survey_areas.geojson").write_text(json.dumps(_fc(survey)), encoding="utf-8")
    if sites is not None:
        (d / "landing_sites.geojson").write_text(json.dumps(_fc(sites)), encoding="utf-8")
    (d / "mission.json").write_text("{}", encoding="utf-8")
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
    if extras:
        for name, content in extras.items():
            path = d / name
            if isinstance(content, (dict, list)):
                path.write_text(json.dumps(content), encoding="utf-8")
            else:
                path.write_text(str(content), encoding="utf-8")


def test_case_polygon_area(tmp_path):
    _write_base(
        tmp_path,
        survey=[_poly(id="A", survey_type="rgb", payload_profile="RGB")],
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
    )
    s = load_scene(tmp_path)
    assert len(s.jobs) == 1
    assert s.jobs[0].mode == "area"
    assert s.jobs[0].geom.geom_type in ("Polygon", "MultiPolygon")


def test_case_multipolygon(tmp_path):
    ring_a = [[_LON0, _LAT0], [_LON0 + 0.005, _LAT0], [_LON0 + 0.005, _LAT0 + 0.005], [_LON0, _LAT0 + 0.005], [_LON0, _LAT0]]
    ring_b = [[_LON1, _LAT1], [_LON1 + 0.005, _LAT1], [_LON1 + 0.005, _LAT1 + 0.005], [_LON1, _LAT1 + 0.005], [_LON1, _LAT1]]
    feat = {
        "type": "Feature",
        "properties": {"id": "MP", "survey_type": "rgb", "payload_profile": "RGB"},
        "geometry": {"type": "MultiPolygon", "coordinates": [[ring_a], [ring_b]]},
    }
    _write_base(
        tmp_path,
        survey=[feat],
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
    )
    s = load_scene(tmp_path)
    assert s.jobs[0].geom.geom_type in ("MultiPolygon", "Polygon")


def test_case_polygon_with_hole(tmp_path):
    outer = [[_LON0, _LAT0], [_LON1, _LAT0], [_LON1, _LAT1], [_LON0, _LAT1], [_LON0, _LAT0]]
    hole = [
        [_LON0 + 0.002, _LAT0 + 0.002],
        [_LON1 - 0.002, _LAT0 + 0.002],
        [_LON1 - 0.002, _LAT1 - 0.002],
        [_LON0 + 0.002, _LAT1 - 0.002],
        [_LON0 + 0.002, _LAT0 + 0.002],
    ]
    feat = {
        "type": "Feature",
        "properties": {"id": "H", "survey_type": "rgb", "payload_profile": "RGB"},
        "geometry": {"type": "Polygon", "coordinates": [outer, hole]},
    }
    _write_base(
        tmp_path,
        survey=[feat],
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
    )
    s = load_scene(tmp_path)
    assert any("hole" in w for w in s.warnings)
    assert _hole_from_job(s) >= 1


def _hole_from_job(s) -> int:
    g = s.jobs[0].geom
    if g.geom_type == "Polygon":
        return len(g.interiors)
    if g.geom_type == "MultiPolygon":
        return sum(len(p.interiors) for p in g.geoms)
    return 0


def test_case_linestring_corridor(tmp_path):
    _write_base(
        tmp_path,
        survey=[_line(id="C", survey_type="rgb", payload_profile="RGB", half_width_m=30)],
        sites=[_point(_LON0, _LAT0, id="S1", role="both")],
    )
    s = load_scene(tmp_path)
    assert s.jobs[0].mode == "corridor"
    assert s.jobs[0].geom.geom_type in ("Polygon", "MultiPolygon")
    assert any("corridor" in w.lower() or "LineString" in w for w in s.warnings)


def test_case_multilinestring_corridor(tmp_path):
    feat = {
        "type": "Feature",
        "properties": {"id": "ML", "survey_type": "rgb", "payload_profile": "RGB", "half_width_m": 25},
        "geometry": {
            "type": "MultiLineString",
            "coordinates": [
                [[_LON0, _LAT0], [_LON1, _LAT0]],
                [[_LON1, _LAT0], [_LON1, _LAT1]],
            ],
        },
    }
    _write_base(
        tmp_path,
        survey=[feat],
        sites=[_point(_LON0, _LAT0, id="S1", role="both")],
    )
    s = load_scene(tmp_path)
    assert s.jobs[0].mode == "corridor"


def test_case_multi_jobs(tmp_path):
    _write_base(
        tmp_path,
        survey=[
            _poly(id="RGB", survey_type="rgb", payload_profile="RGB"),
            _poly(
                [
                    [_LON0 + 0.02, _LAT0],
                    [_LON1 + 0.02, _LAT0],
                    [_LON1 + 0.02, _LAT1],
                    [_LON0 + 0.02, _LAT1],
                    [_LON0 + 0.02, _LAT0],
                ],
                id="IR",
                survey_type="rgb",
                payload_profile="RGB",
            ),
        ],
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
    )
    s = load_scene(tmp_path)
    assert len(s.jobs) == 2


def test_case_soft_and_hard_nfz(tmp_path):
    _write_base(
        tmp_path,
        survey=[_poly(id="A", survey_type="rgb", payload_profile="RGB")],
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
        extras={
            "no_fly_zones.geojson": _fc(
                [
                    _poly(
                        [
                            [_LON0 + 0.003, _LAT0 + 0.003],
                            [_LON0 + 0.004, _LAT0 + 0.003],
                            [_LON0 + 0.004, _LAT0 + 0.004],
                            [_LON0 + 0.003, _LAT0 + 0.004],
                            [_LON0 + 0.003, _LAT0 + 0.003],
                        ],
                        id="HARD",
                        hard=True,
                    ),
                    _poly(
                        [
                            [_LON0 + 0.005, _LAT0 + 0.005],
                            [_LON0 + 0.006, _LAT0 + 0.005],
                            [_LON0 + 0.006, _LAT0 + 0.006],
                            [_LON0 + 0.005, _LAT0 + 0.006],
                            [_LON0 + 0.005, _LAT0 + 0.005],
                        ],
                        id="SOFT",
                        hard=False,
                    ),
                ]
            )
        },
    )
    s = load_scene(tmp_path)
    assert sum(1 for z in s.no_fly_zones if z.hard) == 1
    assert sum(1 for z in s.no_fly_zones if not z.hard) == 1


def test_case_obstacles_buffer(tmp_path):
    _write_base(
        tmp_path,
        survey=[_poly(id="A", survey_type="rgb", payload_profile="RGB")],
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
        extras={
            "obstacles.geojson": _fc(
                [
                    _poly(
                        [
                            [_LON0 + 0.003, _LAT0 + 0.003],
                            [_LON0 + 0.0035, _LAT0 + 0.003],
                            [_LON0 + 0.0035, _LAT0 + 0.0035],
                            [_LON0 + 0.003, _LAT0 + 0.0035],
                            [_LON0 + 0.003, _LAT0 + 0.003],
                        ],
                        id="TOWER",
                        horizontal_buffer_m=20,
                    )
                ]
            )
        },
    )
    s = load_scene(tmp_path)
    assert len(s.obstacles) == 1
    assert any("obstacle" in w.lower() for w in s.warnings)


def test_case_temporal_airspace(tmp_path):
    _write_base(
        tmp_path,
        survey=[_poly(id="A", survey_type="rgb", payload_profile="RGB")],
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
        extras={"temporal_airspace.geojson": _fc([_poly(id="T1")])},
    )
    s = load_scene(tmp_path)
    assert len(s.temporal_airspace) == 1
    assert any("temporal" in w.lower() for w in s.warnings)


def test_case_empty_optional_layers(tmp_path):
    _write_base(
        tmp_path,
        survey=[_poly(id="A", survey_type="rgb", payload_profile="RGB")],
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
        extras={
            "no_fly_zones.geojson": _fc([]),
            "obstacles.geojson": _fc([]),
            "temporal_airspace.geojson": _fc([]),
            "allowed_airspace.geojson": _fc([]),
        },
    )
    s = load_scene(tmp_path)
    assert s.no_fly_zones == []
    assert s.obstacles == []
    assert any("no DEM" in w for w in s.warnings)


def test_case_site_polygon_centroid(tmp_path):
    _write_base(
        tmp_path,
        survey=[_poly(id="A", survey_type="rgb", payload_profile="RGB")],
        sites=[_poly(id="PAD", role="both")],  # polygon pad → centroid
    )
    s = load_scene(tmp_path)
    assert len(s.sites) == 1
    assert s.sites[0].point.geom_type == "Point"


def test_case_site_roles_start_landing(tmp_path):
    _write_base(
        tmp_path,
        survey=[_poly(id="A", survey_type="rgb", payload_profile="RGB")],
        sites=[
            _point(_LON0 + 0.001, _LAT0 + 0.001, id="START", role="start"),
            _point(_LON0 + 0.002, _LAT0 + 0.002, id="LAND", role="landing"),
            _point(_LON0 + 0.003, _LAT0 + 0.003, id="RES", role="reserve"),
        ],
    )
    s = load_scene(tmp_path)
    roles = {x.id: x.role for x in s.sites}
    assert roles["START"] == "start"
    assert roles["LAND"] == "landing"
    assert roles["RES"] == "reserve"


def test_case_null_geometry_skipped(tmp_path):
    feats = [
        {"type": "Feature", "properties": {"id": "BAD"}, "geometry": None},
        _poly(id="OK", survey_type="rgb", payload_profile="RGB"),
    ]
    _write_base(
        tmp_path,
        survey=feats,
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
    )
    s = load_scene(tmp_path)
    assert len(s.jobs) == 1
    assert s.jobs[0].id == "OK"


def test_case_point_survey_rejected(tmp_path):
    _write_base(
        tmp_path,
        survey=[_point(_LON0, _LAT0, id="P", survey_type="rgb")],
        sites=[_point(_LON0, _LAT0, id="S1", role="both")],
    )
    with pytest.raises(SceneError, match="mandatory survey|Point|no survey"):
        load_scene(tmp_path)


def test_case_bad_mission_json(tmp_path):
    _write_base(
        tmp_path,
        survey=[_poly(id="A", survey_type="rgb", payload_profile="RGB")],
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="S1", role="both")],
    )
    (tmp_path / "mission.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(SceneError, match="mission.json|invalid JSON"):
        load_scene(tmp_path)


def test_case_bad_geojson(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "survey_areas.geojson").write_text("not-json", encoding="utf-8")
    (tmp_path / "landing_sites.geojson").write_text(
        json.dumps(_fc([_point(_LON0, _LAT0, id="S1", role="both")])), encoding="utf-8"
    )
    with pytest.raises(SceneError, match="invalid JSON|survey"):
        load_scene(tmp_path)


def test_case_reserve_only_sites_fail(tmp_path):
    _write_base(
        tmp_path,
        survey=[_poly(id="A", survey_type="rgb", payload_profile="RGB")],
        sites=[_point(_LON0 + 0.001, _LAT0 + 0.001, id="R1", role="reserve")],
    )
    with pytest.raises(SceneError, match="start/landing/both"):
        load_scene(tmp_path)


def test_case_kml_linestring_corridor(tmp_path):
    kml = textwrap.dedent(
        f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <kml xmlns="http://www.opengis.net/kml/2.2">
          <Document>
            <Placemark>
              <name>CORRIDOR</name>
              <styleUrl>#survey</styleUrl>
              <LineString>
                <coordinates>{_LON0},{_LAT0},0 {_LON1},{_LAT0},0 {_LON1},{_LAT1},0</coordinates>
              </LineString>
            </Placemark>
            <Placemark>
              <name>BASE</name>
              <description>both</description>
              <Point><coordinates>{_LON0},{_LAT0},0</coordinates></Point>
            </Placemark>
          </Document>
        </kml>
        """
    )
    (tmp_path / "scene.kml").write_text(kml, encoding="utf-8")
    (tmp_path / "mission.json").write_text("{}", encoding="utf-8")
    (tmp_path / "payload_catalog.json").write_text(
        json.dumps({"payload_profiles": [{"id": "RGB", "type": "rgb", "nominal_agl_m": 120, "gsd_cm": 3}]}),
        encoding="utf-8",
    )
    s = load_scene(tmp_path)
    assert any("kml" in w.lower() for w in s.warnings)
    assert s.jobs[0].mode == "corridor"


def test_case_kml_nfz_fallback(tmp_path):
    kml = textwrap.dedent(
        f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <kml xmlns="http://www.opengis.net/kml/2.2">
          <Document>
            <Placemark>
              <name>AREA</name>
              <styleUrl>#survey</styleUrl>
              <Polygon><outerBoundaryIs><LinearRing>
                <coordinates>
                  {_LON0},{_LAT0},0 {_LON1},{_LAT0},0 {_LON1},{_LAT1},0 {_LON0},{_LAT1},0 {_LON0},{_LAT0},0
                </coordinates>
              </LinearRing></outerBoundaryIs></Polygon>
            </Placemark>
            <Placemark>
              <name>NFZ1</name>
              <styleUrl>#nfz</styleUrl>
              <Polygon><outerBoundaryIs><LinearRing>
                <coordinates>
                  {_LON0+0.003},{_LAT0+0.003},0 {_LON0+0.004},{_LAT0+0.003},0
                  {_LON0+0.004},{_LAT0+0.004},0 {_LON0+0.003},{_LAT0+0.004},0 {_LON0+0.003},{_LAT0+0.003},0
                </coordinates>
              </LinearRing></outerBoundaryIs></Polygon>
            </Placemark>
            <Placemark>
              <name>BASE</name>
              <Point><coordinates>{_LON0+0.001},{_LAT0+0.001},0</coordinates></Point>
            </Placemark>
          </Document>
        </kml>
        """
    )
    (tmp_path / "scene.kml").write_text(kml, encoding="utf-8")
    (tmp_path / "mission.json").write_text("{}", encoding="utf-8")
    s = load_scene(tmp_path)
    assert any(z.kind == "no_fly_zone" for z in s.no_fly_zones)
    assert any("NFZ" in w or "nfz" in w.lower() for w in s.warnings)


def test_conformance_s01_obstacles_temporal():
    from paths import scene

    s = load_scene(scene("S01_full_customer_acceptance_100km2"))
    assert len(s.jobs) >= 2
    assert len(s.obstacles) >= 1
    assert len(s.temporal_airspace) >= 1


def test_conformance_s03_temporal():
    from paths import scene

    s = load_scene(scene("S03_temporal_airspace_daylight"))
    assert len(s.temporal_airspace) >= 1
