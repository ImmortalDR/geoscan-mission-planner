"""Conservative temporal airspace scheduling through departure shifts."""
import math
from copy import deepcopy
from ..scheduler import RouteFailure

def _temporal_schedule(sorties, scene, horizon):
    """Conservative departure shifts around sidecar temporal restrictions.

    All points/segments, any altitude, entire sortie interval are considered.
    This may overconstrain but cannot skip a between-waypoint zone crossing.
    """
    if not scene or not scene.get("temporal"):
        return sorties
    from shapely.geometry import LineString, shape
    sorties = deepcopy(sorties)
    for _ in range(len(sorties)*len(scene["temporal"])+1):
        changed = False
        for s in sorted(sorties, key=lambda s: s["start_s"]):
            line = LineString([(p["x"],p["y"]) for p in s["waypoints"]])
            for zone in scene["temporal"]:
                start, end = zone["start_s"], zone["end_s"]
                if not all(math.isfinite(t) for t in (start,end)) or start < 0 or end < start:
                    raise ValueError("invalid temporal zone window")
                if s["end_s"] >= start and s["start_s"] <= end and line.intersects(shape(zone["geometry"])):
                    delta = end-s["start_s"]+1e-3
                    followers = [x for x in sorties if x["uav_id"] == s["uav_id"] and x["start_s"] >= s["start_s"]]
                    if horizon is not None and any(x["end_s"]+delta > horizon for x in followers):
                        raise RouteFailure("temporal restriction exceeds mission window")
                    for x in followers:
                        x["start_s"] += delta
                        x["end_s"] += delta
                        for p in x["waypoints"]:
                            p["t_s"] += delta
                        for t in x["task_times"]:
                            t["start_s"] += delta
                            t["end_s"] += delta
                    changed = True
        if not changed:
            return sorties
    raise RouteFailure("temporal resolution iteration limit")

