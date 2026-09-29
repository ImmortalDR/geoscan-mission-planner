"""Pairwise 4D conflict detection with type-dependent separation minima."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from ..models import Plan, Scene
from .trajectory import Trajectory, build_trajectories

#: Base temporal sampling step; refined by the closing speed of the pair.
DEFAULT_STEP_S = 5.0


@dataclass
class Conflict:
    sortie_a: str
    sortie_b: str
    uav_a: str
    uav_b: str
    required_h_m: float
    required_v_m: float
    min_h_m: float
    v_at_min_h_m: float
    t_first: datetime
    t_last: datetime
    duration_s: float
    x: float
    y: float
    samples: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "sortie_a": self.sortie_a,
            "sortie_b": self.sortie_b,
            "uav_a": self.uav_a,
            "uav_b": self.uav_b,
            "required_horizontal_m": round(self.required_h_m, 1),
            "required_vertical_m": round(self.required_v_m, 1),
            "min_horizontal_m": round(self.min_h_m, 1),
            "vertical_at_min_horizontal_m": round(self.v_at_min_h_m, 1),
            "t_first": self.t_first.isoformat(),
            "t_last": self.t_last.isoformat(),
            "duration_s": round(self.duration_s, 1),
            "location_xy": [round(self.x, 1), round(self.y, 1)],
            "samples_in_violation": self.samples,
        }


def separation_requirement(scene: Scene, uav_a: str, uav_b: str) -> tuple[float, float]:
    ua, ub = scene.uav(uav_a), scene.uav(uav_b)
    h = max(
        ua.horizontal_separation_m if ua else 100.0,
        ub.horizontal_separation_m if ub else 100.0,
    )
    v = max(
        ua.vertical_separation_m if ua else 50.0,
        ub.vertical_separation_m if ub else 50.0,
    )
    return h, v


def detect_conflicts(
    scene: Scene,
    plan: Plan,
    step_s: float = DEFAULT_STEP_S,
    ref: datetime | None = None,
) -> tuple[list[Conflict], dict[str, Any]]:
    trajs, ref_dt = build_trajectories(plan, ref)
    conflicts: list[Conflict] = []
    checked_pairs = 0
    min_observed_h = float("inf")
    if len(trajs) < 2:
        return conflicts, {
            "pairs_checked": 0,
            "sample_step_s": step_s,
            "min_horizontal_separation_m": None,
            "trajectory_count": len(trajs),
        }

    for i in range(len(trajs)):
        for j in range(i + 1, len(trajs)):
            ta, tb = trajs[i], trajs[j]
            if ta.uav_id == tb.uav_id:
                continue  # the same airframe cannot fly two sorties at once
            if not ta.overlaps(tb):
                continue
            req_h, req_v = separation_requirement(scene, ta.uav_id, tb.uav_id)
            # bounding box pre-filter
            if (
                ta.bbox[0] - req_h > tb.bbox[2]
                or tb.bbox[0] - req_h > ta.bbox[2]
                or ta.bbox[1] - req_h > tb.bbox[3]
                or tb.bbox[1] - req_h > ta.bbox[3]
            ):
                continue
            checked_pairs += 1
            t_start = max(ta.t0, tb.t0)
            t_end = min(ta.t1, tb.t1)
            if t_end <= t_start:
                continue
            n = max(int((t_end - t_start) / step_s) + 1, 2)
            grid = np.linspace(t_start, t_end, n)
            xa, ya, za = ta.sample(grid)
            xb, yb, zb = tb.sample(grid)
            dh = np.hypot(xa - xb, ya - yb)
            dv = np.abs(za - zb)
            if dh.size:
                min_observed_h = min(min_observed_h, float(dh.min()))
            viol = (dh < req_h) & (dv < req_v)
            if not viol.any():
                continue
            idx = np.flatnonzero(viol)
            # split into contiguous runs so each encounter is one conflict record
            splits = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
            for run in splits:
                k = run[int(np.argmin(dh[run]))]
                conflicts.append(
                    Conflict(
                        sortie_a=ta.sortie_id,
                        sortie_b=tb.sortie_id,
                        uav_a=ta.uav_id,
                        uav_b=tb.uav_id,
                        required_h_m=req_h,
                        required_v_m=req_v,
                        min_h_m=float(dh[run].min()),
                        v_at_min_h_m=float(dv[k]),
                        t_first=ref_dt + timedelta(seconds=float(grid[run[0]])),
                        t_last=ref_dt + timedelta(seconds=float(grid[run[-1]])),
                        duration_s=float(grid[run[-1]] - grid[run[0]]),
                        x=float(xa[k]),
                        y=float(ya[k]),
                        samples=int(run.size),
                    )
                )
    stats = {
        "pairs_checked": checked_pairs,
        "sample_step_s": step_s,
        "trajectory_count": len(trajs),
        "conflict_count": len(conflicts),
        "min_horizontal_separation_m": (
            round(min_observed_h, 1) if min_observed_h != float("inf") else None
        ),
    }
    return conflicts, stats
