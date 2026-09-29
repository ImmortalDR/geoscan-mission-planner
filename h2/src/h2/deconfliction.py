"""Continuous separation checks for piecewise linear, local-metric trajectories.

Both horizontal and vertical separation must be violated simultaneously.
Returned interval bounds delimit an open violation interval; equality is safe.
AMSL z_m is preferred; agl_m fallback assumes a common flat reference.
"""

from copy import deepcopy
import math

from .algorithms.conflict_detection import detect_conflicts

def _shift_times(sortie: dict, shift: float) -> None:
    for waypoint in sortie["waypoints"]:
        waypoint["t_s"] += shift
    for timing in sortie.get("task_times", []):
        timing["start_s"] += shift
        timing["end_s"] += shift
    for key in ("start_s", "end_s", "departure_s", "arrival_s", "takeoff_s", "landing_s"):
        if key in sortie:
            sortie[key] += shift


def resolve_conflicts(sorties: list[dict], fleet: list[dict], window_end_s=None, *, return_actions: bool = False):
    """Resolve conflicts by ground departure shifts with downstream propagation.

    Airborne holds and altitude changes require rebuilding the route with its
    terrain, resource and immutable survey-altitude constraints. This trajectory
    utility has none of that context, so blocked shifts keep explicit residuals.
    Assignment reorder/relocate/LNS remain in planner.py.
    """
    result = deepcopy(sorties)
    actions: list[dict] = []
    if window_end_s is not None and not math.isfinite(window_end_s):
        raise ValueError("Mission end must be finite")
    by_id = {s["id"]: s for s in result}
    if len(by_id) != len(result):
        raise ValueError("Sortie identifiers must be unique")
    priority = {s["id"]: i for i, s in enumerate(sorted(result, key=lambda s: (s["waypoints"][0]["t_s"], str(s["id"]))))}
    tried: dict[tuple, set[int]] = {}
    for _ in range(max(1, len(result) ** 2 * 4)):
        conflicts = detect_conflicts(result, fleet)
        if not conflicts:
            return (result, [], actions) if return_actions else (result, [])
        conflict = conflicts[0]
        early, late = sorted(
            (by_id[conflict["sortie_a"]], by_id[conflict["sortie_b"]]),
            key=lambda s: priority[s["id"]],
        )
        key = (conflict["sortie_a"], conflict["sortie_b"], round(conflict["start_s"], 3))
        used = tried.setdefault(key, set())

        # Rung 1 — ground shift (original behaviour).
        if 1 not in used:
            used.add(1)
            start = late["waypoints"][0]["t_s"]
            shift = early["waypoints"][-1]["t_s"] - start + 1e-6
            if shift > 0:
                downstream = [
                    s for s in result
                    if s["uav_id"] == late["uav_id"] and priority[s["id"]] >= priority[late["id"]]
                ]
                blocked = (
                    window_end_s is not None
                    and any(s["waypoints"][-1]["t_s"] + shift > window_end_s for s in downstream)
                )
                if not blocked:
                    for sortie in downstream:
                        _shift_times(sortie, shift)
                    actions.append(dict(rung=1, action="shift_start", sortie=late["id"], uav=late["uav_id"]))
                    continue

        residual = detect_conflicts(result, fleet)
        return (result, residual, actions) if return_actions else (result, residual)
    residual = detect_conflicts(result, fleet)
    return (result, residual, actions) if return_actions else (result, residual)
