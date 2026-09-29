"""Warm-start baselines and benchmark reference algorithms (TS §8, §26).

Each baseline produces a complete assignment so it can be both a warm start for
the global optimizer and a benchmark competitor in ``benchmark mode``.
"""

from __future__ import annotations

import math
import random
from typing import Any, Callable

from ..models import Assignment
from .core import PlanningContext


def _empty_sets(pc: PlanningContext) -> dict[str, list[str]]:
    return {u.id: [] for u in pc.fleet}


def equal_area(pc: PlanningContext) -> Assignment:
    """Split each job's tasks between eligible UAVs in equal survey length."""
    sets = _empty_sets(pc)
    by_job: dict[str, list[str]] = {}
    for tid, task in pc.tasks.items():
        by_job.setdefault(task.job_id, []).append(tid)
    for job_id, tids in by_job.items():
        tids.sort()
        cands = sorted({u for t in tids for u in pc.eligible[t]})
        if not cands:
            continue
        total = sum(pc.tasks[t].survey_length_m for t in tids)
        share = total / len(cands)
        idx, acc = 0, 0.0
        for t in tids:
            uav_id = cands[min(idx, len(cands) - 1)]
            if uav_id not in pc.eligible[t]:
                uav_id = pc.eligible[t][0]
            sets[uav_id].append(t)
            acc += pc.tasks[t].survey_length_m
            if acc >= share * (idx + 1) and idx < len(cands) - 1:
                idx += 1
    return pc.assignment_from_sets(sets)


def nearest_depot(pc: PlanningContext) -> Assignment:
    """Every task goes to the eligible UAV whose depot is closest."""
    sets = _empty_sets(pc)
    for tid in pc.task_ids:
        cands = pc.eligible[tid]
        if not cands:
            continue
        c = pc.task_centroid(tid)
        best = min(cands, key=lambda u: math.dist(pc.depot[u].xy, c))
        sets[best].append(tid)
    return pc.assignment_from_sets(sets)


def greedy_load_balanced(pc: PlanningContext, objective: str = "makespan") -> Assignment:
    """Greedy: longest task first into the currently least loaded eligible UAV."""
    sets = _empty_sets(pc)
    load: dict[str, float] = {u.id: 0.0 for u in pc.fleet}
    order = sorted(pc.task_ids, key=lambda t: -pc.tasks[t].survey_length_m)
    for tid in order:
        cands = pc.eligible[tid]
        if not cands:
            continue
        c = pc.task_centroid(tid)

        def key(u: str) -> float:
            approach = math.dist(pc.depot[u].xy, c) / max(pc.uav(u).ground_speed_ms, 1.0)
            return load[u] + pc.survey_time(u, tid) + 0.25 * approach

        best = min(cands, key=key)
        sets[best].append(tid)
        load[best] += pc.survey_time(best, tid)
    return pc.assignment_from_sets(sets)


def balanced_clustering(pc: PlanningContext, iterations: int = 12, seed: int = 20260918) -> Assignment:
    """k-means style clustering of tasks around UAV depots with load balancing."""
    rng = random.Random(seed)
    sets = _empty_sets(pc)
    if not pc.fleet:
        return Assignment(per_uav={})
    centres = {u.id: pc.depot[u.id].xy for u in pc.fleet}
    assign: dict[str, str] = {}
    for _ in range(iterations):
        load = {u.id: 0.0 for u in pc.fleet}
        assign = {}
        for tid in sorted(pc.task_ids, key=lambda t: -pc.tasks[t].survey_length_m):
            cands = pc.eligible[tid]
            if not cands:
                continue
            c = pc.task_centroid(tid)
            cap = {u: pc.uav(u).usable_endurance_s for u in cands}
            best = min(
                cands,
                key=lambda u: math.dist(centres[u], c) / 1000.0
                + 3.0 * load[u] / max(cap[u], 1.0),
            )
            assign[tid] = best
            load[best] += pc.survey_time(best, tid)
        new_centres = {}
        for u in pc.fleet:
            pts = [pc.task_centroid(t) for t, a in assign.items() if a == u.id]
            if pts:
                new_centres[u.id] = (
                    sum(p[0] for p in pts) / len(pts),
                    sum(p[1] for p in pts) / len(pts),
                )
            else:
                new_centres[u.id] = centres[u.id]
        moved = max(math.dist(centres[u], new_centres[u]) for u in centres)
        centres = new_centres
        if moved < 25.0:
            break
    for tid, u in assign.items():
        sets[u].append(tid)
    return pc.assignment_from_sets(sets)


def darp_like(pc: PlanningContext) -> Assignment:
    """DARP-flavoured sequential insertion: sweep tasks by polar angle."""
    sets = _empty_sets(pc)
    if not pc.fleet:
        return Assignment(per_uav={})
    cx = sum(pc.task_centroid(t)[0] for t in pc.task_ids) / max(len(pc.task_ids), 1)
    cy = sum(pc.task_centroid(t)[1] for t in pc.task_ids) / max(len(pc.task_ids), 1)
    order = sorted(
        pc.task_ids,
        key=lambda t: math.atan2(pc.task_centroid(t)[1] - cy, pc.task_centroid(t)[0] - cx),
    )
    load = {u.id: 0.0 for u in pc.fleet}
    for tid in order:
        cands = pc.eligible[tid]
        if not cands:
            continue
        cap = {u: pc.uav(u).usable_endurance_s for u in cands}
        best = min(cands, key=lambda u: load[u] / max(cap[u], 1.0))
        sets[best].append(tid)
        load[best] += pc.survey_time(best, tid)
    return pc.assignment_from_sets(sets)


BASELINES: dict[str, Callable[[PlanningContext], Assignment]] = {
    "equal_area": equal_area,
    "nearest_depot": nearest_depot,
    "greedy": greedy_load_balanced,
    "balanced_clustering": balanced_clustering,
    "darp_like": darp_like,
}


def build_all(pc: PlanningContext, objective: str = "makespan") -> dict[str, Assignment]:
    out: dict[str, Assignment] = {}
    for name, fn in BASELINES.items():
        try:
            out[name] = fn(pc)
        except Exception as exc:  # pragma: no cover - a baseline must never break the run
            out[name] = Assignment(per_uav={})
            out[f"{name}__error"] = str(exc)  # type: ignore[assignment]
    return out
