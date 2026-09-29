"""Domain model for group UAV mission planning.

Every geometry stored on these objects is expressed in the scene metric CRS
(see :mod:`gmp.geo`). Conversion to WGS-84 happens only in the IO layer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Optional, Sequence

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from .geo import CrsPipeline

SurveyType = Literal["rgb", "multispectral", "thermal", "lidar", "geophysics", "rgb_video"]
UavClass = Literal["fixed_wing", "multirotor", "vtol"]
SiteRole = Literal["both", "start", "landing", "reserve"]

MIN_SAFE_AGL_M = 40.0
DEFAULT_OBSTACLE_VERTICAL_BUFFER_M = 30.0


# --------------------------------------------------------------------------- #
# Scene inputs
# --------------------------------------------------------------------------- #
@dataclass
class CameraModel:
    """Sensor geometry used to derive AGL, footprint and overlap spacing."""

    id: str
    image_width_px: int
    image_height_px: int
    focal_length_mm: float
    pixel_pitch_um: float
    fov_across_deg: float | None = None  # used by lidar/geophysics style payloads

    @property
    def sensor_width_mm(self) -> float:
        return self.image_width_px * self.pixel_pitch_um / 1000.0

    @property
    def sensor_height_mm(self) -> float:
        return self.image_height_px * self.pixel_pitch_um / 1000.0

    def agl_for_gsd(self, gsd_m: float) -> float:
        """H = GSD * f / pixel_pitch (both in consistent units)."""
        return gsd_m * self.focal_length_mm / (self.pixel_pitch_um / 1000.0)

    def gsd_for_agl(self, agl_m: float) -> float:
        return agl_m * (self.pixel_pitch_um / 1000.0) / self.focal_length_mm

    def footprint(self, agl_m: float) -> tuple[float, float]:
        """Across-track and along-track ground footprint in metres."""
        w = self.sensor_width_mm * agl_m / self.focal_length_mm
        h = self.sensor_height_mm * agl_m / self.focal_length_mm
        return w, h


@dataclass
class PayloadProfile:
    """Survey payload requirement plus the derived coverage geometry."""

    id: str
    type: str
    nominal_agl_m: float
    gsd_cm: float | None = None
    front_overlap: float | None = None
    side_overlap: float | None = None
    strip_overlap: float | None = None
    line_spacing_m: float | None = None
    camera: CameraModel | None = None

    # derived (filled by the coverage engine)
    agl_m: float = 0.0
    gsd_cm_effective: float | None = None
    footprint_across_m: float | None = None
    footprint_along_m: float | None = None
    swath_spacing_m: float = 0.0
    photo_interval_m: float | None = None
    derivation: dict[str, Any] = field(default_factory=dict)

    @property
    def payload_class(self) -> str:
        return self.type


@dataclass
class SurveyJob:
    id: str
    survey_type: str
    payload_profile_id: str
    geom: BaseGeometry
    properties: dict[str, Any] = field(default_factory=dict)

    # derived
    effective_geom: BaseGeometry | None = None
    excluded_geom: BaseGeometry | None = None
    exclusion_reasons: list[str] = field(default_factory=list)

    @property
    def area_m2(self) -> float:
        return self.geom.area

    @property
    def effective_area_m2(self) -> float:
        g = self.effective_geom if self.effective_geom is not None else self.geom
        return g.area


@dataclass
class Zone:
    """No-fly zone, allowed airspace or 3D+time airspace constraint."""

    id: str
    geom: BaseGeometry
    kind: str  # 'no_fly_zone' | 'allowed_airspace' | 'airspace_constraint'
    hard: bool = True
    min_alt_m: float | None = None
    max_alt_m: float | None = None
    active_from: datetime | None = None
    active_to: datetime | None = None
    restriction_type: str | None = None

    @property
    def is_temporal(self) -> bool:
        return self.active_from is not None or self.active_to is not None

    def active_at(self, t: datetime) -> bool:
        if self.active_from is not None and t < self.active_from:
            return False
        if self.active_to is not None and t > self.active_to:
            return False
        return True

    def covers_window(self, start: datetime, end: datetime) -> bool:
        """True if the restriction is active during the whole mission window."""
        if self.active_from is None and self.active_to is None:
            return True
        af = self.active_from or start
        at = self.active_to or end
        return af <= start and at >= end

    def blocks_altitude(self, alt_amsl_m: float) -> bool:
        lo = self.min_alt_m if self.min_alt_m is not None else -math.inf
        hi = self.max_alt_m if self.max_alt_m is not None else math.inf
        return lo <= alt_amsl_m <= hi

    def permits_altitude(self, alt_amsl_m: float) -> bool:
        """For a max-altitude cap: altitudes above the cap are prohibited."""
        hi = self.max_alt_m if self.max_alt_m is not None else math.inf
        lo = self.min_alt_m if self.min_alt_m is not None else -math.inf
        return lo <= alt_amsl_m <= hi


@dataclass
class Obstacle:
    id: str
    geom: BaseGeometry
    height_m: float
    horizontal_buffer_m: float = 50.0
    vertical_buffer_m: float = DEFAULT_OBSTACLE_VERTICAL_BUFFER_M

    @property
    def protected_top_m(self) -> float:
        return self.height_m + self.vertical_buffer_m

    def protected_footprint(self) -> BaseGeometry:
        return self.geom.buffer(self.horizontal_buffer_m)


@dataclass
class Site:
    id: str
    point: Point
    role: str = "both"
    candidate: bool = False
    elevation_m: float | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def can_start(self) -> bool:
        return self.role in ("both", "start")

    @property
    def can_land(self) -> bool:
        return self.role in ("both", "landing", "reserve")

    @property
    def is_reserve(self) -> bool:
        return self.role == "reserve"

    @property
    def xy(self) -> tuple[float, float]:
        return (self.point.x, self.point.y)


@dataclass
class Uav:
    """A physical UAV instance available for the mission."""

    id: str
    model: str
    uav_class: str
    ground_speed_ms: float
    operational_endurance_min: float
    max_wind_ms: float
    payload_classes: list[str]
    energy_reserve_fraction: float = 0.2
    horizontal_separation_m: float = 100.0
    vertical_separation_m: float = 50.0
    turnaround_buffer_m: float = 50.0
    start_site: str | None = None
    landing_site: str | None = None
    takeoff_time_s: float = 60.0
    landing_time_s: float = 60.0
    service_time_s: float = 480.0
    cruise_agl_m: float = 150.0
    kb_model_ref: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def usable_endurance_s(self) -> float:
        """Endurance minus the mandatory energy reserve."""
        return self.operational_endurance_min * 60.0 * (1.0 - self.energy_reserve_fraction)

    @property
    def endurance_s(self) -> float:
        return self.operational_endurance_min * 60.0

    @property
    def is_fixed_wing(self) -> bool:
        return self.uav_class == "fixed_wing"

    def supports(self, payload_class: str) -> bool:
        return payload_class in self.payload_classes

    def travel_time_s(self, dist_m: float) -> float:
        return dist_m / self.ground_speed_ms


@dataclass
class Wind:
    speed_ms: float = 0.0
    direction_deg_from: float = 0.0

    @property
    def direction_deg_to(self) -> float:
        return (self.direction_deg_from + 180.0) % 360.0


@dataclass
class Mission:
    objectives: list[str] = field(default_factory=lambda: ["makespan"])
    window_start: datetime | None = None
    window_end: datetime | None = None
    daylight_start: datetime | None = None
    daylight_end: datetime | None = None
    wind: Wind = field(default_factory=Wind)
    dem_sampling_step_m: float = 30.0
    service_time_min: float | None = None
    require_schedule: bool = True
    require_complete_coverage: bool = True
    allow_different_start_end: bool = False
    candidate_sites_allowed: bool = False
    force_simultaneous_initial_departure: bool = False
    wind_energy_model_enabled: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def objective(self) -> str:
        return self.objectives[0] if self.objectives else "makespan"

    def effective_start(self) -> datetime:
        cands = [t for t in (self.window_start, self.daylight_start) if t is not None]
        return max(cands) if cands else datetime.now().astimezone()

    def effective_end(self) -> datetime:
        cands = [t for t in (self.window_end, self.daylight_end) if t is not None]
        return min(cands) if cands else self.effective_start() + timedelta(hours=8)


@dataclass
class Dem:
    """Terrain model sampler."""

    path: str | None = None
    sampler: Any = None  # gmp.io.dem.DemSampler
    nominal_elevation_m: float = 0.0

    def elevation(self, x: float, y: float) -> float:
        if self.sampler is None:
            return self.nominal_elevation_m
        return self.sampler.elevation(x, y)

    def max_elevation(self, geom: BaseGeometry) -> float:
        if self.sampler is None:
            return self.nominal_elevation_m
        return self.sampler.max_elevation(geom)

    @property
    def available(self) -> bool:
        return self.sampler is not None


@dataclass
class Scene:
    id: str
    crs: CrsPipeline
    jobs: list[SurveyJob] = field(default_factory=list)
    allowed_airspace: list[Zone] = field(default_factory=list)
    no_fly_zones: list[Zone] = field(default_factory=list)
    airspace_constraints: list[Zone] = field(default_factory=list)
    sites: list[Site] = field(default_factory=list)
    obstacles: list[Obstacle] = field(default_factory=list)
    fleet: list[Uav] = field(default_factory=list)
    payloads: dict[str, PayloadProfile] = field(default_factory=dict)
    mission: Mission = field(default_factory=Mission)
    dem: Dem = field(default_factory=Dem)
    source_dir: str | None = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def site(self, site_id: str) -> Site | None:
        for s in self.sites:
            if s.id == site_id:
                return s
        return None

    def uav(self, uav_id: str) -> Uav | None:
        for u in self.fleet:
            if u.id == uav_id:
                return u
        return None

    @property
    def start_sites(self) -> list[Site]:
        return [s for s in self.sites if s.can_start and not s.candidate]

    @property
    def landing_sites(self) -> list[Site]:
        return [s for s in self.sites if s.can_land and not s.candidate]

    @property
    def candidate_sites(self) -> list[Site]:
        return [s for s in self.sites if s.candidate]

    @property
    def hard_nfz(self) -> list[Zone]:
        return [z for z in self.no_fly_zones if z.hard]

    def job(self, job_id: str) -> SurveyJob | None:
        for j in self.jobs:
            if j.id == job_id:
                return j
        return None


# --------------------------------------------------------------------------- #
# Coverage / plan artefacts
# --------------------------------------------------------------------------- #
@dataclass
class Transect:
    """One clipped survey line inside the effective coverage area."""

    coords: list[tuple[float, float]]
    length_m: float
    job_id: str

    @property
    def start(self) -> tuple[float, float]:
        return self.coords[0]

    @property
    def end(self) -> tuple[float, float]:
        return self.coords[-1]

    def reversed_(self) -> "Transect":
        return Transect(coords=list(reversed(self.coords)), length_m=self.length_m, job_id=self.job_id)

    def line(self) -> LineString:
        return LineString(self.coords)


@dataclass
class AtomicTask:
    """Smallest re-assignable unit of survey work."""

    id: str
    job_id: str
    payload_class: str
    payload_profile_id: str
    agl_m: float
    transects: list[Transect]
    survey_length_m: float
    turn_count: int
    sweep_angle_deg: float
    entry: tuple[float, float]
    exit: tuple[float, float]
    geom: LineString
    internal_transition_m: float = 0.0
    fixed_wing_safe: bool = True
    notes: list[str] = field(default_factory=list)

    route_variants: list[dict[str, Any]] = field(default_factory=list)

    def path_coords(self, reverse: bool = False, variant: str = "primary") -> list[tuple[float, float]]:
        """Full in-task polyline. Transects already alternate direction (snake)."""
        if self.route_variants:
            route = next(v for v in self.route_variants if v["id"] == variant)
            coords = [tuple(p) for p in route["geom_coords"]]
            return list(reversed(coords)) if reverse else coords
        if variant != "primary":
            raise ValueError("Unknown route variant")
        seq = [t.reversed_() for t in reversed(self.transects)] if reverse else list(self.transects)
        out: list[tuple[float, float]] = []
        for t in seq:
            if out and out[-1] == t.coords[0]:
                out.extend(t.coords[1:])
            else:
                out.extend(t.coords)
        return out

    def endpoints(self, reverse: bool = False) -> tuple[tuple[float, float], tuple[float, float]]:
        c = self.path_coords(reverse=reverse)
        return c[0], c[-1]


@dataclass
class Waypoint:
    x: float
    y: float
    agl_m: float
    amsl_m: float
    t: datetime
    phase: str  # takeoff|transit|survey|turn|landing|hold
    task_id: str | None = None
    speed_ms: float = 0.0
    remaining_endurance_s: float = 0.0

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.amsl_m)


@dataclass
class Sortie:
    uav_id: str
    index: int
    start_site_id: str
    landing_site_id: str
    task_ids: list[str] = field(default_factory=list)
    task_reversed: list[bool] = field(default_factory=list)
    task_variants: list[str] = field(default_factory=list)
    t_start: datetime | None = None
    t_end: datetime | None = None
    flight_time_s: float = 0.0
    distance_m: float = 0.0
    survey_length_m: float = 0.0
    transit_length_m: float = 0.0
    waypoints: list[Waypoint] = field(default_factory=list)
    hold_s: float = 0.0
    transit_agl_m: float = 150.0
    energy: dict[str, float] = field(default_factory=dict)
    safety: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.uav_id}_S{self.index}"

    @property
    def duration_s(self) -> float:
        if self.t_start and self.t_end:
            return (self.t_end - self.t_start).total_seconds()
        return self.flight_time_s


@dataclass
class Plan:
    scene_id: str
    objective: str
    sorties: list[Sortie] = field(default_factory=list)
    tasks: dict[str, AtomicTask] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    status: str = "UNKNOWN"
    validation: dict[str, Any] | None = None
    certificate: dict[str, Any] | None = None
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    diagnosis: dict[str, Any] | None = None
    solver_log: list[dict[str, Any]] = field(default_factory=list)
    deconfliction: dict[str, Any] | None = None
    coverage: dict[str, Any] | None = None
    created_at: datetime | None = None

    @property
    def used_uavs(self) -> list[str]:
        return sorted({s.uav_id for s in self.sorties if s.task_ids})

    def sorties_of(self, uav_id: str) -> list[Sortie]:
        return [s for s in self.sorties if s.uav_id == uav_id]

    def makespan_s(self) -> float:
        starts = [s.t_start for s in self.sorties if s.t_start]
        ends = [s.t_end for s in self.sorties if s.t_end]
        if not starts or not ends:
            return 0.0
        return (max(ends) - min(starts)).total_seconds()

    def total_flight_s(self) -> float:
        return sum(s.flight_time_s for s in self.sorties)

    def total_distance_m(self) -> float:
        return sum(s.distance_m for s in self.sorties)


@dataclass
class Assignment:
    """Solver-level solution: ordered task lists per UAV sortie."""

    per_uav: dict[str, list[list[str]]] = field(default_factory=dict)

    def copy(self) -> "Assignment":
        return Assignment(per_uav={u: [list(t) for t in trips] for u, trips in self.per_uav.items()})

    def all_task_ids(self) -> list[str]:
        return [t for trips in self.per_uav.values() for trip in trips for t in trip]

    def task_count(self) -> int:
        return len(self.all_task_ids())


def sequence_length(coords: Sequence[tuple[float, float]]) -> float:
    return sum(math.dist(coords[i], coords[i + 1]) for i in range(len(coords) - 1))
