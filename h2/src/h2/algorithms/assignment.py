"""CP-SAT workload assignment warm start, followed by full route validation."""
import math
import time
from ..scheduler import RouteFailure

def _cp_assignment(scheduler, seconds, seed, deadline):
    """CP-SAT warm start uses workload lower bounds; full route checks follow.

    No claim that this surrogate solves the complete routing problem optimally.
    """
    from ortools.sat.python import cp_model
    model = cp_model.CpModel()
    variables, costs = {}, {}
    for tid in scheduler.tasks:
        if time.monotonic() >= deadline:
            return None
        eligible = scheduler.eligible[tid]
        if not eligible:
            return None
        options = []
        for uid in eligible:
            if time.monotonic() >= deadline:
                return None
            try:
                s = scheduler.schedule_uav(uid, [tid])
            except RouteFailure:
                continue
            v = model.NewBoolVar(f"{uid}:{tid}")
            variables[uid, tid] = v
            costs[uid, tid] = max(1, math.ceil(sum(r["flight_time_s"] for r in s)))
            options.append(v)
        if not options:
            return None
        model.Add(sum(options) == 1)
    loads = []
    upper = sum(costs.values())
    for uid in scheduler.fleet:
        load = model.NewIntVar(0, upper, "load_"+uid)
        model.Add(load == sum(costs[key]*v for key, v in variables.items() if key[0] == uid))
        loads.append(load)
    if scheduler.settings.objective == "makespan":
        span = model.NewIntVar(0, upper, "max_load")
        model.AddMaxEquality(span, loads)
        model.Minimize(span)
    else:
        model.Minimize(sum(loads))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(.001, min(seconds, deadline-time.monotonic()))
    solver.parameters.random_seed = seed
    solver.parameters.num_search_workers = 1
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    assignment = {uid: [] for uid in scheduler.fleet}
    for (uid, tid), v in variables.items():
        if solver.Value(v):
            assignment[uid].append(tid)
    return assignment

