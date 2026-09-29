"""M11 AtomicTaskBuilder."""
from __future__ import annotations

import math

from ..models import AtomicTask, Transect


def _hop_m(a: Transect, b: Transect) -> float:
    return math.dist(a.end, b.start)


def _join_hop_m(left: list[Transect], right: list[Transect]) -> float:
    if not left or not right:
        return 0.0
    return _hop_m(left[-1], right[0])


def chunk_transects(
    transects: list[Transect],
    chunk_len_m: float,
    max_chunks: int,
    *,
    max_hop_m: float | None = None,
) -> list[list[Transect]]:
    """Split survey lines into tasks by length and optional max end→start hop.

    Large hops (holes, inter-cell ferry) stay out of ``geom_coords`` so H2
    treats them as transit between atomic tasks instead of continuous survey.
    """
    chunks: list[list[Transect]] = []
    cur: list[Transect] = []
    cur_len = 0.0
    for t in transects:
        if cur:
            over_len = cur_len + t.length_m > chunk_len_m
            over_hop = max_hop_m is not None and _hop_m(cur[-1], t) > max_hop_m
            if over_len or over_hop:
                chunks.append(cur)
                cur, cur_len = [], 0.0
        cur.append(t)
        cur_len += t.length_m
    if cur:
        chunks.append(cur)

    forced_long_joins = 0
    while len(chunks) > max_chunks and len(chunks) > 1:
        best_i = -1
        best_hop = float("inf")
        # Prefer merging across short joins so long hops stay task boundaries.
        for i in range(len(chunks) - 1):
            hop = _join_hop_m(chunks[i], chunks[i + 1])
            if max_hop_m is not None and hop > max_hop_m:
                continue
            if hop < best_hop:
                best_hop = hop
                best_i = i
        if best_i < 0:
            # Last resort: still need to respect max_chunks.
            best_i = min(
                range(len(chunks) - 1),
                key=lambda i: _join_hop_m(chunks[i], chunks[i + 1]),
            )
            forced_long_joins += 1
        merged = chunks[best_i] + chunks[best_i + 1]
        chunks = chunks[:best_i] + [merged] + chunks[best_i + 2 :]

    # Stash count for callers that want a warning (engine reads via attribute).
    chunk_transects.last_forced_long_joins = forced_long_joins  # type: ignore[attr-defined]
    return chunks


chunk_transects.last_forced_long_joins = 0  # type: ignore[attr-defined]


def build_atomic_tasks(
    chunks: list[list[Transect]],
    *,
    job_id: str,
    payload_class: str,
    payload_profile_id: str,
    agl_m: float,
    sweep_angle_deg: float,
    fixed_wing_safe_flags: list[bool],
) -> list[AtomicTask]:
    tasks: list[AtomicTask] = []
    for i, chunk in enumerate(chunks):
        if not chunk:
            continue
        geom: list[tuple[float, float]] = []
        for t in chunk:
            if geom and geom[-1] == t.coords[0]:
                geom.extend(t.coords[1:])
            else:
                geom.extend(t.coords)
        internal = sum(math.dist(chunk[k].end, chunk[k + 1].start) for k in range(len(chunk) - 1))
        safe = fixed_wing_safe_flags[i] if i < len(fixed_wing_safe_flags) else True
        tasks.append(
            AtomicTask(
                id=f"{job_id}#T{i:03d}",
                job_id=job_id,
                payload_class=payload_class,
                payload_profile_id=payload_profile_id,
                agl_m=agl_m,
                transects=chunk,
                survey_length_m=sum(t.length_m for t in chunk),
                turn_count=max(len(chunk) - 1, 0),
                sweep_angle_deg=sweep_angle_deg,
                entry=chunk[0].start,
                exit=chunk[-1].end,
                geom_coords=geom,
                internal_transition_m=internal,
                fixed_wing_safe=safe,
                notes=[] if safe else ["fixed-wing turn zone unsafe near NFZ/boundary"],
            )
        )
    return tasks
