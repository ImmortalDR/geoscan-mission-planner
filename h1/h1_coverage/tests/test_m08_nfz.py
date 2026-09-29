"""M8 NFZManager — contract h1.m8.nfz.v1"""
from __future__ import annotations

import json

from shapely.geometry import LineString

from h1_coverage.config import CoverageConfig
from h1_coverage.contracts import check_m8_effective, check_m8_g1, geojson_shape
from h1_coverage.coverage.engine import build_coverage
from h1_coverage.coverage.exclusions import compute_effective_area
from h1_coverage.coverage.survey import derive_payload_geometry
from h1_coverage.io.scene import load_scene
from h1_coverage.models import PayloadProfile, Scene, SurveyJob, Zone
from h1_coverage.modules import by_id

from paths import H1_FIX, scene


def test_module_registry_m8():
    assert by_id("M8").contract_id == "h1.m8.nfz.v1"


def test_m8_nfz_metric_fixture():
    raw = json.loads((H1_FIX / "m8_nfz_metric.json").read_text())
    survey = geojson_shape(raw["survey"])
    nfz = geojson_shape(raw["nfz"])
    job = SurveyJob(id="j", survey_type="rgb", payload_profile_id="p", geom=survey)
    payload = PayloadProfile(id="p", type="rgb", nominal_agl_m=120, agl_m=120, swath_spacing_m=40)
    sc = Scene(
        id="m8",
        crs=None,  # type: ignore[arg-type]
        jobs=[job],
        fleet=[],
        sites=[],
        payloads={"p": payload},
        mission=__import__("h1_coverage.models", fromlist=["Mission"]).Mission(),
        no_fly_zones=[Zone(id="n", geom=nfz, kind="no_fly_zone", hard=True)],
    )
    # Scene requires crs — use a dummy pipeline via load of real scene pattern
    from h1_coverage.geo import CrsPipeline

    sc.crs = CrsPipeline.for_point(37.6, 55.75)
    derive_payload_geometry(payload)
    res = compute_effective_area(sc, job, payload, CoverageConfig(nfz_buffer_m=raw["nfz_buffer_m"]))
    check_m8_effective(res.effective, nfz)
    assert res.effective.area < survey.area


def test_m8_nfz_no_intersect_s08():
    s = load_scene(scene("S08_fixed_wing_turnaround"))
    r = build_coverage(s, CoverageConfig(strict_coverage=False))
    hard = None
    from h1_coverage.geo import union_all

    hard = union_all([z.geom for z in s.no_fly_zones if z.hard])
    trs = [tr for t in r.tasks.values() for tr in t.transects]
    check_m8_g1(trs, hard)
    for tr in trs:
        assert not LineString(tr.coords).intersects(hard)
