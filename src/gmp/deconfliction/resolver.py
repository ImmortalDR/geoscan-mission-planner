"""Conflict resolution ladder (TS §15): cheapest change first.

1. shift start;
2. wait/hold (rotary wing only);
3. altitude separation on transit;
4. reorder tasks inside a sortie;
5. swap/relocate tasks between UAVs;
6. local reroute (lateral offset of the transit altitude band);
7. re-optimisation (LNS) of the conflicting subset.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable

from ..energy.scheduler import MissionScheduler, ScheduleOptions
from ..models import Assignment, Plan, Scene
from .detector import Conflict, detect_conflicts

MAX_ROUNDS = 36
SHIFT_STEP_S = 180.0
MAX_SHIFT_S = 8 * 3600.0


@dataclass
class DeconflictionResult:
    plan: Plan
    actions: list[dict[str, Any]] = field(default_factory=list)
    initial_conflicts: list[dict[str, Any]] = field(default_factory=list)
    final_conflicts: list[dict[str, Any]] = field(default_factory=list)
    rounds: int = 0
    stats: dict[str, Any] = field(default_factory=dict)
    options: ScheduleOptions | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "initial_conflict_count": len(self.initial_conflicts),
            "final_conflict_count": len(self.final_conflicts),
            "rounds": self.rounds,
            "actions": self.actions,
            "initial_conflicts": self.initial_conflicts[:20],
            "final_conflicts": self.final_conflicts[:20],
            "ladder_used": sorted({a["rung"] for a in self.actions}),
            "detector": self.stats,
        }


def _later_sortie(plan: Plan, conflict: Conflict) -> tuple[str, str]:
    """Return (uav_id, sortie_id) of the sortie that started later."""
    a = next((s for s in plan.sorties if s.id == conflict.sortie_a), None)
    b = next((s for s in plan.sorties if s.id == conflict.sortie_b), None)
    if a is None or b is None or a.t_start is None or b.t_start is None:
        return conflict.uav_b, conflict.sortie_b
    return (a.uav_id, a.id) if a.t_start > b.t_start else (b.uav_id, b.id)


def resolve(
    scene: Scene,
    scheduler: MissionScheduler,
    assignment: Assignment,
    tasks: dict,
    objective: str,
    options: ScheduleOptions | None = None,
    reoptimize: Callable[[Assignment, list[Conflict]], Assignment] | None = None,
    step_s: float = 5.0,
) -> DeconflictionResult:
    """Iteratively remove 4D conflicts, escalating through the ladder."""
    options = copy.deepcopy(options) if options else ScheduleOptions()
    assignment = assignment.copy()
    plan = scheduler.schedule(assignment, tasks, objective=objective, options=options)
    conflicts, stats = detect_conflicts(scene, plan, step_s=step_s)
    initial = [c.as_dict() for c in conflicts]
    actions: list[dict[str, Any]] = []
    rounds = 0
    tried_rungs: dict[tuple[str, str], set[int]] = {}

    while conflicts and rounds < MAX_ROUNDS:
        rounds += 1
        conflict = max(conflicts, key=lambda c: (c.required_h_m - c.min_h_m, c.duration_s))
        uav_id, sortie_id = _later_sortie(plan, conflict)
        uav = scene.uav(uav_id)
        key = (conflict.sortie_a, conflict.sortie_b)
        used = tried_rungs.setdefault(key, set())

        applied: dict[str, Any] | None = None

        # Rung 1: shift the start of the later sortie's UAV.
        if 1 not in used:
            cur = options.start_offsets_s.get(uav_id, 0.0)
            new = cur + max(SHIFT_STEP_S, conflict.duration_s + 60.0)
            if new <= MAX_SHIFT_S:
                options.start_offsets_s[uav_id] = new
                applied = {
                    "rung": 1,
                    "action": "shift_start",
                    "uav": uav_id,
                    "sortie": sortie_id,
                    "offset_s": round(new, 1),
                }
            else:
                used.add(1)

        # Rung 2: hold before the conflicting sortie (rotary wing may loiter).
        if applied is None and 2 not in used and uav is not None and not uav.is_fixed_wing:
            cur = options.hold_before_sortie_s.get(sortie_id, 0.0)
            options.hold_before_sortie_s[sortie_id] = cur + max(120.0, conflict.duration_s + 60.0)
            used.add(2)
            applied = {
                "rung": 2,
                "action": "hold_before_sortie",
                "uav": uav_id,
                "sortie": sortie_id,
                "hold_s": round(options.hold_before_sortie_s[sortie_id], 1),
            }

        # Rung 3: vertical separation of the transit band.
        if applied is None and 3 not in used and uav is not None:
            step = max(uav.vertical_separation_m + 10.0, 60.0)
            cur = options.altitude_offsets_m.get(uav_id, 0.0)
            options.altitude_offsets_m[uav_id] = cur + step
            used.add(3)
            applied = {
                "rung": 3,
                "action": "altitude_separation_transit",
                "uav": uav_id,
                "offset_m": round(options.altitude_offsets_m[uav_id], 1),
            }

        # Rung 4: reverse the task order inside the later sortie.
        if applied is None and 4 not in used:
            for trips in assignment.per_uav.get(uav_id, []):
                pass
            trips = assignment.per_uav.get(uav_id, [])
            idx = None
            for k, trip in enumerate(trips):
                if f"{uav_id}_S{k + 1}" == sortie_id:
                    idx = k
                    break
            if idx is not None and len(trips[idx]) > 1:
                trips[idx] = list(reversed(trips[idx]))
                used.add(4)
                applied = {
                    "rung": 4,
                    "action": "reorder_tasks",
                    "uav": uav_id,
                    "sortie": sortie_id,
                }
            else:
                used.add(4)

        # Rung 5: relocate one task of the later sortie to another eligible UAV.
        if applied is None and 5 not in used:
            moved = _relocate_task(scene, assignment, uav_id, sortie_id, tasks)
            used.add(5)
            if moved:
                applied = {"rung": 5, "action": "relocate_task", **moved}

        # Rung 6: widen the altitude band further (local reroute proxy).
        if applied is None and 6 not in used and uav is not None:
            options.altitude_offsets_m[uav_id] = (
                options.altitude_offsets_m.get(uav_id, 0.0) + uav.vertical_separation_m + 30.0
            )
            used.add(6)
            applied = {
                "rung": 6,
                "action": "local_reroute_altitude_band",
                "uav": uav_id,
                "offset_m": round(options.altitude_offsets_m[uav_id], 1),
            }

        # Rung 7: re-optimise the conflicting subset.
        if applied is None and 7 not in used and reoptimize is not None:
            assignment = reoptimize(assignment, conflicts)
            used.add(7)
            applied = {"rung": 7, "action": "lns_reoptimisation", "uavs": [conflict.uav_a, conflict.uav_b]}

        if applied is None:
            break

        actions.append(applied)
        plan = scheduler.schedule(assignment, tasks, objective=objective, options=options)
        conflicts, stats = detect_conflicts(scene, plan, step_s=step_s)

    if conflicts:
        staggered = _stagger_uavs(plan, options)
        if staggered:
            plan = scheduler.schedule(assignment, tasks, objective=objective, options=options)
            conflicts, stats = detect_conflicts(scene, plan, step_s=step_s)
            actions.append(
                {
                    "rung": 1,
                    "action": "sequentialise_uavs",
                    "offsets_s": dict(options.start_offsets_s),
                }
            )
            rounds += 1

    result = DeconflictionResult(
        plan=plan,
        actions=actions,
        initial_conflicts=initial,
        final_conflicts=[c.as_dict() for c in conflicts],
        rounds=rounds,
        stats=stats,
        options=options,
    )
    plan.deconfliction = result.as_dict()
    return result


def _stagger_uavs(plan: Plan, options: ScheduleOptions) -> bool:
    """Last-resort deconfliction: fly UAVs one after another."""
    groups: dict[str, list] = {}
    for s in plan.sorties:
        groups.setdefault(s.uav_id, []).append(s)
    if len(groups) < 2:
        return False
    order = sorted(groups, key=lambda u: min(s.t_start or plan.created_at for s in groups[u]))
    cursor = 0.0
    changed = False
    for uid in order:
        ss = groups[uid]
        starts = [s.t_start for s in ss if s.t_start]
        ends = [s.t_end for s in ss if s.t_end]
        if not starts or not ends:
            continue
        span = (max(ends) - min(starts)).total_seconds()
        if options.start_offsets_s.get(uid, 0.0) < cursor - 1.0:
            options.start_offsets_s[uid] = cursor
            changed = True
        else:
            options.start_offsets_s[uid] = max(options.start_offsets_s.get(uid, 0.0), cursor)
            changed = True
        cursor = options.start_offsets_s[uid] + span + 90.0
    return changed


def _relocate_task(
    scene: Scene, assignment: Assignment, uav_id: str, sortie_id: str, tasks: dict
) -> dict[str, Any] | None:
    trips = assignment.per_uav.get(uav_id, [])
    idx = None
    for k in range(len(trips)):
        if f"{uav_id}_S{k + 1}" == sortie_id:
            idx = k
            break
    if idx is None or not trips[idx]:
        return None
    task_id = trips[idx][-1]
    task = tasks.get(task_id)
    if task is None:
        return None
    for other_id, other_trips in assignment.per_uav.items():
        if other_id == uav_id:
            continue
        other = scene.uav(other_id)
        if other is None or not other.supports(task.payload_class):
            continue
        if scene.mission.wind.speed_ms > other.max_wind_ms:
            continue
        trips[idx].pop()
        if not other_trips:
            other_trips.append([task_id])
        else:
            other_trips[-1].append(task_id)
        if not trips[idx]:
            trips.pop(idx)
        return {"task": task_id, "from_uav": uav_id, "to_uav": other_id}
    return None
