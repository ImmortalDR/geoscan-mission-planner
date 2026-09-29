from copy import deepcopy
from pathlib import Path
import pytest
from conftest import make_bundle
from h2.contract import load_bundle
from h2.planner import plan_bundle, Settings


@pytest.mark.parametrize("objective",["makespan","total_flight"])
def test_plan_invariants_and_two_objectives(objective):
    d=make_bundle(4,2)
    original=deepcopy(d)
    p=plan_bundle(d,Settings(objective=objective,time_budget_s=2,max_iterations=20))
    assert p["status"] == "FEASIBLE",p["checks"]
    assert p["checks"]["passed"]
    assert not p["unassigned"] and not p["deconfliction"]["remaining"]
    assert p["requires_h3_validation"] and p["certificate"] is None
    assert d == original and p["tasks"] == d["tasks"]
    logs = p["solver_log"]
    assert all(b["objective_value"] <= a["objective_value"]+1e-6 for a,b in zip(logs,logs[1:]))


def test_ineligible_task_explicit():
    d=make_bundle(2,1)
    d["feasibility"]["eligible_uav_ids_by_task"]["t0"]=[]
    p=plan_bundle(d,Settings(time_budget_s=1,max_iterations=0))
    assert p["status"] == "INFEASIBLE"
    assert {u["task_id"] for u in p["unassigned"]} == {"t0"}


def test_unaffordable_atomic_task_not_split():
    d=make_bundle(1,1,endurance_min=.1)
    p=plan_bundle(d,Settings(time_budget_s=1,max_iterations=0))
    assert p["status"] != "FEASIBLE"
    assert p["unassigned"][0]["task_id"] == "t0"
    assert p["tasks"] == d["tasks"]


def test_five_models_supported():
    d=make_bundle(5,5)
    for i in range(5):
        d["feasibility"]["eligible_uav_ids_by_task"][f"t{i}"]=[f"u{i}"]
    p=plan_bundle(d,Settings(time_budget_s=3,max_iterations=0))
    assert p["status"] == "FEASIBLE",p["checks"]
    assert {s["uav_id"] for s in p["sorties"]} == {f"u{i}" for i in range(5)}


def test_deterministic_bounded_iterations():
    d=make_bundle(3,2)
    opts=Settings(time_budget_s=10,max_iterations=10,cpsat=False)
    a,b=plan_bundle(d,opts),plan_bundle(d,opts)
    assert a["sorties"] == b["sorties"]


def test_temporal_zone_delays_departure():
    d=make_bundle(1,1)
    scene=dict(schema_version="h2.scene.v1",crs=d["crs"],temporal=[dict(start_s=0,end_s=200,
        geometry=dict(type="Polygon",coordinates=[[[500200,5999990],[500300,5999990],[500300,6000010],[500200,6000010],[500200,5999990]]]))])
    p=plan_bundle(d,Settings(time_budget_s=1,max_iterations=0),scene)
    assert p["status"] == "FEASIBLE",p["checks"]
    assert p["sorties"][0]["start_s"] > 200


@pytest.mark.integration
@pytest.mark.parametrize("name",[
    "S00_smoke_rgb",
    "S02_multipolygon_holes",
    "S08_fixed_wing_turnaround",
    "S09_wind_feasibility",
    "S10_different_start_end",
    "S11_payload_compatibility",
])
def test_h1_live_bundle_accounting(name):
    path=Path(__file__).resolve().parents[1]/"fixtures"/(name+".bundle.json")
    d=load_bundle(path)
    p=plan_bundle(d,Settings(objective=d["mission"]["objectives"][0],time_budget_s=2,max_iterations=0,cpsat=False))
    assigned=[tid for s in p["sorties"] for tid in s["task_ids"]]
    missing=[x["task_id"] for x in p["unassigned"]]
    assert sorted(assigned+missing) == sorted(t["id"] for t in d["tasks"])
    assert p["tasks"] == d["tasks"]
    assert p["status"] != "SAFE"
    if p["status"] == "FEASIBLE":
        assert p["checks"]["passed"]


def test_construction_finishes_all_tasks_after_improvement_budget_expires():
    bundle = make_bundle(12, 1, endurance_min=5)
    plan = plan_bundle(bundle, Settings(time_budget_s=1e-9, cpsat=False, max_iterations=0))
    assert plan["status"] == "FEASIBLE", plan["checks"]
    assert not plan["unassigned"]
    assert len(plan["sorties"]) > 1
    assert sorted(t for s in plan["sorties"] for t in s["task_ids"]) == sorted(t["id"] for t in bundle["tasks"])
    for previous, following in zip(plan["sorties"], plan["sorties"][1:]):
        assert following["start_s"] >= previous["end_s"] + bundle["fleet"][0]["service_time_s"]


def test_complete_construction_does_not_extend_mission_window():
    bundle = make_bundle(12, 1, endurance_min=5)
    bundle["mission"]["window_end"] = "2026-09-20T00:04:00Z"
    plan = plan_bundle(bundle, Settings(time_budget_s=1e-9, cpsat=False, max_iterations=0))
    assert plan["unassigned"]
    assert plan["status"] != "FEASIBLE"
    assert all(s["end_s"] <= 240 for s in plan["sorties"])
    attempts = [a for item in plan["unassigned"] for u in item["construction_attempts"] for a in u.get("route_failures", [])]
    assert any(a["reason"] == "mission_window" for a in attempts)
