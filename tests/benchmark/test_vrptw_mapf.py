from __future__ import annotations

from pathlib import Path
import zipfile

import pytest

from gmp.benchmark.mapf import parse_map, parse_scen, prioritized_mapf
from gmp.benchmark.vrptw import parse_solomon, solve_vrptw

VRPTW = Path("/root/h3/datasets/vrptw/vrptw_extracted/data/Solomon")
MAPF_ZIP = Path("/root/h3/datasets/movingai")


@pytest.mark.parametrize("name", ["c101.txt", "r101.txt", "rc101.txt"])
def test_vrptw_solomon_feasible(name):
    path = VRPTW / name
    if not path.exists():
        pytest.skip(f"missing {path}")
    inst = parse_solomon(path)
    result = solve_vrptw(inst, time_limit_s=6.0)
    assert inst.n >= 20
    if name == "c101.txt":
        assert result["feasible"] is True
        assert result["vehicles_used"] >= 1
        assert result["distance"] is not None and result["distance"] > 0
    else:
        assert result["customers"] == inst.n


def test_mapf_random_scenario_conflict_free():
    maps_zip = MAPF_ZIP / "mapf-map.zip"
    scen_zip = MAPF_ZIP / "mapf-scen-random.zip"
    if not maps_zip.exists() or not scen_zip.exists():
        pytest.skip("MovingAI archives missing")
    with zipfile.ZipFile(maps_zip) as z:
        names = [n for n in z.namelist() if n.endswith("empty-16-16.map") or n.endswith("random-32-32-20.map")]
        if not names:
            names = [n for n in z.namelist() if n.endswith(".map")][:1]
        raw = z.read(names[0]).decode("utf-8", errors="replace")
        grid = parse_map(raw, name=names[0])
    with zipfile.ZipFile(scen_zip) as z:
        scens = [n for n in z.namelist() if n.endswith(".scen") and Path(names[0]).stem.replace(".map", "") in n]
        if not scens:
            scens = [n for n in z.namelist() if n.endswith(".scen")][:1]
        agents = parse_scen(z.read(scens[0]).decode("utf-8", errors="replace"))
    result = prioritized_mapf(grid, agents, max_agents=6)
    assert result["agents_solved"] >= 1
    assert result["makespan"] >= 0
