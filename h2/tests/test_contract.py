import copy
from pathlib import Path
import pytest
from h2.contract import load_bundle, ContractError

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("path", sorted((ROOT / "fixtures").glob("*.bundle.json")))
def test_original_h1(path):
    before = path.read_bytes()
    d = load_bundle(path)
    assert d["schema_version"] == "gmp.h1_h2.v1"
    assert path.read_bytes() == before


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(schema_version="gmp.h1_h2.v2"),
    lambda d: d["tasks"][0].pop("entry"),
    lambda d: d["tasks"][0].update(entry=[1, 2]),
    lambda d: d["fleet"][0].update(ground_speed_ms=float("nan")),
    lambda d: d["feasibility"]["eligible_uav_ids_by_task"].clear(),
    lambda d: d["mission"].update(objectives=["BALANCED"]),
    lambda d: d["crs"].update(metric_epsg=4326),
])
def test_reject_invalid(mutation):
    d = load_bundle(ROOT / "fixtures/S00_smoke_rgb.bundle.json")
    mutation(d)
    with pytest.raises(ContractError):
        load_bundle(d)


def test_extensions_optional_ignored_and_input_not_mutated():
    d = load_bundle(ROOT / "fixtures/S00_smoke_rgb.bundle.json")
    d["extensions"] = {"alien": {"anything": [1, 2, 3]}}
    before = copy.deepcopy(d)
    assert load_bundle(d) == before == d
