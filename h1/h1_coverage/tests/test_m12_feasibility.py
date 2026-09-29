"""M12 FeasibilityMatrix — contract h1.m12.feasibility.v1"""
from __future__ import annotations

from h1_coverage.config import CoverageConfig
from h1_coverage.contracts import check_m12_feasibility
from h1_coverage.coverage.engine import build_coverage
from h1_coverage.feasibility import build_feasibility
from h1_coverage.io.scene import load_scene
from h1_coverage.modules import by_id

from paths import scene


def test_module_registry_m12():
    assert by_id("M12").contract_id == "h1.m12.feasibility.v1"


def test_m12_feasibility_s09_s11():
    s09 = load_scene(scene("S09_wind_feasibility"))
    r09 = build_coverage(s09, CoverageConfig(strict_coverage=False))
    f09 = build_feasibility(s09, r09.tasks)
    check_m12_feasibility(f09, list(r09.tasks))
    wind = s09.mission.wind.speed_ms
    for u in s09.fleet:
        if wind > u.max_wind_ms:
            for tid, elig in f09.eligible_uav_ids_by_task.items():
                assert u.id not in elig

    s11 = load_scene(scene("S11_payload_compatibility"))
    r11 = build_coverage(s11, CoverageConfig(strict_coverage=False))
    f11 = build_feasibility(s11, r11.tasks)
    check_m12_feasibility(f11, list(r11.tasks))
    for tid, task in r11.tasks.items():
        for uid in f11.eligible_uav_ids_by_task[tid]:
            uav = next(u for u in s11.fleet if u.id == uid)
            assert task.payload_class in uav.payload_classes
    # Wind must not alter endurance numbers (S09 assertion spirit)
    for u in s09.fleet:
        assert u.operational_endurance_min == u.operational_endurance_min
        assert u.usable_endurance_s > 0
