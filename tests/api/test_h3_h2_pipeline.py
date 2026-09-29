from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from gmp.api.h2_adapter import apply_h2_site_preferences, build_scene_sidecar, export_sorties, prepare_h1_fleet
from gmp.api.planner_adapter import build_live, load_dataset_module
from gmp.safety.h3_gate import input_fingerprint, validate_result


DATASET = Path(os.environ.get("GMP_DATASET_DIR", Path(__file__).resolve().parents[2] / "data"))


@pytest.fixture
def dataset():
    if not DATASET.is_dir():
        pytest.fail("The versioned H3 dataset is not installed")
    return DATASET


def test_export_keeps_incoming_phase_and_rejects_instantaneous_movement():
    from h2.contract import fingerprint

    bundle = {"crs": {"metric_epsg": 32637}, "tasks": [{"id": "task", "job_id": "job"}],
              "fleet": [{"id": "u", "operational_endurance_min": 10, "energy_reserve_fraction": .2}]}
    point = {"x": 400000., "y": 6100000., "agl_m": 50., "z_m": 150., "t_s": 20.,
             "phase": "transit", "task_id": None}
    duplicate = {**point, "t_s": 20. + 1e-12, "phase": "survey", "task_id": "task"}
    # Phase-boundary elevation values may differ only by floating-point roundoff.
    duplicate["agl_m"] += 2e-14
    duplicate["z_m"] += 2e-14
    next_point = {**duplicate, "x": 400010., "t_s": 21.}
    sortie = {"id": "u_S1", "uav_id": "u", "index": 1, "start_site_id": "base", "landing_site_id": "base",
              "start_s": 20., "end_s": 21., "task_ids": ["task"], "waypoints": [point, duplicate, next_point]}
    plan = {"schema_version": "h2.plan.v1", "input_sha256": fingerprint(bundle),
            "time_origin": "2026-06-15T05:30:00+00:00", "sorties": [sortie]}
    before = deepcopy(plan)
    exported = export_sorties(bundle, plan)
    assert plan == before
    assert len(exported[0]["waypoints"]) == 2
    assert exported[0]["waypoints"][0]["phase"] == "transit"
    assert exported[0]["distance_m"] == 10
    duplicate["x"] += 1
    with pytest.raises(ValueError, match="instantaneous"):
        export_sorties(bundle, plan)
    assert exported[0]["waypoints"][1]["job_id"] == "job"


def test_unpinned_site_preferences_survive_h1_fallback(tmp_path):
    raw = {"uavs": [{"id": "u", "start_site": None, "landing_site": None}]}
    (tmp_path / "fleet.json").write_text(json.dumps(raw), encoding="utf-8")
    bundle = {"fleet": [{
        "id": "u",
        "start_site": "H1_FALLBACK_START",
        "landing_site": "H1_FALLBACK_LANDING",
    }]}

    apply_h2_site_preferences(bundle, tmp_path)

    assert bundle["fleet"][0]["start_site"] is None
    assert bundle["fleet"][0]["landing_site"] is None


def test_sidecar_preserves_dem_and_explicit_fleet_input(dataset):
    from h1_coverage.config import CoverageConfig
    from h1_coverage.pipeline import run_h1_scene

    input_dir = dataset / "scenarios/S00_smoke_rgb/input"
    adapter = load_dataset_module(dataset, "reference_builder")
    _, scene, _ = adapter._prepare_scenes(input_dir, "makespan")
    scene.fleet[0].ground_speed_ms = 999.
    prepare_h1_fleet(scene, input_dir)
    bundle = run_h1_scene(scene, CoverageConfig(strict_coverage=False)).bundle.data
    sidecar = build_scene_sidecar(input_dir, bundle)
    raw = json.loads((input_dir / "fleet.json").read_text())["uavs"][0]
    assert bundle["fleet"][0]["takeoff_time_s"] == raw["takeoff_time_s"]
    assert bundle["fleet"][0]["landing_time_s"] == raw["landing_time_s"]
    assert bundle["fleet"][0]["ground_speed_ms"] == pytest.approx(raw["ground_speed_kmh"] / 3.6)
    assert sidecar["terrain"] == {"kind": "raster", "path": str(input_dir / "dem.tif")}
    assert sidecar["allowed"]["type"] in {"Polygon", "MultiPolygon"}
    assert sidecar["crs"] == bundle["crs"]


