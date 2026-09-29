import math
import random

import pytest

from h2.deconfliction import detect_conflicts, resolve_conflicts


FLEET = [dict(id=u, horizontal_separation_m=2., vertical_separation_m=3.) for u in ("a", "b")]


def sortie(uav, start, finish, t0=0., t1=10., sid=None, phase="survey"):
    return dict(id=sid or uav, uav_id=uav, start_s=t0, end_s=t1,
                waypoints=[dict(x=p[0], y=p[1], agl_m=p[2], t_s=t, phase=phase)
                           for p, t in ((start, t0), (finish, t1))])


def crossing():
    return [sortie("a", (-10, 0, 100), (10, 0, 100)),
            sortie("b", (0, -10, 100), (0, 10, 100))]


def test_between_waypoints_analytic_crossing():
    conflict, = detect_conflicts(crossing(), FLEET)
    assert conflict["start_s"] == pytest.approx(5 - 1/math.sqrt(2))
    assert conflict["end_s"] == pytest.approx(5 + 1/math.sqrt(2))
    assert conflict["min_horizontal_m"] == pytest.approx(0)


def test_conflict_existence_query_matches_full_report_on_piecewise_paths():
    rng=random.Random(20260918)
    for _ in range(60):
        sorties=[]
        for uid in ('a','b'):
            points=[dict(x=rng.uniform(-10,10),y=rng.uniform(-10,10),z_m=rng.uniform(95,105),t_s=i*5.,phase='survey') for i in range(8)]
            sorties.append(dict(id=uid,uav_id=uid,waypoints=points))
        assert bool(detect_conflicts(sorties,FLEET,first_only=True))==bool(detect_conflicts(sorties,FLEET))


def test_vertical_and_horizontal_must_coincide():
    a = sortie("a", (0, 0, 100), (0, 0, 100))
    b = sortie("b", (0, 0, 100), (100, 0, 200))
    conflict, = detect_conflicts([a, b], FLEET)
    assert conflict["end_s"] == pytest.approx(.2)
    b = sortie("b", (0, 0, 200), (100, 0, 100))
    assert detect_conflicts([a, b], FLEET) == []


@pytest.mark.parametrize("offset", [(2, 0, 0), (0, 0, 3)])
def test_exact_threshold_safe(offset):
    a = sortie("a", (0, 0, 100), (10, 0, 100))
    b = sortie("b", (offset[0], offset[1], 100+offset[2]), (10+offset[0], offset[1], 100+offset[2]))
    assert detect_conflicts([a, b], FLEET) == []


def test_stationary_vertical_change_and_amsl():
    a = sortie("a", (0, 0, 100), (0, 0, 100))
    b = sortie("b", (0, 0, 90), (0, 0, 110))
    c, = detect_conflicts([a, b], FLEET)
    assert (c["start_s"], c["end_s"]) == pytest.approx((3.5, 6.5))
    for p in b["waypoints"]:
        p["z_m"] = 200
    assert detect_conflicts([a, b], FLEET) == []


def test_ground_excluded_but_takeoff_active():
    a = sortie("a", (0, 0, 0), (0, 0, 0), phase="ground")
    b = sortie("b", (0, 0, 0), (0, 0, 0), phase="service")
    assert detect_conflicts([a, b], FLEET) == []
    for p in a["waypoints"] + b["waypoints"]:
        p["phase"] = "takeoff"
    assert len(detect_conflicts([a, b], FLEET)) == 1


def test_same_uav_cannot_have_concurrent_sorties_even_far_apart():
    a = sortie("a", (0, 0, 100), (10, 0, 100), sid="one")
    b = sortie("a", (1000, 0, 100), (1010, 0, 100), sid="two")
    assert detect_conflicts([a, b], FLEET)[0]["kind"] == "same_uav_overlap"
    plan, conflicts = resolve_conflicts([a, b], FLEET)
    assert not conflicts
    assert plan[1]["start_s"] >= plan[0]["end_s"]


