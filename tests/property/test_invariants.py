"""Property-style invariants that do not re-implement H1."""
from __future__ import annotations

import math

from shapely.geometry import Polygon


def test_polygon_area_positive():
    p = Polygon([(0, 0), (10, 0), (10, 5), (0, 5)])
    assert p.area == 50
    assert math.isfinite(p.length)
