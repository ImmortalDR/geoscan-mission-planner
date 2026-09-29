"""M10 CoverageCandidateGenerator — contract h1.m10.candidates.v1"""
from __future__ import annotations

from shapely.geometry import Polygon

from h1_coverage.config import CoverageConfig
from h1_coverage.contracts import check_m10_candidates
from h1_coverage.coverage.candidates import build_candidates
from h1_coverage.coverage.engine import build_coverage
from h1_coverage.geo import union_all
from h1_coverage.io.scene import load_scene
from h1_coverage.modules import by_id

from paths import scene


def test_module_registry_m10():
    assert by_id("M10").contract_id == "h1.m10.candidates.v1"


def test_m10_wind_scores_s09():
    s = load_scene(scene("S09_wind_feasibility"))
    r = build_coverage(s, CoverageConfig(strict_coverage=False))
    assert any(len(v) >= 2 for v in r.candidates.values())

    area = Polygon([(0, 0), (800, 0), (800, 600), (0, 600)])
    empty = union_all([])
    cfg = CoverageConfig(keep_candidates=5, angle_step_deg=30)
    a = build_candidates(
        area, 40, "j", wind_dir_from=0, wind_speed_ms=10,
        turn_cost_m=40, turn_radius_m=0, hard_nfz=empty, allowed=empty, cfg=cfg,
    )
    b = build_candidates(
        area, 40, "j", wind_dir_from=90, wind_speed_ms=10,
        turn_cost_m=40, turn_radius_m=0, hard_nfz=empty, allowed=empty, cfg=cfg,
    )
    check_m10_candidates(a)
    check_m10_candidates(b)
    # Best wind-aligned label / angle ranking should differ with wind axis
    assert a[0].angle_deg != b[0].angle_deg or a[0].label != b[0].label