def test_resolver_preserves_input_downstream_gaps_and_task_times():
    original = crossing()
    original.append(sortie("b", (0, -10, 100), (0, 10, 100), 20, 30, sid="b2"))
    original[1]["task_times"] = [dict(task_id="task", start_s=2., end_s=8.)]
    result, conflicts = resolve_conflicts(original, FLEET)
    assert conflicts == []
    assert result[2]["start_s"] - result[1]["end_s"] == pytest.approx(10)
    assert result[1]["task_times"][0]["start_s"] == pytest.approx(result[1]["start_s"]+2)
    assert original[1]["start_s"] == 0
    assert detect_conflicts(result, FLEET) == []
    # Window blocks the cascade; survey altitude and geometry remain immutable.
    result, conflicts = resolve_conflicts(original, FLEET, window_end_s=30)
    assert conflicts
    assert conflicts == detect_conflicts(result, FLEET)
    assert result == original


def test_blocked_shift_does_not_invent_fixed_wing_survey_altitude():
    fleet = [dict(id="a", horizontal_separation_m=2., vertical_separation_m=3.),
             dict(id="b", horizontal_separation_m=2., vertical_separation_m=3., uav_class="fixed_wing")]
    a, b = crossing()
    result, conflicts, actions = resolve_conflicts([a, b], fleet, window_end_s=5, return_actions=True)
    assert conflicts
    assert actions == []
    assert result == [a, b]


def test_blocked_multirotor_shift_does_not_change_flight_resource():
    fleet = [dict(u, uav_class="multirotor") for u in FLEET]
    a, b = crossing()
    b.update(flight_time_s=10, resource_margin_s=0)
    result, conflicts, actions = resolve_conflicts([a, b], fleet, window_end_s=10, return_actions=True)
    assert conflicts
    assert actions == []
    assert result == [a, b]


def test_invalid_trajectory_rejected():
    a, b = crossing()
    a["waypoints"][1]["t_s"] = -1
    with pytest.raises(ValueError):
        detect_conflicts([a, b], FLEET)


def test_zero_duration_roundoff_is_stationary_but_real_motion_is_rejected():
    a = sortie("a", (0, 0, 100), (10, 0, 100))
    a["waypoints"].insert(1, dict(x=10, y=0, agl_m=100 + 2e-14,
                                  t_s=10, phase="survey"))
    assert detect_conflicts([a], FLEET) == []
    a["waypoints"][1]["x"] = 10.001
    with pytest.raises(ValueError, match="Zero-duration"):
        detect_conflicts([a], FLEET)


def test_shared_active_endpoint_and_exact_tangency():
    a = sortie("a", (-10, 0, 100), (0, 0, 100))
    b = sortie("b", (0, 0, 100), (10, 0, 100), 10, 20)
    c, = detect_conflicts([a, b], FLEET)
    assert c["start_s"] == c["end_s"] == 10
    b = sortie("b", (-10, 2, 100), (10, 2, 100))
    a = sortie("a", (0, 0, 100), (0, 0, 100))
    assert detect_conflicts([a, b], FLEET) == []


def test_uses_stricter_pair_separation():
    fleet = [dict(u) for u in FLEET]
    fleet[1]["horizontal_separation_m"] = 5
    a = sortie("a", (0, 0, 100), (0, 0, 100))
    b = sortie("b", (4, 0, 100), (4, 0, 100))
    c, = detect_conflicts([a, b], fleet)
    assert c["required_h_m"] == 5


def test_random_segments_against_dense_independent_oracle():
    rng = random.Random(731)
    for _ in range(100):
        endpoints = [tuple(rng.uniform(-5, 5) for _ in range(3)) for _ in range(4)]
        a = sortie("a", endpoints[0], endpoints[1])
        b = sortie("b", endpoints[2], endpoints[3])
        conflicts = detect_conflicts([a, b], FLEET)
        for step in range(1, 500):
            fraction = step / 500
            pa = [endpoints[0][j]*(1-fraction)+endpoints[1][j]*fraction for j in range(3)]
            pb = [endpoints[2][j]*(1-fraction)+endpoints[3][j]*fraction for j in range(3)]
            expected = math.hypot(pa[0]-pb[0], pa[1]-pb[1]) < 2 and abs(pa[2]-pb[2]) < 3
            actual = any(c["start_s"] < fraction*10 < c["end_s"] for c in conflicts)
            assert actual == expected
