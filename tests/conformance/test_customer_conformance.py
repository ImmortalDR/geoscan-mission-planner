from __future__ import annotations

import json
from pathlib import Path

import pytest

from gmp.io.export import plan_geojson, plan_kml
from gmp.io.scene_loader import load_scene, validate_scene
from gmp.planner import PlannerOptions, plan_mission
from gmp.safety.oracles import evaluate_assertions

ROOT = Path(__file__).resolve().parents[2] / "datasets/geoscan_customer_conformance"
SUITE = json.loads((ROOT / "suite.json").read_text(encoding="utf-8"))

BUDGET = {
    "S00_smoke_rgb": 25.0,
    "S01_full_customer_acceptance_100km2": 90.0,
    "S02_multipolygon_holes": 30.0,
    "S03_temporal_airspace_daylight": 40.0,
    "S04_multi_sortie_energy": 40.0,
    "S05_4d_deconfliction": 30.0,
    "S06_recommend_alternative_site": 50.0,
    "S07_reserve_landing_failure": 35.0,
    "S08_fixed_wing_turnaround": 30.0,
    "S09_wind_feasibility": 30.0,
    "S10_different_start_end": 30.0,
    "S11_payload_compatibility": 50.0,
}


@pytest.mark.parametrize("scenario", SUITE["scenarios"])
def test_parser_gate_a(scenario):
    scene = load_scene(ROOT / scenario)
    report = validate_scene(scene)
    assert scene.crs.geographic_epsg == "EPSG:4326"
    hard = [i for i in report["issues"] if i.get("severity") != "warning"]
    # S07 historically warned on payload mismatch; after generator fix it must parse.
    assert scene.jobs, scenario
    assert scene.fleet, scenario


@pytest.mark.parametrize("scenario", SUITE["scenarios"])
def test_customer_conformance_oracles(scenario):
    scene = load_scene(ROOT / scenario)
    assertions = json.loads((ROOT / scenario / "expected_assertions.json").read_text(encoding="utf-8"))
    obj = scene.mission.objective
    budget = BUDGET.get(scenario, 30.0)
    if scenario == "S01_full_customer_acceptance_100km2":
        pytest.importorskip("ortools")
    plan = plan_mission(scene, PlannerOptions(objective=obj, time_budget_s=budget, seed=20260918))
    result = evaluate_assertions(plan, assertions, scene)
    if scenario in ("S00_smoke_rgb", "S02_multipolygon_holes"):
        _ = plan_kml(scene, plan)
        _ = plan_geojson(scene, plan)
    assert result["passed"], f"{scenario} failed {result['failed']}: status={result['status']} metrics={result['metrics']}"
