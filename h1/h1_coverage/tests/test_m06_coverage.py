"""M6 CoverageEngine — contract h1.m6.coverage_engine.v1"""
from __future__ import annotations

from shapely.geometry import LineString, Polygon, box

from h1_coverage.config import CoverageConfig
from h1_coverage.contracts import check_m6_coverage
from h1_coverage.coverage.backend import DiySweepBackend, get_sweep_backend
from h1_coverage.coverage.engine import build_coverage
from h1_coverage.coverage.candidates import SweepCandidate
from h1_coverage.coverage.sweep import generate_transects
from h1_coverage.geo import CrsPipeline
from h1_coverage.io.scene import load_scene
from h1_coverage.modules import by_id
from h1_coverage.models import Mission, PayloadProfile, Scene, SurveyJob, Transect

from paths import scene


def test_module_registry_m6():
    assert by_id("M6").contract_id == "h1.m6.coverage_engine.v1"


def test_m6_coverage_s00():
    s = load_scene(scene("S00_smoke_rgb"))
    r = build_coverage(s, CoverageConfig(strict_coverage=False))
    check_m6_coverage(r, min_pct=99.0)


def test_m6_diy_backend_is_explicit():
    from h1_coverage.coverage.backend import backend_status

    backend = get_sweep_backend()
    # Production path is DIY; F2C name must not be active without a wired bridge.
    assert backend.name == "diy_lawnmower_v1"
    assert isinstance(backend, DiySweepBackend)
    st = backend_status()
    assert st["active"] == "diy_lawnmower_v1"
    assert st["fields2cover_bridge_wired"] is False
    assert "note" in st


def test_m6_overshoot_stays_inside_effective():
    outer = Polygon([(0, 0), (400, 0), (400, 300), (0, 300)])
    hole = Polygon([(150, 100), (250, 100), (250, 200), (150, 200)])
    area = Polygon(outer.exterior.coords, [list(hole.exterior.coords)])
    lines = generate_transects(area, spacing=40, angle_deg=0, job_id="j", overshoot_m=25)
    assert lines
    for t in lines:
        assert area.buffer(0.5).contains(LineString(t.coords))


def test_cell_narrower_than_swath_receives_a_center_pass():
    area = box(0, 0, 1000, 20)
    transects = generate_transects(area, spacing=50, angle_deg=0, job_id="narrow")
    assert len(transects) == 1
    assert area.covered_by(LineString(transects[0].coords).buffer(25))


def test_complete_candidate_is_preferred_over_cheaper_missing_strips(monkeypatch):
    area = box(0, 0, 100, 90)
    def candidate(ys, score):
        lines = [Transect(coords=[(0, y), (100, y)], length_m=100, job_id="job") for y in ys]
        return SweepCandidate(0, lines, 100 * len(lines), 0, len(lines) - 1, 0, 0, score, str(score))

    incomplete, complete = candidate([45], 100), candidate([15, 45, 75], 300)
    def candidates(**kwargs):
        assert kwargs["limit_candidates"] is False
        return [incomplete, complete]

    monkeypatch.setattr("h1_coverage.coverage.engine.build_candidates", candidates)
    scene = Scene(
        id="coverage_ranking",
        crs=CrsPipeline.for_point(37.5, 55.7),
        jobs=[SurveyJob(id="job", survey_type="geophysics", payload_profile_id="p", geom=area)],
        fleet=[], sites=[], mission=Mission(),
        payloads={"p": PayloadProfile(id="p", type="geophysics", nominal_agl_m=80, line_spacing_m=30)},
    )
    result = build_coverage(scene, CoverageConfig(keep_candidates=1))
    assert result.coverage_percent >= 99.9
    assert len(result.candidates["job"]) == 1
    assert next(c for c in result.candidates["job"] if c["selected"])["label"] == "300"


def test_headland_does_not_remove_uncovered_area_from_denominator(monkeypatch):
    line = Transect(coords=[(20, 50), (80, 50)], length_m=60, job_id="job")
    candidate = SweepCandidate(0, [line], 60, 0, 0, 0, 0, 60, "short")
    monkeypatch.setattr("h1_coverage.coverage.engine.build_candidates", lambda **kwargs: [candidate])
    scene = Scene(
        id="coverage_headland", crs=CrsPipeline.for_point(37.5, 55.7),
        jobs=[SurveyJob(id="job", survey_type="geophysics", payload_profile_id="p", geom=box(0, 0, 100, 100))],
        fleet=[], sites=[], mission=Mission(),
        payloads={"p": PayloadProfile(id="p", type="geophysics", nominal_agl_m=80, line_spacing_m=100)},
    )
    result = build_coverage(scene, CoverageConfig(headland_m=20))
    assert result.coverage_percent < 99.9
    assert abs(result.coverage_percent - result.per_job[0]["coverage_percent"]) < 0.001
    assert any("Gate B:" in warning for warning in result.warnings)
