"""M5 SurveyModel — contract h1.m5.survey.v1"""
from __future__ import annotations

import json

from h1_coverage.config import MIN_SAFE_AGL_M
from h1_coverage.contracts import check_m5_payload
from h1_coverage.coverage.survey import derive_payload_geometry
from h1_coverage.models import CameraModel, PayloadProfile
from h1_coverage.modules import by_id

from paths import H1_FIX


def test_module_registry_m5():
    assert by_id("M5").contract_id == "h1.m5.survey.v1"


def test_m5_gsd_and_swath():
    raw = json.loads((H1_FIX / "m5_payload_rgb.json").read_text())
    cam = CameraModel(**raw["camera"])
    p = PayloadProfile(**{**raw["payload"], "camera": cam})
    warns = derive_payload_geometry(p)
    check_m5_payload(p)
    gsd_m = cam.gsd_for_agl(p.agl_m)
    agl2 = cam.agl_for_gsd(gsd_m)
    assert abs(agl2 - p.agl_m) < 1e-6
    fw, _ = cam.footprint(p.agl_m)
    assert abs(p.swath_spacing_m - fw * raw["expect"]["keep_factor"]) < 1e-6
    assert isinstance(warns, list)


def test_m5_raises_agl_to_min_safe():
    cam = CameraModel("c", 6000, 4000, 16.0, 3.9)
    # absurdly fine GSD → tiny AGL
    p = PayloadProfile(id="t", type="rgb", nominal_agl_m=10, gsd_cm=0.1, side_overlap=0.7, camera=cam)
    warns = derive_payload_geometry(p)
    assert p.agl_m >= MIN_SAFE_AGL_M
    assert any("raised" in w for w in warns)
