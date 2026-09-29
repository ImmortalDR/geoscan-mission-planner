"""Transparent conservative resource model (TS §10).

Time-linear model with a fixed take-off/landing cost and an explicit energy
reserve. Wind is *not* used to modify energy consumption (customer decision,
TS §11); it only filters the fleet and scores sweep directions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..kb.payloads import default_payload_kb
from ..models import AtomicTask, Scene, Uav

#: Time penalty per survey turn, seconds.
MULTIROTOR_TURN_S = 6.0


@dataclass
class ResourceContext:
    """Per-scene speed/turn model shared by optimizer, scheduler and validator."""

    scene: Scene
    survey_speed: dict[tuple[str, str], float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        kb = default_payload_kb()
        for uav in self.scene.fleet:
            for payload_type in set(
                [j.survey_type for j in self.scene.jobs] + list(uav.payload_classes)
            ):
                self.survey_speed[(uav.id, payload_type)] = uav.ground_speed_ms * kb.survey_speed_factor(
                    payload_type
                )

    # ------------------------------------------------------------------ #
    def survey_speed_ms(self, uav: Uav, payload_type: str) -> float:
        return self.survey_speed.get(
            (uav.id, payload_type),
            uav.ground_speed_ms * default_payload_kb().survey_speed_factor(payload_type),
        )

    def turn_time_s(self, uav: Uav) -> float:
        if uav.is_fixed_wing:
            radius = max(uav.turnaround_buffer_m, 50.0)
            return math.pi * radius / max(uav.ground_speed_ms, 1.0)
        return MULTIROTOR_TURN_S

    def task_survey_time_s(self, uav: Uav, task: AtomicTask) -> float:
        """Survey lines + in-task transitions between adjacent lines + turn penalty."""
        speed = self.survey_speed_ms(uav, task.payload_class)
        line_time = (task.survey_length_m + task.internal_transition_m) / max(speed, 0.1)
        return line_time + task.turn_count * self.turn_time_s(uav)

    def transit_time_s(self, uav: Uav, distance_m: float) -> float:
        return distance_m / max(uav.ground_speed_ms, 0.1)


@dataclass
class SortieCost:
    flight_time_s: float
    distance_m: float
    survey_length_m: float
    transit_length_m: float
    survey_time_s: float
    transit_time_s: float
    takeoff_s: float
    landing_s: float
    feasible: bool
    usable_endurance_s: float
    margin_s: float
    reserve_fraction: float
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "flight_time_s": round(self.flight_time_s, 1),
            "flight_time_min": round(self.flight_time_s / 60.0, 2),
            "distance_m": round(self.distance_m, 1),
            "survey_length_m": round(self.survey_length_m, 1),
            "transit_length_m": round(self.transit_length_m, 1),
            "survey_time_s": round(self.survey_time_s, 1),
            "transit_time_s": round(self.transit_time_s, 1),
            "takeoff_s": self.takeoff_s,
            "landing_s": self.landing_s,
            "usable_endurance_s": round(self.usable_endurance_s, 1),
            "reserve_fraction": self.reserve_fraction,
            "margin_s": round(self.margin_s, 1),
            "margin_percent": round(
                100.0 * self.margin_s / max(self.usable_endurance_s, 1e-9), 2
            ),
            "feasible": self.feasible,
        }


def sortie_cost(
    ctx: ResourceContext,
    uav: Uav,
    tasks: Sequence[AtomicTask],
    reversed_flags: Sequence[bool],
    start_xy: tuple[float, float],
    landing_xy: tuple[float, float],
    transit_length_fn,
) -> SortieCost:
    """Compute the resource cost of one sortie.

    ``transit_length_fn(a, b)`` returns the safe transit distance in metres.
    """
    survey_len = 0.0
    survey_time = 0.0
    transit_len = 0.0
    prev = start_xy
    for task, rev in zip(tasks, reversed_flags):
        entry, exit_ = task.endpoints(reverse=rev)
        transit_len += transit_length_fn(prev, entry)
        survey_len += task.survey_length_m
        survey_time += ctx.task_survey_time_s(uav, task)
        prev = exit_
    transit_len += transit_length_fn(prev, landing_xy)
    transit_time = ctx.transit_time_s(uav, transit_len)
    flight = uav.takeoff_time_s + transit_time + survey_time + uav.landing_time_s
    usable = uav.usable_endurance_s
    return SortieCost(
        flight_time_s=flight,
        distance_m=survey_len + transit_len,
        survey_length_m=survey_len,
        transit_length_m=transit_len,
        survey_time_s=survey_time,
        transit_time_s=transit_time,
        takeoff_s=uav.takeoff_time_s,
        landing_s=uav.landing_time_s,
        feasible=flight <= usable + 1e-6,
        usable_endurance_s=usable,
        margin_s=usable - flight,
        reserve_fraction=uav.energy_reserve_fraction,
    )


def remaining_endurance_s(uav: Uav, elapsed_flight_s: float) -> float:
    """Flight time left before the mandatory reserve is consumed."""
    return uav.usable_endurance_s - elapsed_flight_s
