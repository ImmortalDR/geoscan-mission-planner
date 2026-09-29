"""M13 BundleExporter — contract h1.m13.bundle.v1 = gmp.h1_h2.v1"""
from __future__ import annotations

import json
from pathlib import Path

from h1_coverage.bundle import export_bundle, validate_bundle
from h1_coverage.config import CoverageConfig, SCHEMA_VERSION
from h1_coverage.contracts import check_m13_bundle
from h1_coverage.coverage.engine import build_coverage
from h1_coverage.io.scene import load_scene
from h1_coverage.modules import by_id
from h1_coverage.pipeline import run_h1_to_file

from paths import scene


def test_module_registry_m13():
    assert by_id("M13").contract_id == "h1.m13.bundle.v1"


def test_m13_bundle_contract(tmp_path: Path):
    out = tmp_path / "S00.bundle.json"
    r = run_h1_to_file(scene("S00_smoke_rgb"), out, CoverageConfig(strict_coverage=False))
    data = json.loads(out.read_text())
    assert data["schema_version"] == SCHEMA_VERSION
    check_m13_bundle(data)
    assert validate_bundle(data) == []
    assert r.bundle.data["tasks"]


def test_m13_live_export_s00():
    s = load_scene(scene("S00_smoke_rgb"))
    cov = build_coverage(s, CoverageConfig(strict_coverage=False))
    bundle = export_bundle(s, cov)
    check_m13_bundle(bundle.data)
