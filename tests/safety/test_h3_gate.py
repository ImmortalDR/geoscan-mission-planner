from copy import deepcopy
from datetime import datetime
import json
from types import SimpleNamespace

import pytest

import test_h3_core as checker_fixtures
from gmp.safety.h3_gate import infer_infeasibility, input_fingerprint, validate_result
from gmp.safety.oracles import evaluate_assertions
from gmp.models import Plan
from gmp.recommend.verified import verify_alternative
from gmp.safety import h3_gate
from shapely.geometry import box


@pytest.fixture
def example():
    case = checker_fixtures.CheckerTests()
    case.setUp()
    yield case
    case.doCleanups()


def test_certificate_is_bound_to_all_inputs_and_result(example):
    report = validate_result(example.directory, example.result, "PLAN_1")
    assert report["passed"] and report["status"] == "SAFE", report
    cert = report["certificate"]
    assert cert["input_sha256"] == input_fingerprint(example.directory)[0]
    assert cert["result_sha256"] == report["result_sha256"]
    assert cert["plan_id"] == "PLAN_1"
    assert len(cert["input_files_sha256"]) == 11
    assert "aircraft_dynamics" in cert["not_checked"]
    original = report["input_sha256"]
    metadata = json.loads((example.directory / "metadata.json").read_text())
    example.write("metadata.json", {**metadata, "description": "New version"})
    changed = validate_result(example.directory, example.result)
    assert changed["input_sha256"] != original


def test_false_metrics_and_stale_infeasible_are_unsafe(example):
    result = deepcopy(example.result)
    result["metrics"]["distance_m"] = 0
    report = validate_result(example.directory, result)
    assert report["status"] == "UNSAFE" and report["certificate"] is None
    assert infer_infeasibility(example.directory, "makespan") is None
    result.update(status="INFEASIBLE", sorties=[], metrics={},
                  diagnosis={"proof": {"type": "no_compatible_uav", "job_id": "JOB"}})
    report = validate_result(example.directory, result)
    assert report["status"] == "UNSAFE" and not report["passed"]


def test_wind_proof_has_no_executable_certificate(example):
    example.mission["wind"]["speed_ms"] = 15
    example.write("mission.json", example.mission)
    result = infer_infeasibility(example.directory, "makespan")
    assert result["diagnosis"]["proof"]["type"] == "wind_excludes_all"
    report = validate_result(example.directory, result)
    assert report["passed"] and report["status"] == "INFEASIBLE"
    assert report["certificate"] is None


def test_malformed_and_timeout_never_receive_certificate(example):
    for result in (None, {}, {"status": "SAFE"}, {"status": "TIMEOUT"}, {"status": "INFEASIBLE"}):
        report = validate_result(example.directory, result)
        assert report["status"] == "UNSAFE" and report["certificate"] is None


def test_invalid_projection_returns_closed_verdict(example):
    example.write("metadata.json", {"scenario_id": "test", "metric_crs": "EPSG:NOT_REAL",
                                    "temporal_altitude_reference": "AMSL"})
    report = validate_result(example.directory, example.result)
    assert report["status"] == "UNSAFE" and not report["passed"]
    assert infer_infeasibility(example.directory, "makespan") is None


def test_dataset_fractional_tolerance_does_not_allow_partial_service_mission(example):
    example.layer("survey_areas.geojson", [(box(-80, -20, 140.05, 20), {
        "id": "JOB", "survey_type": "rgb", "payload_profile": "RGB"})])
    construction = h3_gate.recompute_metrics(example.directory, example.result)
    assert construction["passed"], construction
    example.result["metrics"] = construction["metrics"]
    report = validate_result(example.directory, example.result)
    assert not report["passed"] and report["certificate"] is None
    assert any(v["code"] == "COVERAGE_NOT_COMPLETE" for v in report["violations"])


def test_unverified_recommendation_is_not_offered(example):
    assert verify_alternative(example.directory, example.result, {"start_site": "START"})["verified"]
    example.result["sorties"] = []
    assert verify_alternative(example.directory, example.result, {"start_site": "START"}) is None


def test_absent_counters_do_not_pass_safety_assertions():
    plan = Plan(scene_id="test", objective="makespan", status="SAFE")
    report = evaluate_assertions(plan, {"nfz_violations": 0, "reserve_landing_reachability": True,
                                       "wind_limit_must_filter_fleet": True, "preserve_holes": True})
    assert not report["passed"]
    assert set(report["failed"]) == {"nfz_violations", "reserve_landing_reachability", "wind_limit_must_filter_fleet", "geometry_preserved"}


