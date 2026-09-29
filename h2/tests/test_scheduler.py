from copy import deepcopy
import pytest
from conftest import make_bundle
from h2.scheduler import Scheduler, Settings, RouteFailure


def test_return_and_reserve_included():
    d = make_bundle(1,1)
    s = Scheduler(d,Settings(survey_speed_factor=1))
    route = s.schedule_uav("u0",["t0"])[0]
    # 1000 m at 10 m/s, plus 60 m climb and descent at 3 m/s.
    assert route["flight_time_s"] == pytest.approx(140)
    assert route["waypoints"][0]["agl_m"] == route["waypoints"][-1]["agl_m"] == 0
    assert route["distance_m"] == pytest.approx(1000)
    d["fleet"][0]["operational_endurance_min"] = 139/60/.8
    with pytest.raises(RouteFailure):
        Scheduler(d,Settings(survey_speed_factor=1)).schedule_uav("u0",["t0"])


def test_multisortie_service_and_no_mutation():
    d = make_bundle(3,1,endurance_min=4)
    original = deepcopy(d)
    routes = Scheduler(d,Settings()).schedule_uav("u0",["t0","t1","t2"])
    assert len(routes) >= 2
    assert sorted(t for r in routes for t in r["task_ids"]) == ["t0", "t1", "t2"]
    for a,b in zip(routes,routes[1:]):
        assert b["start_s"] == pytest.approx(a["end_s"]+30)
        assert a["landing_site_id"] == b["start_site_id"]
    assert d == original


def test_different_start_end_and_landing_only():
    d = make_bundle(1,1)
    d["sites"].append(dict(id="end",x=500500.,y=6000000.,role="landing"))
    d["fleet"][0]["landing_site"] = "end"
    with pytest.raises(RouteFailure):
        Scheduler(d,Settings()).schedule_uav("u0",["t0"])
    d["mission"]["allow_different_start_end"] = True
    route=Scheduler(d,Settings()).schedule_uav("u0",["t0"])[0]
    assert route["landing_site_id"] == "end"
    assert route["transit_length_m"] == pytest.approx(100)


def test_task_window_wait_is_on_ground():
    d=make_bundle(1,1)
    route=Scheduler(d,Settings(task_windows={"t0":[100,120]})).schedule_uav("u0",["t0"])[0]
    assert route["task_times"][0]["start_s"] == pytest.approx(100)
    assert route["start_s"] > 0
    assert not any(p["phase"] == "hold" for p in route["waypoints"])


def test_candidate_site_not_activated():
    d=make_bundle(1,1)
    d["sites"][0]["candidate"]=True
    with pytest.raises(RouteFailure):
        Scheduler(d,Settings()).schedule_uav("u0",["t0"])


@pytest.mark.parametrize("end_role,expected_end", [("landing", "near_end"), ("reserve", "far")])
def test_unpinned_uav_chooses_best_allowed_landing_site(end_role, expected_end):
    d = make_bundle(1, 1)
    d["sites"] = [
        dict(id="far", x=499000., y=6000000., role="both", candidate=False),
        dict(id="near_start", x=500090., y=6000000., role="start", candidate=False),
        dict(id="near_end", x=500510., y=6000000., role=end_role, candidate=False),
    ]
    d["fleet"][0]["start_site"] = None
    d["fleet"][0]["landing_site"] = None
    d["mission"]["allow_different_start_end"] = True

    route = Scheduler(d, Settings(survey_speed_factor=1)).schedule_uav("u0", ["t0"])[0]

    assert route["start_site_id"] == "near_start"
    assert route["landing_site_id"] == expected_end
    assert route["waypoints"][0]["x"] == pytest.approx(500090)
    assert route["waypoints"][-1]["x"] == pytest.approx(500510 if end_role == "landing" else 499000)
    assert route["task_ids"] == ["t0"]


def test_dense_terrain_route_has_no_roundoff_only_dwells():
    bundle = make_bundle(1, 1)
    scene = dict(schema_version="h2.scene.v1", crs=bundle["crs"], terrain=dict(
        origin=[500000, 6000000], cell_size_m=10, values=[[0]*51, [0]*51]))
    route = Scheduler(bundle, Settings(), scene).schedule_uav("u0", ["t0"])[0]
    for a, b in zip(route["waypoints"], route["waypoints"][1:]):
        assert b["t_s"] == a["t_s"] or b["t_s"]-a["t_s"] > 1e-7
