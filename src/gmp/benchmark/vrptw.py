"""Solomon / Gehring–Homberger VRPTW parser and OR-Tools CP-SAT regression."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ortools.constraint_solver import pywrapcp, routing_enums_pb2


@dataclass
class VrptwInstance:
    name: str
    vehicle_count: int
    capacity: int
    customers: list[dict[str, float]]

    @property
    def n(self) -> int:
        return len(self.customers)


def parse_solomon(path: str | Path) -> VrptwInstance:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    name = lines[0].split()[0]
    vehicle_count, capacity = 25, 200
    customers: list[dict[str, float]] = []
    mode = "header"
    for ln in lines:
        low = ln.lower()
        if "vehicle" in low and "number" not in low:
            mode = "vehicle_hdr"
            continue
        if mode == "vehicle_hdr":
            if "number" in low:
                mode = "vehicle"
                continue
        if mode == "vehicle":
            parts = ln.split()
            if len(parts) >= 2 and parts[0].replace(".", "", 1).isdigit():
                vehicle_count, capacity = int(float(parts[0])), int(float(parts[1]))
                mode = "body"
            continue
        if "cust" in low and "xcoord" in low.replace(" ", ""):
            mode = "cust"
            continue
        if mode == "cust":
            parts = ln.split()
            if len(parts) >= 7 and parts[0].lstrip("-").isdigit():
                customers.append(
                    {
                        "id": int(parts[0]),
                        "x": float(parts[1]),
                        "y": float(parts[2]),
                        "demand": float(parts[3]),
                        "ready": float(parts[4]),
                        "due": float(parts[5]),
                        "service": float(parts[6]),
                    }
                )
    if not customers:
        raise ValueError(f"no customers parsed from {path}")
    return VrptwInstance(name=name, vehicle_count=vehicle_count, capacity=capacity, customers=customers)


def _dist(a: dict[str, float], b: dict[str, float]) -> int:
    return int(round(((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5))


def solve_vrptw(inst: VrptwInstance, time_limit_s: float = 5.0) -> dict[str, Any]:
    n = inst.n
    depot = 0
    dist = [[_dist(inst.customers[i], inst.customers[j]) for j in range(n)] for i in range(n)]
    manager = pywrapcp.RoutingIndexManager(n, inst.vehicle_count, depot)
    routing = pywrapcp.RoutingModel(manager)

    def distance_cb(from_index, to_index):
        a = manager.IndexToNode(from_index)
        b = manager.IndexToNode(to_index)
        return dist[a][b]

    t_idx = routing.RegisterTransitCallback(distance_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(t_idx)

    def demand_cb(from_index):
        node = manager.IndexToNode(from_index)
        return int(inst.customers[node]["demand"])

    d_idx = routing.RegisterUnaryTransitCallback(demand_cb)
    routing.AddDimensionWithVehicleCapacity(d_idx, 0, [inst.capacity] * inst.vehicle_count, True, "Cap")

    def time_cb(from_index, to_index):
        a = manager.IndexToNode(from_index)
        b = manager.IndexToNode(to_index)
        return dist[a][b] + int(inst.customers[a]["service"])

    time_idx = routing.RegisterTransitCallback(time_cb)
    horizon = int(max(c["due"] for c in inst.customers) + 10)
    routing.AddDimension(time_idx, horizon, horizon, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")
    for i, c in enumerate(inst.customers):
        index = manager.NodeToIndex(i)
        time_dim.CumulVar(index).SetRange(int(c["ready"]), int(c["due"]))

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromMilliseconds(int(time_limit_s * 1000))
    solution = routing.SolveWithParameters(params)
    if solution is None:
        return {"name": inst.name, "feasible": False, "vehicles_used": 0, "distance": None, "customers": n}
    used = 0
    total = 0
    for v in range(inst.vehicle_count):
        idx = routing.Start(v)
        empty = True
        while not routing.IsEnd(idx):
            nxt = solution.Value(routing.NextVar(idx))
            total += routing.GetArcCostForVehicle(idx, nxt, v)
            if not routing.IsEnd(nxt) and manager.IndexToNode(nxt) != depot:
                empty = False
            idx = nxt
        if not empty:
            used += 1
    return {
        "name": inst.name,
        "feasible": True,
        "vehicles_used": used,
        "distance": int(total),
        "customers": n,
        "capacity": inst.capacity,
        "vehicle_limit": inst.vehicle_count,
    }
