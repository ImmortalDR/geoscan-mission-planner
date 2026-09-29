"""Standard VRPTW, deliberately independent of the UAV model.

Distance and travel time are Euclidean, rounded to nearest 0.001 (half up).
The objective is total distance, NOT Solomon's lexicographic fleet/distance
objective. Service windows bound service start, including depot start/end.
No published BKS gap is claimed under this convention.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
import math
from pathlib import Path
import time

SCALE = 1000
CONVENTION = 'euclidean_round_half_up_0.001; travel_speed=1; objective=total_distance'


@dataclass(frozen=True)
class Customer:
    id: int
    x: float
    y: float
    demand: int
    ready: float
    due: float
    service: float


@dataclass(frozen=True)
class Instance:
    name: str
    vehicles: int
    capacity: int
    customers: tuple[Customer, ...]  # Depot is index zero, id zero.

    def __post_init__(self):
        if self.vehicles <= 0 or self.capacity <= 0:
            raise ValueError('vehicles and capacity must be positive')
        if not self.customers or self.customers[0].id != 0:
            raise ValueError('first customer must be depot id 0')
        if len({c.id for c in self.customers}) != len(self.customers):
            raise ValueError('duplicate customer id')
        for c in self.customers:
            if not all(math.isfinite(v) for v in (c.x, c.y, c.ready, c.due, c.service)):
                raise ValueError('nonfinite coordinate or time')
            if c.demand < 0 or c.ready < 0 or c.due < c.ready or c.service < 0:
                raise ValueError('invalid demand or time window')
        if self.customers[0].demand != 0:
            raise ValueError('depot demand must be zero')


@dataclass(frozen=True)
class Visit:
    customer_id: int
    service_start: float


@dataclass(frozen=True)
class Route:
    vehicle_id: int
    visits: tuple[Visit, ...]
    distance: float
    load: int


@dataclass
class Result:
    instance: str
    status: str
    routes: list[Route]
    runtime_s: float
    violations: list[str] = field(default_factory=list)
    convention: str = CONVENTION

    @property
    def feasible(self):
        return self.status == 'feasible' and not self.violations

    @property
    def metrics(self):
        return dict(feasible=self.feasible, total_distance=sum(r.distance for r in self.routes),
                    vehicles_used=len(self.routes), runtime_s=self.runtime_s,
                    violations=len(self.violations), convention=self.convention)

    def to_dict(self):
        return {**asdict(self), 'metrics': self.metrics}


def parse(path: str | Path) -> Instance:
    """Parse Solomon and Gehring-Homberger seven-column plain text files."""
    lines = Path(path).read_text(encoding='utf-8-sig').splitlines()
    name = next((line.strip() for line in lines if line.strip()), '')
    section = None
    fleet = None
    customers = []
    for line in lines[1:]:
        words = line.split()
        if not words:
            continue
        if words[0].upper() in ('VEHICLE', 'CUSTOMER'):
            section = words[0].upper()
            continue
        # Heading lines are ignored, malformed numeric rows are rejected.
        try:
            float(words[0])
        except ValueError:
            continue
        if section == 'VEHICLE':
            if len(words) != 2 or fleet is not None:
                raise ValueError('invalid vehicle row')
            fleet = tuple(int(w) for w in words)
        elif section == 'CUSTOMER':
            if len(words) != 7:
                raise ValueError('customer row must contain seven fields')
            ident, x, y, demand, ready, due, service = words
            customers.append(Customer(int(ident), float(x), float(y), int(demand),
                                      float(ready), float(due), float(service)))
        else:
            raise ValueError('numeric data outside a section')
    if fleet is None:
        raise ValueError('missing vehicle section')
    return Instance(name, *fleet, tuple(customers))


def distance_units(a: Customer, b: Customer) -> int:
    return math.floor(math.hypot(a.x-b.x, a.y-b.y)*SCALE + 0.5)


def validate(instance: Instance, routes: list[Route]) -> list[str]:
    """Recompute assignment, geometry, load and timing without solver state."""
    errors = []
    customers = {c.id: c for c in instance.customers}
    seen = Counter()
    vehicle_ids = []
    for route in routes:
        vehicle_ids.append(route.vehicle_id)
        label = f'vehicle {route.vehicle_id}'
        if not 0 <= route.vehicle_id < instance.vehicles:
            errors.append(f'{label}: invalid vehicle')
        if len(route.visits) < 3 or route.visits[0].customer_id != 0 or route.visits[-1].customer_id != 0:
            errors.append(f'{label}: route must begin/end at depot and contain work')
        load = 0
        distance = 0
        previous = None
        for pos, visit in enumerate(route.visits):
            c = customers.get(visit.customer_id)
            if c is None:
                errors.append(f'{label}: unknown customer {visit.customer_id}')
                previous = None
                continue
            if 0 < pos < len(route.visits)-1:
                if c.id == 0:
                    errors.append(f'{label}: intermediate depot')
                seen[c.id] += 1
                load += c.demand
            t = visit.service_start
            if not math.isfinite(t) or not c.ready-1e-7 <= t <= c.due+1e-7:
                errors.append(f'{label}: time window at {c.id}')
            if previous:
                p, pt = previous
                d = distance_units(p, c)/SCALE
                distance += d
                if t + 1e-7 < pt + math.ceil(p.service*SCALE)/SCALE + d:
                    errors.append(f'{label}: impossible travel/service before {c.id}')
            previous = c, t
        if load > instance.capacity:
            errors.append(f'{label}: capacity exceeded')
        if route.load != load:
            errors.append(f'{label}: incorrect reported load')
        if not math.isfinite(route.distance) or abs(route.distance-distance) > 1e-6:
            errors.append(f'{label}: incorrect reported distance')
    if len(set(vehicle_ids)) != len(vehicle_ids):
        errors.append('vehicle used in multiple routes')
    for c in instance.customers[1:]:
        if seen[c.id] != 1:
            errors.append(f'customer {c.id}: visited {seen[c.id]} times')
    return errors


def solve(instance: Instance, time_limit_s: float = 1.0, method: str = 'guided_local_search') -> Result:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    if not math.isfinite(time_limit_s) or time_limit_s <= 0:
        raise ValueError('time_limit_s must be positive and finite')
    if method not in ('baseline', 'guided_local_search'):
        raise ValueError('unknown method')
    started = time.perf_counter()
    cs = instance.customers
    manager = pywrapcp.RoutingIndexManager(len(cs), instance.vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)
    distances = [[distance_units(a, b) for b in cs] for a in cs]
    distance_cb = routing.RegisterTransitCallback(
        lambda a, b: distances[manager.IndexToNode(a)][manager.IndexToNode(b)])
    routing.SetArcCostEvaluatorOfAllVehicles(distance_cb)
    travel_cb = routing.RegisterTransitCallback(lambda a, b:
        distances[manager.IndexToNode(a)][manager.IndexToNode(b)] +
        math.ceil(cs[manager.IndexToNode(a)].service*SCALE))
    horizon = math.floor(cs[0].due*SCALE)
    routing.AddDimension(travel_cb, horizon, horizon, False, 'Time')
    times = routing.GetDimensionOrDie('Time')
    for i, c in enumerate(cs[1:], 1):
        lo, hi = math.ceil(c.ready*SCALE), min(math.floor(c.due*SCALE), horizon)
        if lo > hi:
            return Result(instance.name, 'infeasible', [], time.perf_counter()-started,
                          [f'customer {c.id}: window outside depot horizon'])
        times.CumulVar(manager.NodeToIndex(i)).SetRange(lo, hi)
    for vehicle in range(instance.vehicles):
        for index in (routing.Start(vehicle), routing.End(vehicle)):
            times.CumulVar(index).SetRange(math.ceil(cs[0].ready*SCALE), horizon)
            routing.AddVariableMinimizedByFinalizer(times.CumulVar(index))
    demand_cb = routing.RegisterUnaryTransitCallback(lambda a: cs[manager.IndexToNode(a)].demand)
    routing.AddDimensionWithVehicleCapacity(demand_cb, 0, [instance.capacity]*instance.vehicles, True, 'Capacity')
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    params.time_limit.FromMilliseconds(max(1, int(time_limit_s*1000)))
    if method == 'baseline':
        params.solution_limit = 1
    else:
        params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    assignment = routing.SolveWithParameters(params)
    elapsed = time.perf_counter()-started
    if assignment is None:
        status = 'infeasible' if routing.status() == 6 else 'no_solution_found'
        return Result(instance.name, status, [], elapsed)
    routes = []
    for vehicle in range(instance.vehicles):
        if not routing.IsVehicleUsed(assignment, vehicle):
            continue
        index = routing.Start(vehicle)
        visits = []
        dist = 0
        load = 0
        while True:
            node = manager.IndexToNode(index)
            visits.append(Visit(cs[node].id, assignment.Min(times.CumulVar(index))/SCALE))
            load += cs[node].demand
            if routing.IsEnd(index):
                break
            nxt = assignment.Value(routing.NextVar(index))
            dist += distances[node][manager.IndexToNode(nxt)]
            index = nxt
        routes.append(Route(vehicle, tuple(visits), dist/SCALE, load))
    errors = validate(instance, routes)
    return Result(instance.name, 'feasible' if not errors else 'invalid', routes, elapsed, errors)