def test_input_mutation_during_validation_invalidates_certificate(example, monkeypatch):
    original = h3_gate.check_result

    def changing_input(*args, **kwargs):
        report = original(*args, **kwargs)
        metadata = json.loads((example.directory / "metadata.json").read_text())
        example.write("metadata.json", {**metadata, "revision": 2})
        return report

    monkeypatch.setattr(h3_gate, "check_result", changing_input)
    report = validate_result(example.directory, example.result)
    assert report["status"] == "UNSAFE" and not report["certificate"]
    assert any(v["code"] == "FINGERPRINT_FAILED" for v in report["violations"])


def test_aircraft_cannot_teleport_between_different_sites(example):
    second = example.sortie(offset=150)
    second.update(id="FLIGHT_2", index=1)
    example.result["sorties"].append(second)
    example.result["metrics"].update(distance_m=800, total_flight_s=120, makespan_s=210)
    report = validate_result(example.directory, example.result)
    assert any(v["code"] == "AIRCRAFT_REPOSITION" for v in report["violations"])


def test_allowed_airspace_hole_is_checked_between_waypoints(example):
    allowed = box(-1500, -1500, 1500, 1500).difference(box(-5, -5, 5, 5))
    example.layer("allowed_airspace.geojson", [(allowed, {"id": "ALLOWED"})])
    report = validate_result(example.directory, example.result)
    assert any(v["code"] == "OUTSIDE_ALLOWED" for v in report["violations"])


def test_reserve_and_remaining_claim_protect_landing_suffix(example):
    example.uav["operational_endurance_min"] = 1.25
    example.write("fleet.json", {"uavs": [example.uav]})
    for w in example.result["sorties"][0]["waypoints"]:
        elapsed = (datetime.fromisoformat(w["t"]) - example.epoch).total_seconds()
        w["remaining_endurance_s"] = 60 - elapsed
    report = validate_result(example.directory, example.result)
    assert report["passed"], report
    assert report["metrics"]["landing_reachability"] == "proved_by_resource_feasible_verified_suffix"
    example.uav["operational_endurance_min"] = 1.249
    example.write("fleet.json", {"uavs": [example.uav]})
    example.result["sorties"][0]["waypoints"][-1]["remaining_endurance_s"] = 999
    report = validate_result(example.directory, example.result)
    codes = {v["code"] for v in report["violations"]}
    assert {"RESOURCE_EXCEEDED", "REMAINING_ENDURANCE"} <= codes
    assert report["metrics"]["landing_reachability"] == "not_proved"


def test_fixed_wing_buffer_checks_a_zone_the_route_does_not_enter(example):
    example.uav.update({"class": "fixed_wing", "turnaround_buffer_m": 35})
    example.write("fleet.json", {"uavs": [example.uav]})
    example.layer("no_fly_zones.geojson", [(box(-102, 24, -98, 28), {"id": "NEAR_TURN"})])
    point = example.result["sorties"][0]["waypoints"][3]
    point["lon"], point["lat"] = example.to_wgs.transform(*(example.origin + [100, 50]))
    report = validate_result(example.directory, example.result)
    codes = {v["code"] for v in report["violations"]}
    assert "NFZ_VIOLATION" not in codes
    assert "FIXED_WING_MANEUVER" in codes


def test_used_aircraft_payload_compatibility_is_rechecked(example):
    example.uav["payload_classes"] = ["thermal"]
    example.write("fleet.json", {"uavs": [example.uav]})
    report = validate_result(example.directory, example.result)
    assert any(v["code"] == "PAYLOAD_INCOMPATIBLE" for v in report["violations"])


@pytest.mark.parametrize(("altitude", "code"), [(110, "GSD_LIMIT"), (20, "MIN_AGL")])
def test_actual_altitude_checks_gsd_and_minimum_clearance(example, altitude, code):
    for point in example.result["sorties"][0]["waypoints"]:
        if point["agl_m"] > 0:
            point.update(agl_m=altitude, amsl_m=100 + altitude)
    report = validate_result(example.directory, example.result)
    assert any(v["code"] == code for v in report["violations"])


def test_daylight_window_and_ground_endpoint_are_checked(example):
    example.mission["daylight_window"] = {"start": example.time(1), "end": example.time(7200)}
    example.write("mission.json", example.mission)
    point = example.result["sorties"][0]["waypoints"][-1]
    point["lon"], point["lat"] = example.to_wgs.transform(*(example.origin + [220, 0]))
    report = validate_result(example.directory, example.result)
    codes = {v["code"] for v in report["violations"]}
    assert {"DAYLIGHT_WINDOW", "SITE_ENDPOINT"} <= codes


