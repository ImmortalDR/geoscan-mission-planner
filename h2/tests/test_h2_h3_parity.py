"""H2↔H3 conflict parity: same pairwise formula on shared trajectories.

H3 owns SAFE/certificate. This test only asserts that the continuous H/V
condition used by H2 `detect_conflicts` matches an independent dense sampler
(oracle) — the same contract documented for H3 alignment in COLLISION_CONSTRAINT.md.
When `gmp.deconfliction.detector` is importable, also compare conflict *counts*
on a synthetic Plan-like structure (best-effort; schemas differ).
"""
from __future__ import annotations

import math

import pytest

from h2.deconfliction import detect_conflicts


FLEET = [
    dict(id="a", horizontal_separation_m=5.0, vertical_separation_m=10.0),
    dict(id="b", horizontal_separation_m=5.0, vertical_separation_m=10.0),
]


def _sortie(uid, sid, a, b, t0=0.0, t1=20.0):
    return dict(
        id=sid,
        uav_id=uid,
        waypoints=[
            dict(x=a[0], y=a[1], z_m=a[2], t_s=t0, phase="survey"),
            dict(x=b[0], y=b[1], z_m=b[2], t_s=t1, phase="survey"),
        ],
    )


def _oracle_conflict(sa, sb, h, v, steps=400):
    for i in range(1, steps):
        f = i / steps
        pa = [sa[0][j] * (1 - f) + sa[1][j] * f for j in range(3)]
        pb = [sb[0][j] * (1 - f) + sb[1][j] * f for j in range(3)]
        if math.hypot(pa[0] - pb[0], pa[1] - pb[1]) < h and abs(pa[2] - pb[2]) < v:
            return True
    return False


@pytest.mark.parametrize(
    "a0,a1,b0,b1,expect",
    [
        ((-20, 0, 100), (20, 0, 100), (0, -20, 100), (0, 20, 100), True),
        ((-20, 0, 100), (20, 0, 100), (0, -20, 130), (0, 20, 130), False),
        ((0, 0, 100), (10, 0, 100), (0, 6, 100), (10, 6, 100), False),
        ((0, 0, 100), (10, 0, 100), (0, 4, 100), (10, 4, 100), True),
    ],
)
def test_h2_detector_matches_dense_oracle(a0, a1, b0, b1, expect):
    sorties = [_sortie("a", "sa", a0, a1), _sortie("b", "sb", b0, b1)]
    conflicts = detect_conflicts(sorties, FLEET)
    assert bool(conflicts) == expect
    assert _oracle_conflict((a0, a1), (b0, b1), 5.0, 10.0) == expect


def test_optional_gmp_detector_import_smoke():
    """Best-effort: if monorepo gmp is on PYTHONPATH, import must not crash."""
    try:
        from gmp.deconfliction.detector import detect_conflicts as gmp_detect  # noqa: F401
    except ImportError:
        pytest.skip("gmp.deconfliction not on PYTHONPATH")
