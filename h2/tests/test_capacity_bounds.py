from h2.contract import load_bundle
from h2.planner import infeasibility_bounds
from h2.scheduler import Scheduler,Settings
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_h1_s10_requires_relaunch_from_landing_only_site():
    b=load_bundle(ROOT/"fixtures/S10_different_start_end.bundle.json")
    proofs=infeasibility_bounds(Scheduler(b,Settings(objective="total_flight")))
    assert any(p["code"] == "landing_site_cannot_launch_required_next_sortie" and p["minimum_sorties"] >= 2 for p in proofs)


def test_h1_s11_forced_lidar_work_plus_service_exceeds_window():
    b=load_bundle(ROOT/"fixtures/S11_payload_compatibility.bundle.json")
    proofs=infeasibility_bounds(Scheduler(b,Settings()))
    assert any(p["code"] == "forced_work_exceeds_window" and p["minimum_duration_s"] > p["window_s"] for p in proofs)


def test_no_false_bound_on_shareable_work(bundle):
    assert not infeasibility_bounds(Scheduler(bundle,Settings()))
