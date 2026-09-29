"""M2 GeoProjector — contract h1.m2.geo_projector.v1"""
from __future__ import annotations

import json

import pytest

from h1_coverage.contracts import check_m2_roundtrip
from h1_coverage.geo import CrsPipeline, looks_like_lonlat, require_metric_xy, utm_epsg_for_lonlat
from h1_coverage.modules import by_id

from paths import H1_FIX


def test_module_registry_m2():
    assert by_id("M2").contract_id == "h1.m2.geo_projector.v1"


def test_m2_crs_roundtrip():
    assert utm_epsg_for_lonlat(37.6, 55.75) == "EPSG:32637"
    pipe = CrsPipeline.for_point(37.6, 55.75)
    check_m2_roundtrip(pipe, 37.6, 55.75)
    assert pipe.roundtrip_error_m(37.6, 55.75) < 0.01
    pts = json.loads((H1_FIX / "m2_roundtrip_points.json").read_text())
    assert pts["expected_metric_epsg"] == "EPSG:32637"
    for p in pts["points"]:
        check_m2_roundtrip(pipe, float(p["lon"]), float(p["lat"]), tol_deg=pts["max_roundtrip_error_deg"])


def test_m2_lonlat_heuristic():
    assert looks_like_lonlat(37.6, 55.75)
    assert not looks_like_lonlat(400000.0, 6000000.0)
    with pytest.raises(ValueError, match="lon/lat"):
        require_metric_xy(37.6, 55.75, context="task entry")
