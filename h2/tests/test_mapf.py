import pytest
from h2.benchmark.mapf import Grid, run, shortest_path


def test_grid_paths_run_h2_deconfliction():
    grid = Grid('inline-8x8', tuple('........' for _ in range(8)))
    pairs = [((0,0),(1,0)), ((5,3),(5,6)), ((1,7),(6,4)),
             ((0,5),(7,4)), ((3,0),(1,5)), ((0,2),(2,4)),
             ((3,7),(5,1)), ((6,7),(7,1)), ((1,2),(1,1)),
             ((2,3),(2,7))]
    result = run(grid, pairs)
    assert len(result['sorties']) == 10
    assert result['feasible']
    assert result['conflicts_before']
    assert not result['conflicts_after']
    for original, resolved in zip(result['original_sorties'], result['sorties']):
        assert [(p['x'],p['y']) for p in original['waypoints']] == [(p['x'],p['y']) for p in resolved['waypoints']]


def test_opposing_edge_swap_detected_between_points():
    result = run(Grid('two', ('..',)), [((0,0),(1,0)), ((1,0),(0,0))])
    assert result['conflicts_before']
    conflict = result['conflicts_before'][0]
    assert 0 < conflict['start_s'] < .5 < conflict['end_s'] < 1
    assert result['feasible']
    assert result['makespan'] > 2


def test_blocked_window_keeps_explicit_residual():
    result = run(Grid('two', ('..',)), [((0,0),(1,0)), ((1,0),(0,0))], window_end_s=1)
    assert not result['feasible']
    assert result['conflicts_after']


def test_bfs_respects_obstacles_and_unreachable():
    grid = Grid('wall', ('...', '.@.', '...'))
    path = shortest_path(grid, (0,1), (2,1))
    assert len(path) == 5
    assert all(grid.free(p) for p in path)
    with pytest.raises(ValueError, match='unreachable'):
        shortest_path(Grid('wall', ('.@.',)), (0,0), (2,0))
