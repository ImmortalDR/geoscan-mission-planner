from copy import deepcopy
import pytest

from h2.benchmark.uav_adapter import adapt
from h2.benchmark.vrptw import Customer, Instance
from h2.contract import validate_bundle
from h2.scheduler import Settings

def instance():
    customers = [Customer(0, 0, 0, 0, 0, 1000, 0)]
    customers += [Customer(i, float(i), float(i % 3), 1, 0, 900, 2)
                  for i in range(1, 13)]
    return Instance('inline', 5, 20, tuple(customers))


def test_synthetic_transform_frozen_contract_and_repeatability():
    source = instance()
    bundle, side = adapt(source, fleet_size=5, customer_limit=10)
    assert (bundle, side) == adapt(source, fleet_size=5, customer_limit=10)
    assert validate_bundle(bundle) is bundle
    assert len(bundle['sites']) == 2
    assert {u['model'] for u in bundle['fleet']} == {
        'geoscan_201','geoscan_401','geoscan_701','geoscan_801','geoscan_gemini'}
    for task, customer in zip(bundle['tasks'], source.customers[1:11]):
        assert task['entry'] == [500000+10*customer.x, 6000000+10*customer.y]
        assert 'uav_id' not in task
        assert side['task_service_s'][task['id']] == customer.service+.1*customer.demand
        assert side['task_windows'][task['id']] == [5*customer.ready,5*customer.due+1800]
    assert any(len(ids)<5 for ids in bundle['feasibility']['eligible_uav_ids_by_task'].values())
    assert not bundle['extensions']['h2_benchmark']['bks_comparable']


@pytest.mark.parametrize('size', [1,11])
def test_invalid_fleet_size(size):
    with pytest.raises(ValueError, match='fleet_size'):
        adapt(instance(), fleet_size=size)


def test_ten_real_customers_through_production_h2():
    from h2.planner import plan_bundle
    bundle, side = adapt(instance(), customer_limit=10)
    original = deepcopy(bundle)
    plan = plan_bundle(bundle, settings=Settings(time_budget_s=15, max_iterations=20, **side))
    assert bundle == original
    assert plan['status'] == 'FEASIBLE', plan
    assert plan['checks']['passed'], plan['checks']
    assert not plan['unassigned'], plan
    tasks = [tid for sortie in plan['sorties'] for tid in sortie['task_ids']]
    assert len(tasks) == len(set(tasks)) == 10
    assert set(tasks) == {t['id'] for t in bundle['tasks']}
    for sortie in plan['sorties']:
        assert sortie['resource_margin_s'] >= 0
        for tid in sortie['task_ids']:
            assert sortie['uav_id'] in bundle['feasibility']['eligible_uav_ids_by_task'][tid]
