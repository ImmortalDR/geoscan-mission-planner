"""Analytic continuous-time separation between piecewise-linear trajectories."""
import math
from itertools import combinations
from bisect import bisect_left, bisect_right

_GROUND = {"ground", "service"}

def _segments(sortie):
    points = [dict(p, z_m=p.get("z_m", p.get("agl_m"))) for p in sortie["waypoints"]]
    for p in points:
        if not all(math.isfinite(float(p[k])) for k in ("x", "y", "z_m", "t_s")):
            raise ValueError("Trajectory coordinates and times must be finite")
    for a, b in zip(points, points[1:]):
        dt = b["t_s"] - a["t_s"]
        if dt < 0:
            raise ValueError("Trajectory timestamps must be nondecreasing")
        if dt == 0:
            # Adjacent scheduler phases can describe the same physical point
            # after independently evaluating an AGL expression.  Treat normal
            # floating-point noise as stationary, while still rejecting an
            # actual instantaneous displacement.
            if any(not math.isclose(a[k], b[k], rel_tol=0.0, abs_tol=1e-9)
                   for k in ("x", "y", "z_m")):
                raise ValueError("Zero-duration trajectory segment cannot move")
            continue
        if a.get("phase") in _GROUND and b.get("phase") in _GROUND:
            continue
        yield a, b


def _position(a, b, t):
    f = (t - a["t_s"]) / (b["t_s"] - a["t_s"])
    return tuple(a[k] + f * (b[k] - a[k]) for k in ("x", "y", "z_m"))


def _inside_interval(position, velocity, radius, duration):
    """Solve ||position + velocity*t|| < radius on [0, duration]."""
    speed2 = sum(v*v for v in velocity)
    if speed2 == 0:
        return (0.0, duration) if sum(p*p for p in position) < radius*radius else None
    center = -sum(p*v for p, v in zip(position, velocity)) / speed2
    closest2 = sum((p + center*v)**2 for p, v in zip(position, velocity))
    gap = radius*radius - closest2
    if gap <= 0:
        return None
    half_width = math.sqrt(gap / speed2)
    lo, hi = max(0.0, center-half_width), min(duration, center+half_width)
    return (lo, hi) if hi > lo else None


def detect_conflicts(sorties: list[dict], fleet: list[dict], *, first_only=False) -> list[dict]:
    """Find continuous conflicts, including simultaneous sorties of one UAV.

    Inputs are never changed. Unknown UAVs or invalid coordinates fail explicitly.
    Ground/service-only segments are excluded from inter-aircraft separation.
    first_only is an existence query for repair probes; the returned conflict
    need not be the earliest across pairs. Full reports use the default.
    """
    by_uav = {u["id"]: u for u in fleet}
    segments = {id(s): list(_segments(s)) for s in sorties}
    for s in sorties:
        u = by_uav[s["uav_id"]]
        for key in ("horizontal_separation_m", "vertical_separation_m"):
            if not math.isfinite(u[key]) or u[key] <= 0:
                raise ValueError("Separation thresholds must be positive and finite")
    conflicts = []
    for sa, sb in combinations(sorties, 2):
        ua, ub = by_uav[sa["uav_id"]], by_uav[sb["uav_id"]]
        h = max(ua["horizontal_separation_m"], ub["horizontal_separation_m"])
        v = max(ua["vertical_separation_m"], ub["vertical_separation_m"])
        base = dict(sortie_a=sa["id"], sortie_b=sb["id"],
                    uav_a=sa["uav_id"], uav_b=sb["uav_id"], required_h_m=h, required_v_m=v)
        if sa["uav_id"] == sb["uav_id"]:
            if not sa["waypoints"] or not sb["waypoints"]:
                continue
            start = max(sa["waypoints"][0]["t_s"], sb["waypoints"][0]["t_s"])
            end = min(sa["waypoints"][-1]["t_s"], sb["waypoints"][-1]["t_s"])
            if end > start:
                conflicts.append(dict(base, start_s=start, end_s=end,
                                      min_horizontal_m=0.0, kind="same_uav_overlap"))
                if first_only:return conflicts
            continue
        pair = []
        other_segments = segments[id(sb)]
        other_starts = [c["t_s"] for c, d in other_segments]
        other_ends = [d["t_s"] for c, d in other_segments]
        for a, b in segments[id(sa)]:
            first = bisect_left(other_ends, a["t_s"])
            last = bisect_right(other_starts, b["t_s"])
            for c, d in other_segments[first:last]:
                start, end = max(a["t_s"], c["t_s"]), min(b["t_s"], d["t_s"])
                if end < start:
                    continue
                pa, pb = _position(a, b, start), _position(c, d, start)
                rel = tuple(x-y for x, y in zip(pa, pb))
                if end == start:
                    distance = math.hypot(rel[0], rel[1])
                    if distance < h and abs(rel[2]) < v:
                        pair.append(dict(base, start_s=start, end_s=end,
                                         min_horizontal_m=distance, kind="separation"))
                        if first_only:return pair
                    continue
                velocity = tuple((b[k]-a[k])/(b["t_s"]-a["t_s"])
                                 - (d[k]-c[k])/(d["t_s"]-c["t_s"])
                                 for k in ("x", "y", "z_m"))
                horizontal = _inside_interval(rel[:2], velocity[:2], h, end-start)
                vertical = _inside_interval(rel[2:], velocity[2:], v, end-start)
                if horizontal is None or vertical is None:
                    continue
                lo, hi = max(horizontal[0], vertical[0]), min(horizontal[1], vertical[1])
                if hi <= lo:
                    continue
                speed2 = sum(x*x for x in velocity[:2])
                t = max(lo, min(hi, -sum(x*y for x, y in zip(rel[:2], velocity[:2])) / speed2)) if speed2 else lo
                minimum = math.hypot(rel[0]+t*velocity[0], rel[1]+t*velocity[1])
                pair.append(dict(base, start_s=start+lo, end_s=start+hi,
                                 min_horizontal_m=minimum, kind="separation"))
                if first_only:return pair
        # Coalesce adjacent segment-level intervals for a readable report.
        merged = []
        for conflict in sorted(pair, key=lambda c: c["start_s"]):
            if merged and conflict["start_s"] <= merged[-1]["end_s"]:
                merged[-1]["end_s"] = max(merged[-1]["end_s"], conflict["end_s"])
                merged[-1]["min_horizontal_m"] = min(merged[-1]["min_horizontal_m"], conflict["min_horizontal_m"])
            else:
                merged.append(conflict)
        conflicts.extend(merged)
    return sorted(conflicts, key=lambda c: (c["start_s"], str(c["sortie_a"]), str(c["sortie_b"])))
