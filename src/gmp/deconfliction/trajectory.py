"""4D trajectory representation: p_k(t) = (x(t), y(t), z(t))."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

import numpy as np

from ..models import Plan, Sortie


@dataclass
class Trajectory:
    """Time-parameterised polyline of one sortie."""

    sortie_id: str
    uav_id: str
    t0: float  # seconds since the epoch reference
    t1: float
    ts: np.ndarray
    xs: np.ndarray
    ys: np.ndarray
    zs: np.ndarray
    agls: np.ndarray
    phases: list[str]
    bbox: tuple[float, float, float, float]

    def sample(self, grid: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        x = np.interp(grid, self.ts, self.xs)
        y = np.interp(grid, self.ts, self.ys)
        z = np.interp(grid, self.ts, self.zs)
        return x, y, z

    def position_at(self, t_s: float) -> tuple[float, float, float]:
        return (
            float(np.interp(t_s, self.ts, self.xs)),
            float(np.interp(t_s, self.ts, self.ys)),
            float(np.interp(t_s, self.ts, self.zs)),
        )

    def overlaps(self, other: "Trajectory") -> bool:
        return not (self.t1 < other.t0 or other.t1 < self.t0)


def _epoch(t: datetime, ref: datetime) -> float:
    return (t - ref).total_seconds()


def build_trajectory(sortie: Sortie, ref: datetime) -> Trajectory | None:
    if not sortie.waypoints:
        return None
    ts, xs, ys, zs, agls, phases = [], [], [], [], [], []
    last_t = -1e18
    for wp in sortie.waypoints:
        t = _epoch(wp.t, ref)
        if t <= last_t:
            t = last_t + 1e-3  # keep the time axis strictly increasing
        last_t = t
        ts.append(t)
        xs.append(wp.x)
        ys.append(wp.y)
        zs.append(wp.amsl_m)
        agls.append(wp.agl_m)
        phases.append(wp.phase)
    xs_a = np.asarray(xs, dtype=float)
    ys_a = np.asarray(ys, dtype=float)
    return Trajectory(
        sortie_id=sortie.id,
        uav_id=sortie.uav_id,
        t0=ts[0],
        t1=ts[-1],
        ts=np.asarray(ts, dtype=float),
        xs=xs_a,
        ys=ys_a,
        zs=np.asarray(zs, dtype=float),
        agls=np.asarray(agls, dtype=float),
        phases=phases,
        bbox=(float(xs_a.min()), float(ys_a.min()), float(xs_a.max()), float(ys_a.max())),
    )


def build_trajectories(plan: Plan, ref: datetime | None = None) -> tuple[list[Trajectory], datetime]:
    starts = [s.t_start for s in plan.sorties if s.t_start]
    if ref is None:
        ref = min(starts) if starts else None
    out: list[Trajectory] = []
    if ref is None:
        return out, ref  # type: ignore[return-value]
    for s in plan.sorties:
        traj = build_trajectory(s, ref)
        if traj is not None:
            out.append(traj)
    return out, ref
