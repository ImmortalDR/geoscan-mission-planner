"""Large Neighbourhood Search over UAV<->task assignments.

Anytime behaviour (TS §21): the first feasible incumbent is kept immediately and
then improved until the time budget expires; every improvement is reported
through the progress callback so the API/WebSocket layer can stream it.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..models import Assignment
from .core import EvalResult, PlanningContext

ProgressCb = Callable[[dict[str, Any]], None]


@dataclass
class LnsResult:
    assignment: Assignment
    evaluation: EvalResult
    history: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    operator_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    elapsed_s: float = 0.0


def _completion(pc: PlanningContext, sets: dict[str, list[str]], uav_id: str) -> float:
    uav = pc.uav(uav_id)
    total = 0.0
    for tid in sets.get(uav_id, []):
        total += pc.survey_time(uav_id, tid)
    return total


class LnsSolver:
    OPERATORS = ("random_removal", "related_removal", "worst_uav_removal", "sortie_removal")

    def __init__(
        self,
        pc: PlanningContext,
        objective: str = "makespan",
        seed: int = 20260918,
        progress_cb: ProgressCb | None = None,
    ):
        self.pc = pc
        self.objective = objective
        self.rng = random.Random(seed)
        self.progress_cb = progress_cb
        self.stats: dict[str, dict[str, int]] = {
            op: {"tried": 0, "improved": 0} for op in self.OPERATORS
        }

    # ------------------------------------------------------------------ #
    def _destroy(self, sets: dict[str, list[str]], k: int) -> tuple[dict[str, list[str]], list[str], str]:
        op = self.rng.choice(self.OPERATORS)
        sets = {u: list(v) for u, v in sets.items()}
        removed: list[str] = []
        if op == "random_removal":
            pool = [t for v in sets.values() for t in v]
            self.rng.shuffle(pool)
            removed = pool[:k]
        elif op == "related_removal":
            pool = [t for v in sets.values() for t in v]
            if pool:
                seed_task = self.rng.choice(pool)
                removed = [seed_task] + self.pc.nearest_tasks(seed_task, k - 1)
                removed = [t for t in removed if any(t in v for v in sets.values())]
        elif op == "worst_uav_removal":
            loads = {u: _completion(self.pc, sets, u) for u in sets if sets[u]}
            if loads:
                worst = max(loads, key=lambda u: loads[u])
                pool = list(sets[worst])
                self.rng.shuffle(pool)
                removed = pool[: max(k, len(pool) // 3)]
        else:  # sortie_removal
            uav_ids = [u for u in sets if sets[u]]
            if uav_ids:
                uid = self.rng.choice(uav_ids)
                pool = list(sets[uid])
                start = self.rng.randrange(len(pool))
                removed = pool[start : start + k]
        removed_set = set(removed)
        for u in sets:
            sets[u] = [t for t in sets[u] if t not in removed_set]
        return sets, list(removed_set), op

    def _repair(self, sets: dict[str, list[str]], removed: list[str]) -> dict[str, list[str]]:
        order = list(removed)
        self.rng.shuffle(order)
        # Insert the longest tasks first: they constrain the schedule most.
        order.sort(key=lambda t: -self.pc.tasks[t].survey_length_m)
        loads = {u: _completion(self.pc, sets, u) for u in sets}
        for tid in order:
            cands = self.pc.eligible[tid]
            if not cands:
                continue
            centroid = self.pc.task_centroid(tid)
            best, best_score = None, math.inf
            for uid in cands:
                uav = self.pc.uav(uid)
                st = self.pc.survey_time(uid, tid)
                near = min(
                    [math.dist(centroid, self.pc.task_centroid(t)) for t in sets[uid][-12:]]
                    or [math.dist(centroid, self.pc.depot[uid].xy)]
                )
                detour = 2.0 * near / max(uav.ground_speed_ms, 1.0)
                if self.objective == "total_flight":
                    score = st + detour + 0.05 * loads[uid]
                else:
                    score = loads[uid] + st + 0.5 * detour
                score += self.rng.uniform(0.0, 0.05) * max(st, 1.0)
                if score < best_score:
                    best, best_score = uid, score
            if best is None:
                continue
            sets[best].append(tid)
            loads[best] += self.pc.survey_time(best, tid)
        return sets

    # ------------------------------------------------------------------ #
    def solve(
        self,
        start: Assignment,
        time_budget_s: float = 30.0,
        max_iterations: int | None = None,
        report_every_s: float = 2.0,
    ) -> LnsResult:
        pc = self.pc
        t0 = time.time()
        best = start.copy()
        best_eval = pc.evaluate(best, self.objective)
        history = [
            {
                "t_s": 0.0,
                "iteration": 0,
                "source": "warm_start",
                **best_eval.as_dict(),
            }
        ]
        if self.progress_cb:
            self.progress_cb(history[-1])
        cur_sets = pc.sets_from_assignment(best)
        cur_eval = best_eval
        it = 0
        last_report = t0
        n_tasks = max(len(pc.task_ids), 1)

        while time.time() - t0 < time_budget_s:
            if max_iterations is not None and it >= max_iterations:
                break
            it += 1
            k = max(2, min(int(n_tasks * self.rng.uniform(0.04, 0.18)), 40))
            trial_sets, removed, op = self._destroy(cur_sets, k)
            self.stats[op]["tried"] += 1
            if not removed:
                continue
            trial_sets = self._repair(trial_sets, removed)
            trial = pc.assignment_from_sets(trial_sets, polish=False)
            ev = pc.evaluate(trial, self.objective)
            accept = ev.objective < cur_eval.objective - 1e-6
            # Record-to-record travel: mild worsening keeps the search moving.
            if not accept and ev.objective < best_eval.objective * 1.02:
                accept = self.rng.random() < 0.2
            if accept:
                cur_sets = pc.sets_from_assignment(trial)
                cur_eval = ev
                if ev.objective < best_eval.objective - 1e-6:
                    best, best_eval = trial.copy(), ev
                    self.stats[op]["improved"] += 1
                    entry = {
                        "t_s": round(time.time() - t0, 2),
                        "iteration": it,
                        "source": op,
                        **ev.as_dict(),
                    }
                    history.append(entry)
                    if self.progress_cb and time.time() - last_report > report_every_s:
                        self.progress_cb(entry)
                        last_report = time.time()

        # Final polish: re-route every UAV with 2-opt.
        polished = pc.assignment_from_sets(pc.sets_from_assignment(best), polish=True)
        pol_eval = pc.evaluate(polished, self.objective)
        if pol_eval.objective <= best_eval.objective:
            best, best_eval = polished, pol_eval
            history.append(
                {
                    "t_s": round(time.time() - t0, 2),
                    "iteration": it,
                    "source": "two_opt_polish",
                    **pol_eval.as_dict(),
                }
            )
        result = LnsResult(
            assignment=best,
            evaluation=best_eval,
            history=history,
            iterations=it,
            operator_stats=self.stats,
            elapsed_s=round(time.time() - t0, 2),
        )
        if self.progress_cb:
            self.progress_cb({"final": True, **best_eval.as_dict(), "iterations": it})
        return result