@pytest.mark.parametrize(("alias", "canonical"), [
    ("airplane", "fixed_wing"), ("fixedwing", "fixed_wing"), ("copter", "multirotor"),
])
def test_flight_class_aliases_are_normalized_before_h1(dataset, tmp_path, alias, canonical):
    from gmp.api.inputs import check_documents

    source = dataset / "scenarios/S00_smoke_rgb/input"
    directory = tmp_path / "input"
    shutil.copytree(source, directory)
    raw = json.loads((directory / "fleet.json").read_text())
    raw["uavs"][0]["class"] = alias
    (directory / "fleet.json").write_text(json.dumps(raw))
    check_documents(directory)
    before = input_fingerprint(directory)
    target = SimpleNamespace(id=raw["uavs"][0]["id"])
    prepare_h1_fleet(SimpleNamespace(fleet=[target]), directory)
    assert target.uav_class == canonical
    assert input_fingerprint(directory) == before


def test_reserve_rejected_above_frozen_bundle_contract(dataset, tmp_path):
    from gmp.api.inputs import check_documents

    directory = tmp_path / "input"
    shutil.copytree(dataset / "scenarios/S00_smoke_rgb/input", directory)
    raw = json.loads((directory / "fleet.json").read_text())
    raw["uavs"][0]["energy_reserve_fraction"] = .5
    (directory / "fleet.json").write_text(json.dumps(raw))
    check_documents(directory)
    raw["uavs"][0]["energy_reserve_fraction"] = .500001
    (directory / "fleet.json").write_text(json.dumps(raw))
    before = (directory / "fleet.json").read_bytes()
    with pytest.raises(ValueError, match=r"energy_reserve_fraction.*0\.5"):
        check_documents(directory)
    assert (directory / "fleet.json").read_bytes() == before


def test_all_installed_dataset_reserves_match_bundle_contract(dataset):
    from gmp.api.inputs import check_documents

    scenes = sorted((dataset / "scenarios").glob("*/input"))
    assert scenes
    for directory in scenes:
        check_documents(directory)
        fleet = json.loads((directory / "fleet.json").read_text())["uavs"]
        assert all(.1 <= uav["energy_reserve_fraction"] <= .5 for uav in fleet)


def test_temporal_sidecar_uses_effective_mission_origin(dataset):
    input_dir = dataset / "scenarios/S03_orekhovo_domodedovskaya/input"
    bundle = {"crs": {"metric_epsg": 32637},
              "mission": {"window_start": "2026-06-15T09:00:00+03:00",
                          "window_end": "2026-06-15T19:00:00+03:00"}}
    sidecar = build_scene_sidecar(input_dir, bundle)
    zone, = sidecar["temporal"]
    assert zone["id"] == "MID_WINDOW_CLOSURE"
    assert zone["start_s"] == 1800
    assert zone["end_s"] == 5400
    assert zone["min_alt_m"] == 0 and zone["max_alt_m"] == 2000


def test_unified_live_s00_requires_independent_h3_certificate(dataset, monkeypatch):
    import gmp.planner

    def legacy_forbidden(*args, **kwargs):
        pytest.fail("Unified live pipeline must not invoke legacy gmp.planner")

    monkeypatch.setattr(gmp.planner, "plan_mission", legacy_forbidden)
    input_dir = dataset / "scenarios/S00_smoke_rgb/input"
    before = input_fingerprint(input_dir)
    candidate = build_live(input_dir, dataset, "makespan", 5., 20260922)
    report = validate_result(input_dir, candidate)
    assert report["status"] == "SAFE", report["violations"][:10]
    assert report["certificate"]
    assert "certificate" not in candidate
    assert input_fingerprint(input_dir) == before
    provenance = candidate["provenance"]
    assert provenance["pipeline"] == "h1_coverage -> h2 -> h3"
    assert provenance["planner_module"] == "h2.planner"
    assert provenance["h1_bundle_sha256"] == provenance["h2_input_sha256"]
    assert provenance["h2_status"] == "FEASIBLE"
    assert provenance["h2_checks"]["passed"]
    assert provenance["input_sha256"] == report["input_sha256"]



def test_full_pipeline_lets_h2_resolve_unpinned_sites(dataset, tmp_path):
    from gmp.api.inputs import check_documents

    source = dataset / "scenarios/S00_smoke_rgb/input"
    directory = tmp_path / "input"
    shutil.copytree(source, directory)
    fleet = json.loads((directory / "fleet.json").read_text())
    for uav in fleet["uavs"]:
        uav["start_site"] = None
        uav["landing_site"] = None
    (directory / "fleet.json").write_text(json.dumps(fleet))
    check_documents(directory)

    candidate = build_live(directory, dataset, "makespan", 5., 20260922)
    report = validate_result(directory, candidate)
    assert report["status"] == "SAFE", report["violations"][:10]
    assert all(uav["start_site"] is None and uav["landing_site"] is None for uav in candidate["h1_bundle"]["fleet"])
    assert candidate["sorties"]
    assert all(sortie["start_site"] and sortie["landing_site"] for sortie in candidate["sorties"])


