"""Multi-sortie scheduler (TS §9).

Turns an abstract assignment (ordered task lists per UAV sortie) into a
concrete 4D plan: start/landing sites, take-off and landing times, phase-tagged
waypoints with AGL/AMSL altitude, per-sortie resource accounting and a schedule
that respects the mission window, daylight and temporal airspace.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

from shapely.geometry import LineString, Point

from ..geo import densify
from ..models import (
    Assignment,
    AtomicTask,
    Plan,
    Scene,
    Site,
    Sortie,
    Uav,
    Waypoint,
)
from ..transit import RouterBank
from .model import ResourceContext, sortie_cost

#: Along-path sampling step for waypoint generation.
WAYPOINT_STEP_M = 250.0
#: Maximum number of scheduling repair iterations per sortie.
MAX_TEMPORAL_REPAIRS = 4


@dataclass
class ScheduleOptions:
    start_offsets_s: dict[str, float] = field(default_factory=dict)
    altitude_offsets_m: dict[str, float] = field(default_factory=dict)
    hold_before_sortie_s: dict[str, float] = field(default_factory=dict)
    respect_temporal_airspace: bool = True


class MissionScheduler:
    def __init__(self, scene: Scene, ctx: ResourceContext, routers: RouterBank):
        self.scene = scene
        self.ctx = ctx
        self.routers = routers
        self.mission_start = scene.mission.effective_start()
        self.mission_end = scene.mission.effective_end()

    # ------------------------------------------------------------------ #
    def start_site_for(self, uav: Uav) -> Site:
        if uav.start_site:
            s = self.scene.site(uav.start_site)
            if s is not None and s.can_start:
                return s
        starts = [s for s in self.scene.sites if s.can_start and not s.candidate]
        if not starts:
            starts = [s for s in self.scene.sites if not s.candidate] or self.scene.sites
        return starts[0]

    def landing_candidates(self) -> list[Site]:
        out = [s for s in self.scene.sites if s.can_land and not s.candidate]
        return out or [s for s in self.scene.sites if not s.candidate] or self.scene.sites

    def _terrain(self, x: float, y: float) -> float:
        return self.scene.dem.elevation(x, y) if self.scene.dem.available else 0.0

    # ------------------------------------------------------------------ #
    def build_sortie(
        self,
        uav: Uav,
        index: int,
        task_ids: Sequence[str],
        tasks: dict[str, AtomicTask],
        start_site: Site,
        t_start: datetime,
        options: ScheduleOptions,
    ) -> Sortie:
        task_objs = [tasks[t] for t in task_ids]
        agl = max([t.agl_m for t in task_objs], default=uav.cruise_agl_m)
        transit_agl = agl + options.altitude_offsets_m.get(uav.id, 0.0)
        router = self.routers.for_altitude(transit_agl)

        # Orientation of every task (forward/reverse) by greedy chaining.
        reversed_flags: list[bool] = []
        prev = start_site.xy
        for task in task_objs:
            fwd_entry, fwd_exit = task.endpoints(reverse=False)
            rev_entry, rev_exit = task.endpoints(reverse=True)
            if math.dist(prev, rev_entry) < math.dist(prev, fwd_entry):
                reversed_flags.append(True)
                prev = rev_exit
            else:
                reversed_flags.append(False)
                prev = fwd_exit

        landing = self._pick_landing_site(uav, prev, start_site, router)
        cost = sortie_cost(
            self.ctx,
            uav,
            task_objs,
            reversed_flags,
            start_site.xy,
            landing.xy,
            router.length,
        )

        sortie = Sortie(
            uav_id=uav.id,
            index=index,
            start_site_id=start_site.id,
            landing_site_id=landing.id,
            task_ids=list(task_ids),
            task_reversed=reversed_flags,
            transit_agl_m=transit_agl,
            flight_time_s=cost.flight_time_s,
            distance_m=cost.distance_m,
            survey_length_m=cost.survey_length_m,
            transit_length_m=cost.transit_length_m,
            energy=cost.as_dict(),
        )
        self._materialise(sortie, uav, task_objs, reversed_flags, start_site, landing, t_start, router, options)
        return sortie

    def _pick_landing_site(
        self, uav: Uav, last_xy: tuple[float, float], start_site: Site, router
    ) -> Site:
        if uav.landing_site:
            s = self.scene.site(uav.landing_site)
            if s is not None and s.can_land:
                return s
        candidates = self.landing_candidates()
        if not self.scene.mission.allow_different_start_end:
            if start_site.can_land:
                return start_site
            # start-only site: must land elsewhere
        best, best_d = candidates[0], math.inf
        for s in candidates:
            d = router.length(last_xy, s.xy)
            # Prefer returning to the launch site unless a different end is allowed.
            if not self.scene.mission.allow_different_start_end and s.id != start_site.id:
                d += 1_000_000.0
            if s.is_reserve:
                d += 50_000.0  # reserve sites are for emergencies, not routine landings
            if d < best_d:
                best, best_d = s, d
        return best

    def _materialise(
        self,
        sortie: Sortie,
        uav: Uav,
        task_objs: Sequence[AtomicTask],
        reversed_flags: Sequence[bool],
        start_site: Site,
        landing: Site,
        t_start: datetime,
        router,
        options: ScheduleOptions | None = None,
    ) -> None:
        options = options or ScheduleOptions()
        alt_off = options.altitude_offsets_m.get(uav.id, 0.0)
        wps: list[Waypoint] = []
        t = t_start
        usable = uav.usable_endurance_s
        elapsed = 0.0

        def push(x: float, y: float, agl: float, phase: str, task_id: str | None, speed: float) -> None:
            nonlocal elapsed
            wps.append(
                Waypoint(
                    x=x,
                    y=y,
                    agl_m=agl,
                    amsl_m=self._terrain(x, y) + agl,
                    t=t,
                    phase=phase,
                    task_id=task_id,
                    speed_ms=speed,
                    remaining_endurance_s=usable - elapsed,
                )
            )

        # take-off
        push(start_site.xy[0], start_site.xy[1], 0.0, "takeoff", None, 0.0)
        t += timedelta(seconds=uav.takeoff_time_s)
        elapsed += uav.takeoff_time_s
        push(start_site.xy[0], start_site.xy[1], sortie.transit_agl_m, "takeoff", None, 0.0)

        prev = start_site.xy
        for task, rev in zip(task_objs, reversed_flags):
            coords = task.path_coords(reverse=rev)
            entry, exit_ = coords[0], coords[-1]
            # transit to the task entry
            path, length = router.path(prev, entry)
            for pt in densify(path, WAYPOINT_STEP_M)[1:]:
                seg = math.dist((wps[-1].x, wps[-1].y), pt)
                dt = seg / max(uav.ground_speed_ms, 0.1)
                t += timedelta(seconds=dt)
                elapsed += dt
                push(pt[0], pt[1], sortie.transit_agl_m, "transit", None, uav.ground_speed_ms)
            # survey
            speed = self.ctx.survey_speed_ms(uav, task.payload_class)
            turn_time = self.ctx.turn_time_s(uav)
            prev_pt = (wps[-1].x, wps[-1].y)
            n_segments = 0
            survey_pts = densify(coords, WAYPOINT_STEP_M)
            if survey_pts and math.dist(prev_pt, survey_pts[0]) < 1.0:
                survey_pts = survey_pts[1:]
            for pt in survey_pts:
                seg = math.dist(prev_pt, pt)
                dt = seg / max(speed, 0.1)
                t += timedelta(seconds=dt)
                elapsed += dt
                push(pt[0], pt[1], task.agl_m + alt_off, "survey", task.id, speed)
                prev_pt = pt
                n_segments += 1
            if task.turn_count:
                extra = task.turn_count * turn_time
                t += timedelta(seconds=extra)
                elapsed += extra
                if wps:
                    wps[-1].t = t
                    wps[-1].remaining_endurance_s = usable - elapsed
            prev = exit_

        # return to landing site
        path, length = router.path(prev, landing.xy)
        for pt in densify(path, WAYPOINT_STEP_M)[1:]:
            seg = math.dist((wps[-1].x, wps[-1].y), pt)
            dt = seg / max(uav.ground_speed_ms, 0.1)
            t += timedelta(seconds=dt)
            elapsed += dt
            push(pt[0], pt[1], sortie.transit_agl_m, "transit", None, uav.ground_speed_ms)
        t += timedelta(seconds=uav.landing_time_s)
        elapsed += uav.landing_time_s
        push(landing.xy[0], landing.xy[1], 0.0, "landing", None, 0.0)

        sortie.waypoints = wps
        sortie.t_start = t_start
        sortie.t_end = t
        sortie.flight_time_s = elapsed
        sortie.energy["flight_time_s"] = round(elapsed, 1)
        sortie.energy["flight_time_min"] = round(elapsed / 60.0, 2)
        sortie.energy["margin_s"] = round(usable - elapsed, 1)
        sortie.energy["margin_percent"] = round(100.0 * (usable - elapsed) / max(usable, 1e-9), 2)
        sortie.energy["feasible"] = elapsed <= usable + 1e-6

    # ------------------------------------------------------------------ #
    def _temporal_conflict(self, sortie: Sortie) -> dict[str, Any] | None:
        """First temporal airspace conflict of a materialised sortie, if any."""
        if not self.scene.airspace_constraints:
            return None
        win_start, win_end = self.mission_start, self.mission_end
        for zone in self.scene.airspace_constraints:
            if zone.covers_window(win_start, win_end):
                continue  # permanent -> handled geometrically
            if zone.active_from is None or zone.active_to is None:
                continue
            if sortie.t_end < zone.active_from or sortie.t_start > zone.active_to:
                continue
            for wp in sortie.waypoints:
                if not (zone.active_from <= wp.t <= zone.active_to):
                    continue
                if not zone.blocks_altitude(wp.amsl_m):
                    continue
                if zone.geom.contains(Point(wp.x, wp.y)):
                    return {
                        "zone": zone.id,
                        "active_from": zone.active_to.isoformat(),
                        "at": wp.t.isoformat(),
                        "active_to": zone.active_to,
                    }
        return None

    def schedule(
        self,
        assignment: Assignment,
        tasks: dict[str, AtomicTask],
        objective: str = "makespan",
        options: ScheduleOptions | None = None,
    ) -> Plan:
        """Build the full plan (sorties + schedule) for an assignment."""
        options = options or ScheduleOptions()
        plan = Plan(scene_id=self.scene.id, objective=objective, tasks=tasks, created_at=datetime.now())
        repairs: list[dict[str, Any]] = []

        for uav_id, trips in assignment.per_uav.items():
            uav = self.scene.uav(uav_id)
            if uav is None:
                continue
            start_site = self.start_site_for(uav)
            t_cursor = self.mission_start + timedelta(
                seconds=options.start_offsets_s.get(uav_id, 0.0)
            )
            index = 0
            for trip in trips:
                if not trip:
                    continue
                index += 1
                hold = options.hold_before_sortie_s.get(f"{uav_id}_S{index}", 0.0)
                t_start = t_cursor + timedelta(seconds=hold)
                sortie = self.build_sortie(
                    uav, index, trip, tasks, start_site, t_start, options
                )
                if options.respect_temporal_airspace:
                    for _ in range(MAX_TEMPORAL_REPAIRS):
                        conflict = self._temporal_conflict(sortie)
                        if conflict is None:
                            break
                        new_start = conflict["active_to"] + timedelta(seconds=30)
                        if new_start >= self.mission_end:
                            break
                        repairs.append(
                            {
                                "sortie": sortie.id,
                                "action": "delay_start_past_temporal_restriction",
                                "zone": conflict["zone"],
                                "from": sortie.t_start.isoformat(),
                                "to": new_start.isoformat(),
                            }
                        )
                        sortie = self.build_sortie(
                            uav, index, trip, tasks, start_site, new_start, options
                        )
                sortie.hold_s = hold
                plan.sorties.append(sortie)
                start_site = self.scene.site(sortie.landing_site_id) or start_site
                if start_site is None or not start_site.can_start:
                    start_site = self.start_site_for(uav)
                t_cursor = sortie.t_end + timedelta(seconds=uav.service_time_s)

        plan.metrics = compute_metrics(self.scene, plan)
        if repairs:
            plan.metrics["temporal_schedule_repairs"] = repairs
        return plan


def compute_metrics(scene: Scene, plan: Plan) -> dict[str, Any]:
    mission_start = scene.mission.effective_start()
    mission_end = scene.mission.effective_end()
    ends = [s.t_end for s in plan.sorties if s.t_end]
    starts = [s.t_start for s in plan.sorties if s.t_start]
    makespan_s = (max(ends) - mission_start).total_seconds() if ends else 0.0
    per_uav: dict[str, Any] = {}
    for uav in scene.fleet:
        ss = plan.sorties_of(uav.id)
        if not ss:
            continue
        per_uav[uav.id] = {
            "model": uav.model,
            "class": uav.uav_class,
            "sorties": len(ss),
            "flight_time_min": round(sum(s.flight_time_s for s in ss) / 60.0, 2),
            "distance_km": round(sum(s.distance_m for s in ss) / 1000.0, 3),
            "tasks": sum(len(s.task_ids) for s in ss),
            "min_margin_percent": round(
                min(s.energy.get("margin_percent", 0.0) for s in ss), 2
            ),
            "first_takeoff": min(s.t_start for s in ss).isoformat() if ss else None,
            "last_landing": max(s.t_end for s in ss).isoformat() if ss else None,
        }
    margins = [
        s.energy.get("margin_percent", 0.0) for s in plan.sorties if s.energy
    ]
    return {
        "objective": plan.objective,
        "mission_start": mission_start.isoformat(),
        "mission_end": mission_end.isoformat(),
        "makespan_s": round(makespan_s, 1),
        "makespan_min": round(makespan_s / 60.0, 2),
        "total_flight_s": round(plan.total_flight_s(), 1),
        "total_flight_min": round(plan.total_flight_s() / 60.0, 2),
        "total_distance_km": round(plan.total_distance_m() / 1000.0, 3),
        "survey_length_km": round(sum(s.survey_length_m for s in plan.sorties) / 1000.0, 3),
        "transit_length_km": round(sum(s.transit_length_m for s in plan.sorties) / 1000.0, 3),
        "sortie_count": len([s for s in plan.sorties if s.task_ids]),
        "used_uav_count": len(plan.used_uavs),
        "used_uavs": plan.used_uavs,
        "assigned_task_count": sum(len(s.task_ids) for s in plan.sorties),
        "min_resource_margin_percent": round(min(margins), 2) if margins else None,
        "last_landing": max(ends).isoformat() if ends else None,
        "first_takeoff": min(starts).isoformat() if starts else None,
        "window_exceeded": bool(ends and max(ends) > mission_end),
        "per_uav": per_uav,
    }
