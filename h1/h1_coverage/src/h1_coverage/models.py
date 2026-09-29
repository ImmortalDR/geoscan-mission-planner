"""Domain models for H1 (internal). Wire format for H2 lives in bundle.py."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from .config import MIN_SAFE_AGL_M
from .geo import CrsPipeline


@dataclass
class CameraModel:
    id: str
    image_width_px: int
    image_height_px: int
    focal_length_mm: float
    pixel_pitch_um: float

    @property
    def sensor_width_mm(self) -> float:
        return self.image_width_px * self.pixel_pitch_um / 1000.0

    def agl_for_gsd(self, gsd_m: float) -> float:
        return gsd_m * self.focal_length_mm / (self.pixel_pitch_um / 1000.0)

    def gsd_for_agl(self, agl_m: float) -> float:
        return agl_m * (self.pixel_pitch_um / 1000.0) / self.focal_length_mm

    def footprint(self, agl_m: float) -> tuple[float, float]:
        w = self.sensor_width_mm * agl_m / self.focal_length_mm
        h = (self.image_height_px * self.pixel_pitch_um / 1000.0) * agl_m / self.focal_length_mm
        return w, h


@dataclass
class PayloadProfile:
    id: str
    type: str
    nominal_agl_m: float
    gsd_cm: float | None = None
    side_overlap: float | None = 0.7
    front_overlap: float | None = 0.8
    line_spacing_m: float | None = None
    camera: CameraModel | None = None
    agl_m: float = 0.0
    swath_spacing_m: float = 0.0
    footprint_across_m: float | None = None
    footprint_along_m: float | None = None


@dataclass
class Wind:
    speed_ms: float = 0.0
    direction_deg_from: float = 0.0


@dataclass
class Mission:
    objectives: list[str] = field(default_factory=lambda: ["makespan", "total_flight"])
    wind: Wind = field(default_factory=Wind)
    window_start: datetime | None = None
    window_end: datetime | None = None
    require_complete_coverage: bool = True
    require_schedule: bool = True
    allow_different_start_end: bool = False


@dataclass
class Zone:
    id: str
    geom: BaseGeometry
    kind: str
    hard: bool = True


@dataclass
class Site:
    id: str
    point: Point
    role: str = "both"
    candidate: bool = False

    @property
    def can_start(self) -> bool:
        return self.role in ("both", "start")

    @property
    def can_land(self) -> bool:
        return self.role in ("both", "landing", "reserve")


@dataclass
class Uav:
    id: str
    model: str
    uav_class: str
    ground_speed_ms: float
    operational_endurance_min: float
    max_wind_ms: float
    payload_classes: list[str]
    energy_reserve_fraction: float = 0.2
    horizontal_separation_m: float = 50.0
    vertical_separation_m: float = 30.0
    turnaround_buffer_m: float = 50.0
    start_site: str | None = None
    landing_site: str | None = None
    takeoff_time_s: float = 60.0
    landing_time_s: float = 60.0
    service_time_s: float = 480.0
    cruise_agl_m: float = 120.0

    @property
    def is_fixed_wing(self) -> bool:
        return self.uav_class == "fixed_wing"

    @property
    def usable_endurance_s(self) -> float:
        return self.operational_endurance_min * 60.0 * (1.0 - self.energy_reserve_fraction)

    def supports(self, payload_class: str) -> bool:
        return payload_class in self.payload_classes


@dataclass
class SurveyJob:
    id: str
    survey_type: str
    payload_profile_id: str
    geom: BaseGeometry
    effective_geom: BaseGeometry | None = None
    mode: str = "area"  # "area" | "corridor"


@dataclass
class Dem:
    path: str | None = None
    sampler: Any = None
    nominal_elevation_m: float = 0.0

    @property
    def available(self) -> bool:
        return self.sampler is not None

    def elevation(self, x: float, y: float) -> float:
        if self.sampler is None:
            return self.nominal_elevation_m
        return float(self.sampler.elevation(x, y))


@dataclass
class Scene:
    id: str
    crs: CrsPipeline
    jobs: list[SurveyJob]
    fleet: list[Uav]
    sites: list[Site]
    payloads: dict[str, PayloadProfile]
    mission: Mission
    allowed_airspace: list[Zone] = field(default_factory=list)
    no_fly_zones: list[Zone] = field(default_factory=list)
    obstacles: list[Zone] = field(default_factory=list)
    temporal_airspace: list[Zone] = field(default_factory=list)
    dem: Dem = field(default_factory=Dem)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Transect:
    coords: list[tuple[float, float]]
    length_m: float
    job_id: str

    @property
    def start(self) -> tuple[float, float]:
        return self.coords[0]

    @property
    def end(self) -> tuple[float, float]:
        return self.coords[-1]

    def line(self) -> LineString:
        return LineString(self.coords)


@dataclass
class AtomicTask:
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
    geom_coords: list[tuple[float, float]]
    internal_transition_m: float = 0.0
    fixed_wing_safe: bool = True
    notes: list[str] = field(default_factory=list)

    alternate_fixed_wing_safe: bool = False

    @property
    def route_variants(self) -> list[dict]:
        """Same survey lines; flip each line to connect their opposite ends."""
        from .route_variants import build_route_variants
        return build_route_variants(self.transects, self.fixed_wing_safe,
                                    self.alternate_fixed_wing_safe)

    def __post_init__(self) -> None:
        if self.agl_m < MIN_SAFE_AGL_M:
            self.agl_m = MIN_SAFE_AGL_M
