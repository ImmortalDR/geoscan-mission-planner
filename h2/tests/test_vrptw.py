from dataclasses import replace
import pytest

from h2.benchmark.vrptw import Customer, Instance, Route, Visit, parse, solve, validate

def small():
    return Instance('small', 2, 1, (
        Customer(0, 0, 0, 0, 10, 30, 0),
        Customer(1, 1, 0, 1, 15, 20, 2),
        Customer(2, 0, 1, 1, 15, 20, 2),
    ))


def test_service_capacity_and_all_depot_windows():
    result = solve(small(), .2)
    assert result.feasible
    assert len(result.routes) == 2
    for route in result.routes:
        assert route.visits[0].service_start >= 10
        assert route.visits[-1].service_start <= 30
        assert route.visits[-1].service_start >= route.visits[1].service_start + 3
        assert route.load == 1


def test_baseline_and_improvement_are_both_valid():
    for method in ('baseline', 'guided_local_search'):
        assert solve(small(), .1, method).feasible


def test_independent_validator_detects_forged_plan():
    route = Route(0, (Visit(0, 0), Visit(1, 0), Visit(1, 0), Visit(0, 0)), 999, 0)
    errors = validate(small(), [route, route])
    for expected in ('time window', 'impossible travel', 'capacity exceeded',
                     'incorrect reported load', 'incorrect reported distance',
                     'multiple routes', 'customer 1: visited 4', 'customer 2: visited 0'):
        assert any(expected in error for error in errors), errors


def test_impossible_customer_not_silently_dropped():
    instance = replace(small(), capacity=1, customers=(small().customers[0],
                        replace(small().customers[1], demand=2)))
    result = solve(instance, .1)
    assert not result.feasible
    assert result.routes == []


def test_depot_return_window_enforced():
    instance = Instance('return', 1, 2, (
        Customer(0, 0, 0, 0, 0, 10, 0),
        Customer(1, 6, 0, 1, 0, 9, 0)))
    assert not solve(instance, .1).feasible


def test_malformed_parser_and_invalid_data(tmp_path):
    path = tmp_path / 'bad.txt'
    path.write_text('BAD\nVEHICLE\n1 10\nCUSTOMER\n0 1 2\n')
    with pytest.raises(ValueError, match='seven'):
        parse(path)
    with pytest.raises(ValueError, match='duplicate'):
        replace(small(), customers=(small().customers[0], small().customers[1], small().customers[1]))
    with pytest.raises(ValueError, match='nonfinite'):
        replace(small(), customers=(replace(small().customers[0], x=float('nan')),))


def test_arbitrary_customer_ids(tmp_path):
    path = tmp_path / 'ids.txt'
    path.write_text('IDS\nVEHICLE\n1 10\nCUSTOMER\n0 0 0 0 0 30 0\n42 1 0 1 0 20 0\n')
    result = solve(parse(path), .1)
    assert result.feasible
    assert result.routes[0].visits[1].customer_id == 42


def test_c101_mini_fixture_parses_and_solves():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "fixtures" / "benchmarks" / "c101_mini.txt"
    instance = parse(path)
    assert instance.vehicles == 3 and instance.capacity == 50
    assert len(instance.customers) == 6  # depot + 5
    result = solve(instance, 1.0, "baseline")
    # Mini slice may be tight; solver must not crash and must validate if feasible.
    if result.feasible:
        assert validate(instance, result.routes) == []
