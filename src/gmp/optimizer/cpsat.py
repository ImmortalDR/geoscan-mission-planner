"""CP-SAT assignment/balancing model (OR-Tools).

CP-SAT solves the hard combinatorial part - which physical UAV takes which
atomic task, and how many sorties that implies - on a time-aggregated model.
Intra-UAV sequencing and exact 4D timing are then produced by the routing and
scheduling layers, and the result is polished by LNS.
"""

from __future__ import annotations

import math
from typing import Any

from ortools.sat.python import cp_model

from ..models import Assignment
from .core import PlanningContext

#: Fraction of the depot approach time charged to a single task in the
#: aggregated model (tasks share transit inside a sortie).
APPROACH_SHARE = 0.30


def solve_assignment(
    pc: PlanningContext,
    objective: str = "makespan",
    time_limit_s: float = 20.0,
    workers: int = 2,
    warm_start: Assignment | None = None,
) -> tuple[Assignment | None, dict[str, Any]]:
    """Return (assignment, solver info). ``None`` if no solution was found."""
    if not pc.fleet or not pc.task_ids:
        return None, {"status": "EMPTY"}

    model = cp_model.CpModel()
    x: dict[tuple[str, str], Any] = {}
    cost: dict[tuple[str, str], int] = {}

    for tid in pc.task_ids:
        cands = pc.eligible[tid]
        if not cands:
            continue
        centroid = pc.task_centroid(tid)
        for uid in cands:
            uav = pc.uav(uid)
            approach = math.dist(pc.depot[uid].xy, centroid) / max(uav.ground_speed_ms, 1.0)
            c = pc.survey_time(uid, tid) + APPROACH_SHARE * 2.0 * approach
            cost[(tid, uid)] = int(round(c))
            x[(tid, uid)] = model.NewBoolVar(f"x_{tid}_{uid}")
        model.AddExactlyOne(x[(tid, uid)] for uid in cands)

    horizon = int(max(pc.window_s * 4, 3600))
    completion: dict[str, Any] = {}
    loads: dict[str, Any] = {}
    for uav in pc.fleet:
        uid = uav.id
        my_tasks = [t for t in pc.task_ids if (t, uid) in x]
        load = model.NewIntVar(0, horizon, f"load_{uid}")
        model.Add(load == sum(cost[(t, uid)] * x[(t, uid)] for t in my_tasks)) if my_tasks else model.Add(load == 0)
        usable = max(int(uav.usable_endurance_s), 1)
        extra = model.NewIntVar(0, 50, f"extra_{uid}")
        # load <= usable * (extra + 1)  ->  at least extra+1 sorties are needed
        model.Add(load <= usable * (extra + 1))
        comp = model.NewIntVar(0, horizon, f"comp_{uid}")
        model.Add(comp == load + int(uav.service_time_s) * extra)
        loads[uid] = load
        completion[uid] = comp

    info: dict[str, Any] = {"objective_kind": objective}
    if objective == "total_flight":
        total = model.NewIntVar(0, horizon * len(pc.fleet), "total")
        model.Add(total == sum(loads.values()))
        for uid, comp in completion.items():
            model.Add(comp <= int(pc.window_s))
        model.Minimize(total)
    else:
        makespan = model.NewIntVar(0, horizon, "makespan")
        model.AddMaxEquality(makespan, list(completion.values()))
        model.Minimize(makespan)

    if warm_start is not None:
        hints = []
        for uid, trips in warm_start.per_uav.items():
            for trip in trips:
                for tid in trip:
                    if (tid, uid) in x:
                        hints.append((x[(tid, uid)], 1))
        for var, val in hints:
            model.AddHint(var, val)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_s)
    solver.parameters.num_search_workers = max(1, int(workers))
    solver.parameters.log_search_progress = False
    status = solver.Solve(model)
    info.update(
        {
            "status": solver.StatusName(status),
            "objective_value": solver.ObjectiveValue() if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
            "best_bound": solver.BestObjectiveBound() if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
            "wall_time_s": round(solver.WallTime(), 2),
            "task_count": len(pc.task_ids),
            "uav_count": len(pc.fleet),
        }
    )
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, info

    sets: dict[str, list[str]] = {u.id: [] for u in pc.fleet}
    for (tid, uid), var in x.items():
        if solver.Value(var):
            sets[uid].append(tid)
    return pc.assignment_from_sets(sets), info
