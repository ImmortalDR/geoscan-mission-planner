from copy import deepcopy
from pathlib import Path

import pytest

from h2.checks import check_plan
from h2.contract import load_bundle
from h2.scheduler import Scheduler, Settings


@pytest.fixture
def valid():
    bundle = load_bundle(Path(__file__).parents[1]/"fixtures/S00_smoke_rgb.bundle.json")
    uid = bundle["fleet"][0]["id"]
    sorties = Scheduler(bundle, Settings()).schedule({uid: [t["id"] for t in bundle["tasks"]]})
    return bundle, dict(sorties=sorties, unassigned=[], tasks=deepcopy(bundle["tasks"]), metrics={})


def codes(bundle, plan, **kwargs):
    return {v["code"] for v in check_plan(bundle, plan, **kwargs)["violations"]}


def test_valid_and_immutable(valid):
    bundle, plan = valid
    before = deepcopy((bundle, plan))
    assert check_plan(bundle, plan)["passed"]
    assert (bundle, plan) == before


def test_missing_duplicate_and_ineligible(valid):
    bundle, plan = valid
    tid = bundle["tasks"][0]["id"]
    bundle["feasibility"]["eligible_uav_ids_by_task"][tid] = []
    assert "ineligible_assignment" in codes(bundle, plan)
    plan["sorties"][0]["task_ids"].append(tid)
    assert "task_accounting" in codes(bundle, plan)
    plan["sorties"] = []
    assert "task_accounting" in codes(bundle, plan)
    plan["unassigned"] = [dict(task_id=tid, reason="no resource")]
    assert "unassigned_task" in codes(bundle, plan)


def test_independent_resource_not_declared_duration(valid):
    bundle, plan = valid
    bundle["fleet"][0]["operational_endurance_min"] = .01
    plan["sorties"][0]["flight_time_s"] = .001
    found = codes(bundle, plan)
    assert {"resource_exceeded", "declared_flight_time_mismatch"} <= found


def test_time_and_speed_corruption(valid):
    bundle, plan = valid
    plan["sorties"][0]["waypoints"][1]["t_s"] = -1
    assert "nonmonotonic_time" in codes(bundle, plan)
    plan["sorties"][0]["waypoints"][1]["x"] += 1e6
    assert "excessive_speed" in codes(bundle, plan)


def test_geometry_corruption(valid):
    bundle, plan = valid
    next(p for p in plan["sorties"][0]["waypoints"] if p["phase"] == "survey")["x"] += 3
    assert "survey_geometry_mismatch" in codes(bundle, plan)
    plan["tasks"][0]["transects"][0]["coords"][0][0] += 1
    assert "task_geometry_changed" in codes(bundle, plan)


def test_metric_and_task_window(valid):
    bundle, plan = valid
    plan["metrics"]["total_flight_s"] = -1
    assert "metric_mismatch" in codes(bundle, plan)
    tid = bundle["tasks"][0]["id"]
    assert "task_window" in codes(bundle, plan, settings={"task_windows": {tid: [100000, 100001]}})


def test_service_and_same_uav_overlap(valid):
    bundle, plan = valid
    duplicate = deepcopy(plan["sorties"][0])
    duplicate["id"] = "other"
    plan["sorties"].append(duplicate)
    assert {"service_gap", "conflict"} <= codes(bundle, plan)


def test_nonfinite_input_fails_closed(valid):
    bundle, plan = valid
    plan["sorties"][0]["waypoints"][0]["t_s"] = float("nan")
    assert not check_plan(bundle, plan)["passed"]


def test_temporal_restrictions(valid):
    from shapely.geometry import box, mapping
    bundle, plan = valid
    p = plan["sorties"][0]["waypoints"][0]
    scene = dict(schema_version="h2.scene.v1", crs=bundle["crs"], temporal=[dict(
        geometry=mapping(box(p["x"]-10, p["y"]-10, p["x"]+10, p["y"]+10)), start_s=0, end_s=100)])
    assert "temporal_restriction" in codes(bundle, plan, scene=scene)
    scene["temporal"][0]["start_s"] = 1e8
    scene["temporal"][0]["end_s"] = 1e9
    assert "temporal_restriction" not in codes(bundle, plan, scene=scene)


def test_pinned_initial_site_allows_subsequent_start_at_previous_landing():
    from conftest import make_bundle
    bundle = make_bundle(2, 1)
    bundle["mission"]["allow_different_start_end"] = True
    bundle["sites"].append(dict(id="other", x=500500., y=6000000., role="both"))
    uid = bundle["fleet"][0]["id"]
    original = bundle["sites"][0]["id"]
    bundle["fleet"][0]["start_site"] = original
    bundle["fleet"][0]["landing_site"] = None
    scheduler = Scheduler(bundle, Settings())
    first = deepcopy(scheduler.route(uid, ("t0",), original, "other"))
    first["id"] = "first"
    second = deepcopy(scheduler.route(uid, ("t1",), "other", "other"))
    second["id"] = "second"
    delay = first["end_s"]+bundle["fleet"][0]["service_time_s"]
    second["start_s"] += delay
    second["end_s"] += delay
    for p in second["waypoints"]:
        p["t_s"] += delay
    for t in second["task_times"]:
        t["start_s"] += delay
        t["end_s"] += delay
    plan = dict(sorties=[first, second], unassigned=[])
    assert check_plan(bundle, plan)["passed"]
    bundle["fleet"][0]["start_site"] = "other"
    assert "pinned_site_mismatch" in codes(bundle, plan)


def test_unknown_refs_and_short_takeoff(valid):
    bundle, plan = valid
    plan["sorties"][0]["task_ids"].append("invented")
    plan["sorties"][0]["waypoints"][0]["task_id"] = "unknown"
    assert {"unknown_task", "unknown_waypoint_task", "task_timing_ids"} <= codes(bundle, plan)
    for p in plan["sorties"][0]["waypoints"]:
        if p["phase"] == "takeoff":
            p["phase"] = "transit"
    assert "takeoff_landing_duration" in codes(bundle, plan)


def test_forged_hash_and_altitude(valid):
    bundle, plan = valid
    plan["input_sha256"] = "forged"
    plan["sorties"][0]["waypoints"][0]["z_m"] += 1000
    assert {"input_fingerprint_mismatch", "altitude_reference_mismatch"} <= codes(bundle, plan)


@pytest.mark.parametrize("index", [0, -1])
def test_airborne_site_endpoint_is_not_a_physical_takeoff_or_landing(valid, index):
    bundle, plan = valid
    point = plan["sorties"][0]["waypoints"][index]
    point["agl_m"] = point["z_m"] = 60
    assert "site_ground_altitude" in codes(bundle, plan)
