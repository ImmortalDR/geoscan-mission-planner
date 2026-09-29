"""Independent regression cases for optimisation behaviour and fail-closed output."""
from copy import deepcopy

from conftest import make_bundle
from h2.checks import check_plan
from h2.planner import Settings, plan_bundle


def test_anytime_improves_zigzag_baseline():
    bundle = make_bundle(6, 1)
    for task, offset in zip(bundle["tasks"], [0, 1000, 200, 800, 400, 600]):
        coords = [[x, 6000000.+offset] for x, _ in task["transects"][0]["coords"]]
        task["transects"][0]["coords"] = coords
        task["entry"], task["exit"] = coords[0], coords[-1]
        task["geom_coords"] = deepcopy(coords)
    plan = plan_bundle(bundle, Settings(cpsat=False, time_budget_s=5, max_iterations=150))
    assert plan["status"] == "FEASIBLE"
    log = plan["solver_log"]
    assert len(log) > 1
    assert log[-1]["objective_value"] < .9*log[0]["objective_value"]
    assert all(b["objective_value"] <= a["objective_value"] for a, b in zip(log, log[1:]))


def test_objectives_choose_different_valid_plans():
    bundle = make_bundle(4, 2)
    for i, uav in enumerate(bundle["fleet"]):
        uav["cruise_agl_m"] = 60+i*40
    for task in bundle["tasks"][2:]:
        task["agl_m"] = 100
    bundle["sites"].append(dict(id="other", x=500000., y=6000500., role="both"))
    bundle["fleet"][1]["start_site"] = bundle["fleet"][1]["landing_site"] = "other"
    plans = [plan_bundle(bundle, Settings(objective=objective, cpsat=False, time_budget_s=5, max_iterations=100))
             for objective in ("makespan", "total_flight")]
    a, b = plans
    assert all(p["status"] == "FEASIBLE" for p in plans)
    assert a["metrics"]["makespan_s"] < b["metrics"]["makespan_s"]
    assert a["metrics"]["total_flight_s"] > b["metrics"]["total_flight_s"]


def test_terrain_hill_interior_requires_extra_climb_time():
    bundle = make_bundle(1, 1)
    options = Settings(cpsat=False, time_budget_s=3, max_iterations=0)
    flat = plan_bundle(bundle, options)
    scene = dict(schema_version="h2.scene.v1", crs=bundle["crs"], terrain=dict(
        origin=[500000, 6000000], cell_size_m=250, values=[[0, 300, 0], [0, 300, 0]]))
    hill = plan_bundle(bundle, options, scene)
    assert hill["status"] == "FEASIBLE", hill["checks"]
    assert hill["metrics"]["total_flight_s"] > flat["metrics"]["total_flight_s"]
    for sortie in hill["sorties"]:
        for a, b in zip(sortie["waypoints"], sortie["waypoints"][1:]):
            assert abs(a["z_m"]-b["z_m"]) <= 3*(b["t_s"]-a["t_s"])+1e-5


def test_duplicate_sortie_ids_fail_checker():
    bundle = make_bundle(1, 1)
    plan = plan_bundle(bundle, Settings(cpsat=False, max_iterations=0))
    plan["sorties"].append(deepcopy(plan["sorties"][0]))
    result = check_plan(bundle, plan)
    assert not result["passed"]
    assert "duplicate_sortie_id" in {v["code"] for v in result["violations"]}


def test_unresolved_conflicts_never_mark_feasible():
    bundle = make_bundle(2, 2)
    bundle["feasibility"]["eligible_uav_ids_by_task"] = {"t0": ["u0"], "t1": ["u1"]}
    plan = plan_bundle(bundle, Settings(cpsat=False, deconflict=False, max_iterations=0))
    assert plan["deconfliction"]["remaining"]
    assert plan["status"] == "UNRESOLVED"
    assert not plan["checks"]["passed"]
