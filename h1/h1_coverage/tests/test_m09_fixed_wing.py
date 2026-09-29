"""M9 FixedWingBuffer — contract h1.m9.fixed_wing.v1"""
from __future__ import annotations

from h1_coverage.config import CoverageConfig
from h1_coverage.contracts import check_m9_fw_eligibility
from h1_coverage.coverage.engine import build_coverage
from h1_coverage.feasibility import build_feasibility
from h1_coverage.io.scene import load_scene
from h1_coverage.modules import by_id

from paths import scene


def test_module_registry_m9():
    assert by_id("M9").contract_id == "h1.m9.fixed_wing.v1"


def test_m9_fixed_wing_s08():
    s = load_scene(scene("S08_fixed_wing_turnaround"))
    r = build_coverage(s, CoverageConfig(strict_coverage=False))
    feas = build_feasibility(s, r.tasks)
    fw_ids = {u.id for u in s.fleet if u.is_fixed_wing}
    for tid, task in r.tasks.items():
        elig_fw = [uid for uid in feas.eligible_uav_ids_by_task[tid] if uid in fw_ids]
        check_m9_fw_eligibility(task, elig_fw)
