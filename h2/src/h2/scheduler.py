"""Transparent time-linear sortie model and physical-aircraft scheduling."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import math
import time

from .contract import mission_clock, poly_length
from .transit import TransitContext, NoPath
from .task_routes import route_variants


@dataclass
class Settings:
    algorithm: str = "annealing"
    search_depth: str | None = None
    objective: str = "makespan"
    time_budget_s: float = 10.0
    seed: int = 20260918
    max_iterations: int = 200
    cpsat: bool = True
    deconflict: bool = True
    # Conservative explicit model, not manufacturer performance guarantees.
    survey_speed_factor: float = 0.75
    multirotor_turn_s: float = 6.0
    vertical_speed_ms: float = 3.0
    sample_step_m: float = 25.0
    # Benchmark side input only: never adds required AtomicTask fields.
    task_windows: dict = field(default_factory=dict)
    task_service_s: dict = field(default_factory=dict)

    def validate(self):
        if self.algorithm not in ("annealing", "routing"):
            raise ValueError("invalid algorithm")
        if type(self.seed) is not int or not 0 <= self.seed <= 2147483647:
            raise ValueError("seed must be a fixed nonnegative 32-bit integer")
        if self.search_depth not in (None, "quick", "standard", "deep"):
            raise ValueError("invalid search_depth")
        for k in ("time_budget_s", "survey_speed_factor", "vertical_speed_ms", "sample_step_m"):
            if not math.isfinite(getattr(self, k)) or getattr(self, k) <= 0:
                raise ValueError(f"{k} must be finite and positive")
        if self.survey_speed_factor > 1 or self.multirotor_turn_s < 0 or not math.isfinite(self.multirotor_turn_s):
            raise ValueError("invalid resource model")
        if self.max_iterations < 0:
            raise ValueError("max_iterations must be nonnegative")
        for tid, window in self.task_windows.items():
            if len(window) != 2 or not all(math.isfinite(x) for x in window) or not 0 <= window[0] <= window[1]:
                raise ValueError(f"invalid task window {tid}")
        if any(not math.isfinite(x) or x < 0 for x in self.task_service_s.values()):
            raise ValueError("invalid task service duration")


class RouteFailure(ValueError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


class Scheduler:
    def __init__(self, bundle, settings, scene=None):
        self.bundle, self.settings = bundle, settings
        self.tasks = {t["id"]: t for t in bundle["tasks"]}
        self.fleet = {u["id"]: u for u in bundle["fleet"]}
        self.sites = {s["id"]: s for s in bundle["sites"] if not s.get("candidate", False)}
        self.eligible = bundle["feasibility"]["eligible_uav_ids_by_task"]
        self.origin, self.horizon = mission_clock(bundle)
        self.context = TransitContext(scene, bundle["crs"]["metric_epsg"])
        self.deadline = None

    def _check_budget(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise RouteFailure("search time budget exhausted")

    def task_seconds(self, uid, tid, variant=None):
        u, t = self.fleet[uid], self.tasks[tid]
        turn = (math.pi * max(50., u["turnaround_buffer_m"]) / u["ground_speed_ms"]
                if u["uav_class"] == "fixed_wing" else self.settings.multirotor_turn_s)
        internal = (variant["internal_transition_m"] if variant is not None else
                    min(v["internal_transition_m"] for v in route_variants(t)))
        return max((t["survey_length_m"] + internal) /
                   (u["ground_speed_ms"] * self.settings.survey_speed_factor) + t["turn_count"] * turn,
                   self.settings.task_service_s.get(tid, 0))

    @lru_cache(maxsize=20000)
    def route(self, uid, tids, start_id, landing_id):
        """Return a relative trajectory; no hover is introduced for fixed-wing."""
        u, o = self.fleet[uid], self.settings
        start, land = self.sites[start_id], self.sites[landing_id]
        usable = u["operational_endurance_min"] * 60 * (1-u["energy_reserve_fraction"])
        points, times, reversals, variants_used = [], [], [], []
        transit_length = survey_length = 0.0
        tclock = 0.0
        current = [start["x"], start["y"]]
        altitude = u["cruise_agl_m"]
        buffer = u["turnaround_buffer_m"] if u["uav_class"] == "fixed_wing" else 0.

        def point(xy, agl, phase, tid=None):
            points.append(dict(x=xy[0], y=xy[1], agl_m=agl,
                               z_m=agl+self.context.elevation(*xy), t_s=tclock,
                               phase=phase, task_id=tid))

        # Fixed takeoff/landing occupancy at site is a conservative scheduling
        # envelope, not an aerodynamic hover instruction or a flight controller path.
        point(current, 0.0, "takeoff")
        tclock += max(u["takeoff_time_s"], altitude/o.vertical_speed_ms)
        point(current, altitude, "takeoff")

        def move(coords, target_agl, phase, duration, tid=None):
            nonlocal current, altitude, tclock
            total = poly_length(coords)
            z0 = altitude + self.context.elevation(*current)
            z1 = target_agl + self.context.elevation(*coords[-1])
            duration = max(duration, abs(z1-z0)/o.vertical_speed_ms)
            # A same-position altitude transition is a takeoff/climb envelope;
            # reject such manoeuvres for fixed-wing during airborne task transitions.
            if total < 1e-9 and abs(target_agl-altitude) > 1e-9 and u["uav_class"] == "fixed_wing":
                raise RouteFailure("fixed-wing vertical transition needs nonzero transit")
            elapsed = 0.0
            initial_agl = altitude
            start_time = tclock
            point(coords[0], altitude, phase, tid)
            previous_fraction = 0.
            previous_z = z0
            for a, b in zip(coords, coords[1:]):
                distance = math.dist(a, b)
                step = min(o.sample_step_m, self.context.sample_step_m)
                n = max(1, math.ceil(distance/step)) if self.context.has_terrain else 1
                for k in range(1, n+1):
                    xy = [a[j]+(b[j]-a[j])*k/n for j in (0, 1)]
                    fraction = (elapsed+distance*k/n)/total if total > 1e-9 else 1.
                    agl = initial_agl+(target_agl-initial_agl)*fraction
                    z = agl+self.context.elevation(*xy)
                    tclock += max(duration*(fraction-previous_fraction), abs(z-previous_z)/o.vertical_speed_ms)
                    point(xy, agl, phase, tid)
                    previous_fraction, previous_z = fraction, z
                elapsed += distance
            tclock = max(tclock, start_time+duration)
            current, altitude = list(coords[-1]), target_agl
            if tclock-points[-1]["t_s"] > 1e-7:
                point(current, altitude, phase, tid)
            else:
                # Per-segment sums and total duration differ by floating noise.
                # Preserve one physical event rather than a sub-microsecond dwell.
                tclock = points[-1]["t_s"]

        for tid in tids:
            self._check_budget()
            if uid not in self.eligible[tid]:
                raise RouteFailure("ineligible UAV")
            task = self.tasks[tid]
            candidates = []
            for variant_index, variant in enumerate(route_variants(task)):
                if u["uav_class"] == "fixed_wing" and not variant["fixed_wing_safe"]:
                    continue
                coords = variant["geom_coords"]
                if not self.context.geometry_clear(coords):
                    continue
                for reverse in (False, True):
                    path = list(reversed(coords)) if reverse else coords
                    try:
                        transit = self.context.path(tuple(current), tuple(path[0]), buffer)
                        candidates.append((poly_length(transit), variant_index, reverse, path, transit))
                    except NoPath:
                        continue
            if not candidates:
                raise RouteFailure("no admissible task variant or geographic transit to task")
            distance, variant_index, reverse, path, transit = min(candidates, key=lambda c: c[:3])
            variant = route_variants(task)[variant_index]
            move(transit, task["agl_m"], "transit", distance/u["ground_speed_ms"])
            transit_length += distance
            begin = tclock
            # Turn penalty is distributed over the path, never an FW hover command.
            move(path, task["agl_m"], "survey", self.task_seconds(uid, tid, variant), tid)
            times.append(dict(task_id=tid, start_s=begin, end_s=tclock))
            survey_length += task["survey_length_m"]
            reversals.append(reverse)
            variants_used.append(variant["id"])
        try:
            transit = self.context.path(tuple(current), (land["x"], land["y"]), buffer)
        except NoPath as e:
            raise RouteFailure("no geographic return path") from e
        distance = poly_length(transit)
        move(transit, u["cruise_agl_m"], "transit", distance/u["ground_speed_ms"])
        transit_length += distance
        point(current, altitude, "landing")
        tclock += max(u["landing_time_s"], altitude/o.vertical_speed_ms)
        point(current, 0.0, "landing")
        if tclock > usable+1e-6:
            raise RouteFailure("sortie exceeds endurance including reserve and return",
                               flight_time_s=tclock, usable_time_s=usable, uav_id=uid)
        return dict(uav_id=uid, start_site_id=start_id, landing_site_id=landing_id,
                    task_ids=list(tids), task_reversed=reversals, task_variants=variants_used, start_s=0., end_s=tclock,
                    flight_time_s=tclock, distance_m=poly_length([[p["x"],p["y"]] for p in points]),
                    transit_length_m=transit_length, survey_length_m=survey_length,
                    resource_margin_s=usable-tclock, waypoints=points, task_times=times)

    def append_task(self, uid, scheduled, tid):
        """Extend only the last sortie or add a serviced sortie, keeping the prefix.

        Initial construction must visit every task without rebuilding every
        possible partition of the aircraft's entire history at each insertion.
        Returned routes do not mutate the incumbent or cached relative routes.
        """
        from copy import deepcopy

        u = self.fleet[uid]
        if not scheduled:
            return self.schedule_uav(uid, [tid])
        last = scheduled[-1]
        attempts = [(scheduled[:-1], last["task_ids"] + [tid],
                     last["start_site_id"], last["start_s"]),
                    (scheduled, [tid], last["landing_site_id"],
                     last["end_s"] + u["service_time_s"])]
        choices = []
        failures = []
        for prefix, tids, pos, ready in attempts:
            if self.sites[pos]["role"] not in {"both", "start"}:
                continue
            for land in self.sites.values():
                if (land["role"] not in {"both", "landing"}
                        or (u["landing_site"] is not None and land["id"] != u["landing_site"])
                        or (not self.bundle["mission"]["allow_different_start_end"] and land["id"] != pos)):
                    continue
                try:
                    relative = self.route(uid, tuple(tids), pos, land["id"])
                except (RouteFailure, NoPath) as exc:
                    failures.append(dict(kind="new_sortie" if len(tids) == 1 else "extend_sortie",
                                         start_site_id=pos, landing_site_id=land["id"],
                                         reason=str(exc), **getattr(exc, "details", {})))
                    continue
                depart = ready
                for timing in relative["task_times"]:
                    window = self.settings.task_windows.get(timing["task_id"])
                    if window:
                        depart = max(depart, window[0] - timing["start_s"])
                if any((w := self.settings.task_windows.get(t["task_id"]))
                       and depart + t["start_s"] > w[1] + 1e-6 for t in relative["task_times"]):
                    continue
                if self.horizon is not None and depart + relative["end_s"] > self.horizon + 1e-6:
                    failures.append(dict(kind="new_sortie" if len(tids) == 1 else "extend_sortie",
                                         reason="mission_window", end_s=depart + relative["end_s"],
                                         window_s=self.horizon))
                    continue
                route = deepcopy(relative)
                route.update(index=len(prefix) + 1, id=f"{uid}_S{len(prefix)+1}",
                             start_s=depart, end_s=depart + relative["end_s"])
                for wp in route["waypoints"]:
                    wp["t_s"] += depart
                for timing in route["task_times"]:
                    timing["start_s"] += depart
                    timing["end_s"] += depart
                routes = [*prefix, route]
                cost = (route["end_s"] if self.settings.objective == "makespan"
                        else sum(s["flight_time_s"] for s in routes))
                choices.append((cost, land["id"], routes))
        if choices:
            return min(choices, key=lambda item: item[:2])[2]
        # Repartition only on failure (e.g. a previously final landing-only
        # site must become an intermediate stop); never drop the old tasks.
        if self.sites[last["landing_site_id"]]["role"] not in {"both", "start"}:
            return self.schedule_uav(uid, [t for s in scheduled for t in s["task_ids"]] + [tid])
        raise RouteFailure("no feasible extension or serviced sortie", route_failures=failures)

    def schedule_uav(self, uid, tids):
        """Greedy maximal packing, preserving physical location between sorties."""
        from copy import deepcopy
        u = self.fleet[uid]
        starts = [s["id"] for s in self.sites.values() if s["role"] in {"both", "start"}
                  and (u["start_site"] is None or s["id"] == u["start_site"])]
        results = []
        failures = []
        for initial in starts:
            out, pos, ready, i = [], initial, 0., 0
            try:
                while i < len(tids):
                    if self.sites[pos]["role"] not in {"both", "start"}:
                        raise RouteFailure("previous landing site cannot launch next sortie")
                    lands = [s["id"] for s in self.sites.values() if s["role"] in {"both", "landing"}
                             and (u["landing_site"] is None or s["id"] == u["landing_site"])
                             and (self.bundle["mission"]["allow_different_start_end"] or s["id"] == pos)]
                    best = None
                    for j in range(i+1, len(tids)+1):
                        self._check_budget()
                        choices = []
                        for land in lands:
                            if j < len(tids) and self.sites[land]["role"] not in {"both", "start"}:
                                continue
                            try:
                                relative = self.route(uid, tuple(tids[i:j]), pos, land)
                                depart = ready
                                for timing in relative["task_times"]:
                                    w = self.settings.task_windows.get(timing["task_id"])
                                    if w:
                                        depart = max(depart, w[0]-timing["start_s"])
                                if any((w := self.settings.task_windows.get(t["task_id"])) and depart+t["start_s"] > w[1]+1e-6 for t in relative["task_times"]):
                                    continue
                                if self.horizon is not None and depart+relative["end_s"] > self.horizon+1e-6:
                                    continue
                                choices.append((relative["flight_time_s"], land, depart, relative))
                            except (RouteFailure, NoPath) as exc:
                                if len(tids) == 1:
                                    failures.append(dict(kind="new_sortie", start_site_id=pos,
                                                         landing_site_id=land, reason=str(exc),
                                                         **getattr(exc, "details", {})))
                                continue
                        if not choices:
                            # Longer grouping may permit ending at a landing-only site.
                            continue
                        _, land, depart, route = min(choices, key=lambda c: c[:2])
                        best = j, land, depart, route
                    if best is None:
                        raise RouteFailure("no resource/site/window-feasible sortie")
                    j, land, depart, relative = best
                    route = deepcopy(relative)
                    route.update(index=len(out)+1, id=f"{uid}_S{len(out)+1}", start_s=depart, end_s=depart+relative["end_s"])
                    for wp in route["waypoints"]:
                        wp["t_s"] += depart
                    for timing in route["task_times"]:
                        timing["start_s"] += depart
                        timing["end_s"] += depart
                    out.append(route)
                    pos, ready, i = land, route["end_s"]+u["service_time_s"], j
                results.append(out)
            except RouteFailure:
                continue
        if not results:
            if not tids:
                return []
            raise RouteFailure(f"no feasible schedule found for {uid}", route_failures=failures)
        return min(results, key=lambda r: (r[-1]["end_s"] if r and self.settings.objective == "makespan" else sum(s["flight_time_s"] for s in r)))

    def schedule(self, assignment):
        return [s for uid in self.fleet for s in self.schedule_uav(uid, assignment.get(uid, []))]