def test_legacy_adapter_checks_in_memory_variant_not_source_directory(example):
    from gmp.io.scene_loader import load_scene
    from gmp.models import CameraModel, Sortie, Uav, Waypoint
    from gmp.safety.model_adapter import validate_model_plan
    from gmp.safety.certificate import issue_certificate
    from gmp.safety.validator import ValidationReport

    scene = load_scene(example.directory, scene_id="test")
    raw_uav = deepcopy(example.uav)
    raw_uav["uav_class"] = raw_uav.pop("class")
    raw_uav["ground_speed_ms"] = raw_uav.pop("ground_speed_kmh") / 3.6
    scene.fleet = [Uav(**raw_uav)]
    scene.payloads["RGB"].camera = CameraModel(id="TEST", **example.payload["camera"])
    source = example.result["sorties"][0]
    waypoints = []
    for w in source["waypoints"]:
        x, y = scene.crs.lonlat_to_xy(w["lon"], w["lat"])
        waypoints.append(Waypoint(x=x, y=y, agl_m=w["agl_m"], amsl_m=w["amsl_m"],
                                  t=datetime.fromisoformat(w["t"]), phase=w["phase"],
                                  task_id="TASK" if w["job_id"] else None,
                                  speed_ms=w["speed_ms"], remaining_endurance_s=w["remaining_endurance_s"]))
    sortie = Sortie(uav_id="U1", index=0, start_site_id="START", landing_site_id="END", task_ids=["TASK"],
                    t_start=waypoints[0].t, t_end=waypoints[-1].t, flight_time_s=60, distance_m=400, waypoints=waypoints)
    plan = Plan(scene_id="test", objective="makespan", sorties=[sortie], tasks={"TASK": SimpleNamespace(job_id="JOB")})
    report = validate_model_plan(scene, plan)
    assert report["passed"], report
    legacy_report = ValidationReport(scene_id="test", status="SAFE", checks={"independent_h3": report})
    assert issue_certificate(scene, plan, legacy_report)
    scene.mission.wind.speed_ms = 15
    assert issue_certificate(scene, plan, legacy_report) is None
    report = validate_model_plan(scene, plan)
    assert not report["passed"]
    assert any(v["code"] == "WIND_LIMIT" for v in report["violations"])


def test_explicit_unsafe_candidate_runs_diagnostics_without_certificate(example):
    candidate = deepcopy(example.result)
    candidate["status"] = "UNSAFE"
    report = validate_result(example.directory, candidate)
    assert report["status"] == "UNSAFE" and not report["passed"]
    assert report["certificate"] is None
    assert report["metrics"]["makespan_s"] > 0
    assert any(v["code"] == "UNSAFE_CANDIDATE" for v in report["violations"])
    assert not any(v["code"] == "INVALID_DATA" for v in report["violations"])


def test_explicit_intermediate_recharge_and_mission_endpoints(example):
    from pyproj import Transformer
    for _,properties in example.sites:
        properties['role']='both'
    example.layer('landing_sites.geojson',example.sites)
    example.uav.update(landing_site='START',refuel_sites=['END'])
    example.write('fleet.json',{'uavs':[example.uav]})
    second=example.sortie(offset=120)
    second.update(id='SECOND',index=1,start_site='END',landing_site='START')
    project=Transformer.from_crs(4326,32637,always_xy=True)
    for wp in second['waypoints']:
        x,y=project.transform(wp['lon'],wp['lat'])
        wp['lon'],wp['lat']=example.to_wgs.transform(2*example.origin[0]-x,y)
    example.result['sorties'].append(second)
    example.result['metrics'].update(distance_m=800,total_flight_s=120,makespan_s=180)
    report=validate_result(example.directory,example.result)
    assert report['passed'],report['violations']
    example.uav['refuel_sites']=[]
    example.write('fleet.json',{'uavs':[example.uav]})
    report=validate_result(example.directory,example.result)
    assert not report['passed'] and report['certificate'] is None
    assert any(v['code']=='REFUEL_SITE' for v in report['violations'])


def test_any_recharge_does_not_authorize_landing_only_site(example):
    example.uav['refuel_sites']=['END']
    example.write('fleet.json',{'uavs':[example.uav]})
    report=validate_result(example.directory,example.result)
    assert not report['passed'] and report['certificate'] is None
