"""M11 AtomicTaskBuilder — contract h1.m11.atomic_task.v1"""
from __future__ import annotations

from h1_coverage.config import CoverageConfig
from h1_coverage.contracts import check_m11_atomic
from h1_coverage.coverage.engine import build_coverage
from h1_coverage.io.scene import load_scene
from h1_coverage.modules import by_id

from paths import scene


def test_module_registry_m11():
    assert by_id("M11").contract_id == "h1.m11.atomic_task.v1"


def test_m11_atomic_invariants_s00():
    s = load_scene(scene("S00_smoke_rgb"))
    r1 = build_coverage(s, CoverageConfig(strict_coverage=False))
    r2 = build_coverage(s, CoverageConfig(strict_coverage=False))
    ids1 = sorted(r1.tasks)
    ids2 = sorted(r2.tasks)
    assert ids1 == ids2
    assert len(ids1) == len(set(ids1))
    for t in r1.tasks.values():
        check_m11_atomic(t)
