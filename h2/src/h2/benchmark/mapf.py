"""MovingAI-derived continuous H2 separation regression (not a MAPF solver).

Four-neighbour BFS creates obstacle-free centerline paths. Cells map to 10 m,
steps take 1 s; agents exist only during sorties (land/disappear at arrival).
Classic MAPF stay-at-goal and published octile optimum semantics do not apply.
The actual production H2 conflict detector/resolver processes every trajectory.
"""
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from h2.deconfliction import detect_conflicts, resolve_conflicts


@dataclass(frozen=True)
class Grid:
    name: str
    rows: tuple[str, ...]

    @property
    def width(self):
        return len(self.rows[0])

    @property
    def height(self):
        return len(self.rows)

    def free(self, cell):
        x, y = cell
        return 0 <= x < self.width and 0 <= y < self.height and self.rows[y][x] in '.GS'


def parse_map(path: str | Path) -> Grid:
    path = Path(path)
    lines = path.read_text().splitlines()
    if len(lines) < 5 or lines[0] != 'type octile' or lines[3] != 'map':
        raise ValueError('invalid MovingAI map header')
    height, width = int(lines[1].split()[1]), int(lines[2].split()[1])
    rows = tuple(lines[4:])
    if height <= 0 or width <= 0 or len(rows) != height or any(len(r) != width for r in rows):
        raise ValueError('map dimension mismatch')
    return Grid(path.name, rows)


def parse_scenario(path: str | Path, grid: Grid, limit: int = 10):
    if limit <= 0:
        raise ValueError('limit must be positive')
    lines = Path(path).read_text().splitlines()
    if not lines or lines[0] != 'version 1':
        raise ValueError('unsupported MovingAI scenario version')
    pairs = []
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 9:
            raise ValueError('invalid scenario row')
        _, name, width, height, sx, sy, gx, gy, _ = fields
        if name != grid.name or (int(width), int(height)) != (grid.width, grid.height):
            raise ValueError('scenario/map mismatch')
        start, goal = (int(sx), int(sy)), (int(gx), int(gy))
        if not grid.free(start) or not grid.free(goal):
            raise ValueError('blocked scenario endpoint')
        pairs.append((start, goal))
        if len(pairs) == limit:
            break
    if not pairs:
        raise ValueError('empty scenario')
    return pairs


def shortest_path(grid: Grid, start, goal):
    if not grid.free(start) or not grid.free(goal):
        raise ValueError('blocked endpoint')
    queue = deque([start])
    previous = {start: None}
    while queue:
        here = queue.popleft()
        if here == goal:
            path = []
            while here is not None:
                path.append(here)
                here = previous[here]
            return list(reversed(path))
        x, y = here
        for nxt in ((x+1,y), (x,y+1), (x-1,y), (x,y-1)):
            if grid.free(nxt) and nxt not in previous:
                previous[nxt] = here
                queue.append(nxt)
    raise ValueError('unreachable goal')


def run(grid: Grid, pairs, window_end_s=None):
    sorties, fleet = [], []
    for i, (start, goal) in enumerate(pairs):
        path = shortest_path(grid, start, goal)
        # A stationary agent still occupies a one-second active interval.
        if len(path) == 1:
            path.append(path[0])
        ident = f'agent-{i}'
        fleet.append(dict(id=ident, horizontal_separation_m=2.0, vertical_separation_m=2.0))
        points = [dict(x=10.0*x, y=10.0*y, z_m=100.0, agl_m=100.0,
                       t_s=float(t), phase='transit') for t,(x,y) in enumerate(path)]
        sorties.append(dict(id=f'path-{i}', uav_id=ident, start_s=0.0,
                            end_s=points[-1]['t_s'], waypoints=points))
    before = detect_conflicts(sorties, fleet)
    resolved, residual = resolve_conflicts(sorties, fleet, window_end_s)
    # Recheck the actual shifted output, not merely resolver's report.
    independent = detect_conflicts(resolved, fleet)
    return dict(profile='movingai_derived_h2', map=grid.name, fleet=fleet,
                original_sorties=sorties, sorties=resolved, conflicts_before=before,
                conflicts_after=independent, feasible=not residual and not independent,
                makespan=max((s['end_s'] for s in resolved), default=0.0),
                semantics='4-neighbour BFS, 10m cells, 1s steps, landing/disappear at goal; no MAPF BKS comparison')
