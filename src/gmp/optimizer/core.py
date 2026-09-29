"""Shared machinery for the global optimizer.

The optimisation problem is a heterogeneous multi-depot multi-trip
coverage-routing problem with scheduling. This module holds the pieces every
solver (baselines, CP-SAT, LNS) needs: eligibility, fast cost evaluation,
intra-UAV routing and the trip (sortie) splitting rule.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ..energy.model import ResourceContext
from ..models import Assignment, AtomicTask, Scene, Site, Uav
from ..transit import RouterBank

BIG = 1e12


@dataclass
class EvalResult:
    objective: float
    makespan_s: float
    total_flight_s: float
    unassigned: int
    infeasible_sorties: int
    per_uav_completion_s: dict[str, float] = field(default_factory=dict)
    per_uav_trips: dict[str, list[float]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": round(self.objective, 1),
            "makespan_s": round(self.makespan_s, 1),
            "total_flight_s": round(self.total_flight_s, 1),
            "unassigned": self.unassigned,
            "infeasible_sorties": self.infeasible_sorties,
        }


class PlanningContext:
    """Pre-computed problem data plus fast (schedule-free) cost evaluation."""

    def __init__(
        self,
        scene: Scene,
        tasks: dict[str, AtomicTask],
        ctx: ResourceContext,
        routers: RouterBank,
    ):
        self.scene = scene
        self.tasks = tasks
        self.ctx = ctx
        self.routers = routers
        self.task_ids = list(tasks)
        self.wind = scene.mission.wind

        self.fleet = [u for u in scene.fleet if self.wind.speed_ms <= u.max_wind_ms]
        self.wind_excluded = [
            u.id for u in scene.fleet if self.wind.speed_ms > u.max_wind_ms
        ]
        self.depot: dict[str, Site] = {}
        for uav in self.fleet:
            site = scene.site(uav.start_site) if uav.start_site else None
            if site is None or not site.can_start:
                starts = [s for s in scene.sites if s.can_start and not s.candidate]
                site = starts[0] if starts else (scene.sites[0] if scene.sites else None)
            if site is not None:
                self.depot[uav.id] = site

        self.eligible: dict[str, list[str]] = {}
        for tid, task in tasks.items():
            cands = [u.id for u in self.fleet if u.supports(task.payload_class)]
            if task.fixed_wing_safe is False:
                rotary = [u for u in cands if not (scene.uav(u) and scene.uav(u).is_fixed_wing)]
                if rotary:
                    cands = rotary
            self.eligible[tid] = cands
        self.uav_tasks: dict[str, list[str]] = {
            u.id: [t for t in self.task_ids if u.id in self.eligible[t]] for u in self.fleet
        }
        self._router = {u.id: routers.for_altitude(self._uav_agl(u)) for u in self.fleet}
        self._dist_cache: dict[tuple, float] = {}
        self.window_s = (
            scene.mission.effective_end() - scene.mission.effective_start()
        ).total_seconds()

    # ------------------------------------------------------------------ #
    def _uav_agl(self, uav: Uav) -> float:
        agls = [t.agl_m for t in self.tasks.values() if uav.supports(t.payload_class)]
        return max(agls) if agls else uav.cruise_agl_m

    def dist(self, uav_id: str, a: tuple[float, float], b: tuple[float, float]) -> float:
        key = (uav_id, round(a[0], 1), round(a[1], 1), round(b[0], 1), round(b[1], 1))
        v = self._dist_cache.get(key)
        if v is None:
            v = self._router[uav_id].length(a, b)
            self._dist_cache[key] = v
        return v

    def uav(self, uav_id: str) -> Uav:
        u = self.scene.uav(uav_id)
        assert u is not None
        return u

    def task(self, task_id: str) -> AtomicTask:
        return self.tasks[task_id]

    def survey_time(self, uav_id: str, task_id: str) -> float:
        return self.ctx.task_survey_time_s(self.uav(uav_id), self.tasks[task_id])

    # ------------------------------------------------------------------ #
    def trip_cost(self, uav_id: str, trip: Sequence[str]) -> tuple[float, bool]:
        """Flight time of a trip and whether it fits the usable endurance."""
        if not trip:
            return 0.0, True
        uav = self.uav(uav_id)
        depot = self.depot[uav_id].xy
        total = uav.takeoff_time_s + uav.landing_time_s
        prev = depot
        last_exit = depot
        for tid in trip:
            task = self.tasks[tid]
            entry, exit_ = self._oriented(uav_id, task, prev)
            total += self.dist(uav_id, prev, entry) / max(uav.ground_speed_ms, 0.1)
            total += self.survey_time(uav_id, tid)
            prev = exit_
            last_exit = exit_
        home = self._landing_xy(uav_id, last_exit)
        total += self.dist(uav_id, prev, home) / max(uav.ground_speed_ms, 0.1)
        return total, total <= uav.usable_endurance_s + 1e-6

    def _landing_xy(self, uav_id: str, last_xy: tuple[float, float]) -> tuple[float, float]:
        uav = self.uav(uav_id)
        if uav.landing_site:
            site = self.scene.site(uav.landing_site)
            if site is not None and site.can_land:
                return site.xy
        if self.scene.mission.allow_different_start_end:
            landable = [s for s in self.scene.sites if s.can_land and not s.candidate and not s.is_reserve]
            if landable:
                return min(landable, key=lambda s: math.dist(last_xy, s.xy)).xy
        return self.depot[uav_id].xy

    def _oriented(
        self, uav_id: str, task: AtomicTask, prev: tuple[float, float]
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        f_entry, f_exit = task.endpoints(reverse=False)
        r_entry, r_exit = task.endpoints(reverse=True)
        if math.dist(prev, r_entry) < math.dist(prev, f_entry):
            return r_entry, r_exit
        return f_entry, f_exit

    def split_into_trips(self, uav_id: str, ordered: Sequence[str]) -> list[list[str]]:
        """Greedy multi-trip split honouring the per-sortie endurance limit."""
        trips: list[list[str]] = []
        cur: list[str] = []
        for tid in ordered:
            trial = cur + [tid]
            _, ok = self.trip_cost(uav_id, trial)
            if ok:
                cur = trial
                continue
            if cur:
                trips.append(cur)
            _, alone_ok = self.trip_cost(uav_id, [tid])
            if alone_ok:
                cur = [tid]
            else:
                # Task cannot be flown by this UAV at all.
                cur = []
                trips.append([tid])  # kept, marked infeasible by the evaluator
        if cur:
            trips.append(cur)
        return trips

    def route_uav(self, uav_id: str, task_set: Iterable[str], polish: bool = True) -> list[str]:
        """Nearest-neighbour ordering from the depot + optional 2-opt polishing."""
        remaining = list(task_set)
        if len(remaining) <= 2:
            return remaining
        depot = self.depot[uav_id].xy
        order: list[str] = []
        prev = depot
        pool = set(remaining)
        while pool:
            best, best_d = None, math.inf
            for tid in pool:
                task = self.tasks[tid]
                e1, _ = task.endpoints(False)
                e2, _ = task.endpoints(True)
                d = min(math.dist(prev, e1), math.dist(prev, e2))
                if d < best_d:
                    best, best_d = tid, d
            order.append(best)
            task = self.tasks[best]
            _, ex = self._oriented(uav_id, task, prev)
            prev = ex
            pool.discard(best)
        return self.two_opt(uav_id, order) if polish else order

    def _order_length(self, uav_id: str, order: Sequence[str]) -> float:
        depot = self.depot[uav_id].xy
        prev = depot
        total = 0.0
        for tid in order:
            task = self.tasks[tid]
            entry, exit_ = self._oriented(uav_id, task, prev)
            total += math.dist(prev, entry)
            prev = exit_
        total += math.dist(prev, depot)
        return total

    def two_opt(self, uav_id: str, order: list[str], max_rounds: int = 3) -> list[str]:
        if len(order) < 4:
            return order
        best = list(order)
        best_len = self._order_length(uav_id, best)
        n = len(best)
        for _ in range(max_rounds):
            improved = False
            for i in range(n - 1):
                for j in range(i + 2, min(i + 12, n)):
                    cand = best[:i] + list(reversed(best[i : j + 1])) + best[j + 1 :]
                    cl = self._order_length(uav_id, cand)
                    if cl < best_len - 1.0:
                        best, best_len, improved = cand, cl, True
            if not improved:
                break
        return best

    # ------------------------------------------------------------------ #
    def evaluate(self, assignment: Assignment, objective: str = "makespan") -> EvalResult:
        assigned = set()
        total_flight = 0.0
        makespan = 0.0
        infeasible = 0
        per_uav: dict[str, float] = {}
        per_trips: dict[str, list[float]] = {}
        for uav_id, trips in assignment.per_uav.items():
            uav = self.uav(uav_id)
            completion = 0.0
            costs: list[float] = []
            n = 0
            for trip in trips:
                if not trip:
                    continue
                cost, ok = self.trip_cost(uav_id, trip)
                if not ok:
                    infeasible += 1
                total_flight += cost
                costs.append(cost)
                completion += cost
                n += 1
                assigned.update(trip)
            if n > 1:
                completion += (n - 1) * uav.service_time_s
            per_uav[uav_id] = completion
            per_trips[uav_id] = costs
            makespan = max(makespan, completion)

        unassigned = len(self.task_ids) - len(assigned)
        if objective == "total_flight":
            base = total_flight
        else:
            base = makespan
        penalty = unassigned * BIG / 1e6 + infeasible * 1e6
        over_window = max(0.0, makespan - self.window_s) * 1e3
        return EvalResult(
            objective=base + penalty + over_window,
            makespan_s=makespan,
            total_flight_s=total_flight,
            unassigned=unassigned,
            infeasible_sorties=infeasible,
            per_uav_completion_s=per_uav,
            per_uav_trips=per_trips,
        )

    # ------------------------------------------------------------------ #
    def assignment_from_sets(self, sets: dict[str, list[str]], polish: bool = True) -> Assignment:
        """Route + split per-UAV task sets into a full assignment."""
        per_uav: dict[str, list[list[str]]] = {}
        for uav_id, task_set in sets.items():
            if not task_set:
                per_uav[uav_id] = []
                continue
            order = self.route_uav(uav_id, task_set, polish=polish)
            per_uav[uav_id] = self.split_into_trips(uav_id, order)
        return Assignment(per_uav=per_uav)

    @staticmethod
    def sets_from_assignment(assignment: Assignment) -> dict[str, list[str]]:
        return {
            uav_id: [t for trip in trips for t in trip]
            for uav_id, trips in assignment.per_uav.items()
        }

    def task_centroid(self, task_id: str) -> tuple[float, float]:
        c = self.tasks[task_id].geom.centroid
        return (c.x, c.y)

    def nearest_tasks(self, task_id: str, k: int = 12) -> list[str]:
        c0 = self.task_centroid(task_id)
        scored = [
            (math.dist(c0, self.task_centroid(t)), t)
            for t in self.task_ids
            if t != task_id
        ]
        scored.sort()
        return [t for _, t in scored[:k]]
