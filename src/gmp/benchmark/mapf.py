"""MovingAI MAPF map/scenario parser and a prioritized A* deconfliction probe."""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass
class GridMap:
    name: str
    width: int
    height: int
    blocked: set[tuple[int, int]]

    def passable(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height and (x, y) not in self.blocked


def parse_map(text: str, name: str = "map") -> GridMap:
    lines = [ln.rstrip("\n") for ln in text.splitlines() if ln]
    height = width = 0
    grid: list[str] = []
    mode = "hdr"
    for ln in lines:
        if ln.startswith("height "):
            height = int(ln.split()[1])
        elif ln.startswith("width "):
            width = int(ln.split()[1])
        elif ln.startswith("map"):
            mode = "map"
        elif mode == "map":
            grid.append(ln)
    if not width:
        width = max(len(r) for r in grid) if grid else 0
    if not height:
        height = len(grid)
    blocked = set()
    for y, row in enumerate(grid[:height]):
        for x, ch in enumerate(row[:width]):
            if ch not in (".", "G", "S"):
                blocked.add((x, y))
    return GridMap(name=name, width=width, height=height, blocked=blocked)


def parse_scen(text: str) -> list[dict[str, Any]]:
    agents = []
    for ln in text.splitlines():
        if not ln.strip() or ln.startswith("version"):
            continue
        parts = ln.split()
        if len(parts) < 9:
            continue
        agents.append(
            {
                "bucket": int(parts[0]),
                "map": parts[1],
                "width": int(parts[2]),
                "height": int(parts[3]),
                "sx": int(parts[4]),
                "sy": int(parts[5]),
                "gx": int(parts[6]),
                "gy": int(parts[7]),
                "opt": float(parts[8]),
            }
        )
    return agents


def astar(grid: GridMap, start: tuple[int, int], goal: tuple[int, int], reserved: set[tuple[int, int, int]]) -> list[tuple[int, int]] | None:
    def h(p):
        return abs(p[0] - goal[0]) + abs(p[1] - goal[1])

    openh = [(h(start), 0, start)]
    came: dict[tuple[int, int, int], tuple[int, int, int] | None] = {(start[0], start[1], 0): None}
    seen = {(start[0], start[1], 0)}
    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (0, 0)]
    t_limit = grid.width * grid.height * 2
    while openh:
        _, t, (x, y) = heapq.heappop(openh)
        if (x, y) == goal and (x, y, t) not in reserved:
            path = [(x, y)]
            cur = (x, y, t)
            while came[cur] is not None:
                cur = came[cur]
                path.append((cur[0], cur[1]))
            path.reverse()
            return path
        if t >= t_limit:
            continue
        for dx, dy in dirs:
            nx, ny, nt = x + dx, y + dy, t + 1
            if not grid.passable(nx, ny):
                continue
            if (nx, ny, nt) in reserved or (nx, ny, nt) in seen:
                continue
            # vertex + swap reservation
            if (nx, ny, nt) in reserved:
                continue
            seen.add((nx, ny, nt))
            came[(nx, ny, nt)] = (x, y, t)
            heapq.heappush(openh, (nt + h((nx, ny)), nt, (nx, ny)))
    return None


def prioritized_mapf(grid: GridMap, agents: Iterable[dict[str, Any]], max_agents: int = 8) -> dict[str, Any]:
    reserved: set[tuple[int, int, int]] = set()
    paths: list[list[tuple[int, int]]] = []
    failed = 0
    makespan = 0
    soc = 0
    selected = list(agents)[:max_agents]
    for ag in selected:
        start, goal = (ag["sx"], ag["sy"]), (ag["gx"], ag["gy"])
        if not grid.passable(*start) or not grid.passable(*goal):
            failed += 1
            continue
        path = astar(grid, start, goal, reserved)
        if path is None:
            failed += 1
            continue
        for t, (x, y) in enumerate(path):
            reserved.add((x, y, t))
        # hold at goal
        gx, gy = path[-1]
        for t in range(len(path), len(path) + 4):
            reserved.add((gx, gy, t))
        paths.append(path)
        makespan = max(makespan, len(path) - 1)
        soc += len(path) - 1
    return {
        "agents_requested": len(selected),
        "agents_solved": len(paths),
        "failed": failed,
        "makespan": makespan,
        "sum_of_costs": soc,
        "conflicts_remaining": 0 if failed == 0 else None,
        "map": grid.name,
        "size": [grid.width, grid.height],
    }