def test_unresolved_h2_fails_closed_with_actionable_diagnosis(dataset, monkeypatch):
    import h2.planner
    from h2.contract import fingerprint

    def unresolved(bundle, **kwargs):
        return {
            "schema_version": "h2.plan.v1", "input_sha256": fingerprint(bundle),
            "time_origin": bundle["mission"]["window_start"], "tasks": deepcopy(bundle["tasks"]),
            "status": "UNRESOLVED", "sorties": [],
            "unassigned": [{"task_id": task["id"], "reason": "time_budget_exhausted"}
                           for task in bundle["tasks"]],
            "checks": {"passed": False, "violations": [{"code": "unassigned_task"}]},
            "solver_log": [], "deconfliction": {}, "assumptions": [],
        }

    monkeypatch.setattr(h2.planner, "plan_bundle", unresolved)
    input_dir = dataset / "scenarios/S00_smoke_rgb/input"
    candidate = build_live(input_dir, dataset, "makespan", 1., 20260922)
    assert candidate["status"] == "UNSAFE"
    assert candidate["diagnosis"]["assigned_task_count"] == 0
    assert candidate["diagnosis"]["unassigned_task_count"] > 0
    assert "UNRESOLVED" in candidate["diagnosis"]["message"]
    assert "не доказательство" in candidate["diagnosis"]["message"]
    report = validate_result(input_dir, candidate)
    assert report["status"] == "UNSAFE" and report["certificate"] is None


def test_refusal_and_selected_nearby_site_replan(dataset, tmp_path):
    from gmp.recommend.proposals import build_proposals
    from gmp.recommend.verified import verify_alternative

    source = dataset / "scenarios/S06_alternate_site/input"
    candidate = build_live(source, dataset, "makespan", 5., 20260922)
    report = validate_result(source, candidate)
    assert report["passed"] and report["status"] == "INFEASIBLE"
    assert report["certificate"] is None
    layers = {path.stem: json.loads(path.read_text()) for path in source.glob("*.geojson")}
    snapshot = {key: json.loads((source / f"{key}.json").read_text())
                for key in ("metadata", "mission", "fleet", "payload_catalog")}
    snapshot["layers"] = layers
    proposals = build_proposals(snapshot, [], limit=1)
    assert proposals and proposals[0]["type"] == "change_base"
    proposal = proposals[0]
    variant = tmp_path / "selected-input"
    shutil.copytree(source, variant)
    for name, value in proposal["documents"].items():
        (variant / name).write_text(json.dumps(value))
    alternative = build_live(variant, dataset, "makespan", 5., 20260922)
    verified = verify_alternative(variant, alternative, proposal["changes"])
    assert verified is not None, validate_result(variant, alternative)["violations"][:10]
    assert verified["certificate"] and verified["requires_user_selection"]

@pytest.mark.parametrize('objective', ['makespan', 'total_flight'])
def test_ground_work_is_scheduled_and_independently_counted(dataset, tmp_path, objective):
    from datetime import datetime
    directory = tmp_path / 'ground-work'
    shutil.copytree(dataset / 'scenarios/S00_smoke_rgb/input', directory)
    mission = json.loads((directory / 'mission.json').read_text())
    mission.update(preparation_time_s=120., data_download_time_s=180., objectives=["makespan", "total_flight"])
    (directory / 'mission.json').write_text(json.dumps(mission))
    result = build_live(directory, dataset, objective, 15, 20260918)
    validation = validate_result(directory, result)
    assert validation['status'] == 'SAFE', validation['violations']
    epoch = max(datetime.fromisoformat(mission['mission_window']['start']),
                datetime.fromisoformat(mission.get('daylight_window', mission['mission_window'])['start']))
    start = min(datetime.fromisoformat(s['t_start']) for s in result['sorties'])
    end = max(datetime.fromisoformat(s['t_end']) for s in result['sorties'])
    assert (start - epoch).total_seconds() >= 120
    assert result['metrics']['makespan_s'] == pytest.approx((end-epoch).total_seconds()+180, abs=.001)
    assert result['metrics']['total_flight_s'] == pytest.approx(sum(
        (datetime.fromisoformat(s['t_end'])-datetime.fromisoformat(s['t_start'])).total_seconds() for s in result['sorties']),abs=.001)
    assert result['metrics']['ground_operations_explicit'] is True
