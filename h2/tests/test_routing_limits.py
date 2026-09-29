from dataclasses import replace

import pytest

from h2.planner import plan_bundle
from h2.routing_solver import ImprovementLimit
from h2.scenarios import make_bundle
from h2.scheduler import Settings


def test_stagnation_requires_a_solution_and_only_strict_improvement_resets_patience():
    limit = ImprovementLimit(100)
    assert not limit.reached(1000000)  # Do not abandon feasibility search.
    limit.observe(500, 1000000)
    limit.observe(600, 1000050)
    limit.observe(500, 1000099)
    assert not limit.reached(1000099)
    assert limit.reached(1000100)  # Cycling/equal-cost alternatives do not reset.
    limit.observe(499, 1000100)
    assert not limit.reached(1000199)
    assert limit.reached(1000200)


@pytest.mark.parametrize('objective', ['makespan', 'total_flight'])
def test_deep_search_retains_complete_deterministic_plan_with_bounded_patience(objective):
    bundle = make_bundle(4, 2, 30)
    settings = Settings(algorithm='routing', search_depth='deep', time_budget_s=180, objective=objective)
    first = plan_bundle(bundle, settings)
    again = plan_bundle(bundle, replace(settings))
    assert first['status'] == 'FEASIBLE', first['checks']
    assert first['routing']['solution_sha256'] == again['routing']['solution_sha256']
    assert first['routing']['stagnation_read_limit'] == 20000
    assert first['routing']['stagnation_read_limit'] < first['routing']['table_read_limit']
    assert len({t for s in first['sorties'] for t in s['task_ids']}) == 4
