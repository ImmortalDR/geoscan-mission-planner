from copy import deepcopy
import json

import pytest
from shapely.geometry import Point

import test_h3_core as checker_fixtures
from gmp.recommend.proposals import build_proposals, generate_proposals


@pytest.fixture
def example():
    case = checker_fixtures.CheckerTests()
    case.setUp()
    yield case
    case.doCleanups()


def snapshot(case):
    names = ("survey_areas", "allowed_airspace", "no_fly_zones", "temporal_airspace", "landing_sites", "obstacles")
    read = lambda name: json.loads((case.directory / name).read_text())
    return {"layers": {name: read(name + ".geojson") for name in names},
            **{name: read(name + ".json") for name in ("mission", "fleet", "payload_catalog", "metadata")}}


def test_candidate_base_first_and_no_implicit_safety_claim(example):
    example.sites.append((Point(0, -500), {"id": "CANDIDATE_BASE", "role": "both", "candidate": True}))
    example.layer("landing_sites.geojson", example.sites)
    source = snapshot(example)
    original = deepcopy(source)
    proposals = build_proposals(source, [], limit=3)
    assert proposals[0]["type"] == "change_base"
    assert not proposals[0]["verified"] and proposals[0]["requires_replan"]
    changed = proposals[0]["documents"]
    first = changed["fleet.json"]["uavs"][0]
    assert first["start_site"] == first["landing_site"]
    assert first["start_site"].startswith("ACTIVE_")
    site = next(f for f in changed["landing_sites.geojson"]["features"] if f["properties"]["id"] == first["start_site"])
    assert site["properties"]["candidate"] is False
    assert source == original


def test_reserve_and_forbidden_sites_never_become_routine_bases(example):
    example.sites.append((Point(0, 500), {"id": "RESERVE", "role": "reserve", "candidate": True}))
    example.sites.append((Point(2000, 500), {"id": "OUTSIDE", "role": "both", "candidate": True}))
    example.layer("landing_sites.geojson", example.sites)
    for proposal in build_proposals(snapshot(example), [], limit=8):
        for uav in proposal["documents"].get("fleet.json", {}).get("uavs", []):
            assert uav["start_site"] not in {"RESERVE", "OUTSIDE"}
            assert uav["landing_site"] not in {"RESERVE", "OUTSIDE"}


def test_equipment_proposals_preserve_reserve_and_catalog_provenance(example):
    template = {**deepcopy(example.uav), "model": "geoscan_701", "operational_endurance_min": 480,
                "energy_reserve_fraction": .1, "catalog_provenance": {"source": "test fixture"}}
    proposals = build_proposals(snapshot(example), [template], limit=8)
    replacement = next(p for p in proposals if p["type"] == "replace_uav")
    uav = replacement["documents"]["fleet.json"]["uavs"][0]
    assert uav["energy_reserve_fraction"] == .2
    assert uav["catalog_provenance"] == template["catalog_provenance"]
    assert replacement["assumptions"]
    assert any(p["type"] == "add_uav" for p in proposals)


def test_no_addition_above_ten_aircraft(example):
    data = snapshot(example)
    data["fleet"]["uavs"] = [{**example.uav, "id": f"U{i}"} for i in range(10)]
    assert not any(p["type"] == "add_uav" for p in build_proposals(data, [], limit=8))


def test_time_alternative_stays_inside_original_window(example):
    data = snapshot(example)
    data["layers"]["temporal_airspace"]["features"] = [{
        "properties": {"active_to": example.time(300)}}, {"properties": {"active_to": example.time(9000)}}]
    before = deepcopy(data)
    proposals = build_proposals(data, [], limit=8)
    delayed = [p for p in proposals if p["type"] == "delay_start"]
    assert len(delayed) == 1
    changed = delayed[0]["documents"]["mission.json"]
    assert changed["mission_window"]["start"] == example.time(301)
    assert changed["mission_window"]["end"] == example.time(7200)
    assert changed["wind"] == data["mission"]["wind"]
    assert changed["validation_policy"] == data["mission"]["validation_policy"]
    assert data == before


def test_bounded_deterministic_and_no_weakening_documents(example):
    data = snapshot(example)
    for i in range(20):
        feature = deepcopy(data["layers"]["landing_sites"]["features"][0])
        feature["properties"].update(id=f"CANDIDATE_{i}", role="both", candidate=True)
        data["layers"]["landing_sites"]["features"].append(feature)
    first = build_proposals(data, [], limit=100)
    assert len(first) == 8
    assert first == build_proposals(data, [], limit=100)
    for proposal in first:
        assert not set(proposal["documents"]).intersection({"survey_areas.geojson", "no_fly_zones.geojson",
                                                            "allowed_airspace.geojson", "payload_catalog.json", "dem.tif"})


def test_public_adapter_reads_only_current_input(example):
    proposals = generate_proposals(example.directory, limit=3)
    assert len(proposals) <= 3
    assert all(not p["verified"] and p["requires_user_selection"] for p in proposals)
