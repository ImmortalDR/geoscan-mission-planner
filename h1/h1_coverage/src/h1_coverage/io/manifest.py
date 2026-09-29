"""Canonical inventory of H1 SceneLoader inputs (M1).

Used by ``inspect_scene_dir``, CLI ``inspect``, and docs ``docs/inputs.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Presence = Literal["required", "required_alt", "optional"]


@dataclass(frozen=True)
class SceneInputSpec:
    key: str
    filename: str
    presence: Presence
    role: str
    notes: str = ""


# Directory layout = customer conformance pack (same names as gmp datasets).
SCENE_INPUTS: tuple[SceneInputSpec, ...] = (
    SceneInputSpec(
        "survey_areas",
        "survey_areas.geojson",
        "required_alt",
        "survey geometry (Polygon / MultiPolygon / LineString→corridor)",
        "Primary. Alt: scene.kml placemarks with style=survey",
    ),
    SceneInputSpec(
        "scene_kml",
        "scene.kml",
        "optional",
        "KML companion / survey+sites fallback",
        "Used for survey when GeoJSON absent; sites when landing_sites absent",
    ),
    SceneInputSpec(
        "landing_sites",
        "landing_sites.geojson",
        "required_alt",
        "start / landing / reserve Points",
        "≥1 site with role start|landing|both. Alt: Point placemarks in scene.kml",
    ),
    SceneInputSpec(
        "no_fly_zones",
        "no_fly_zones.geojson",
        "optional",
        "hard/soft NFZ polygons",
        "property hard=true|false; hard cut from effective (G1)",
    ),
    SceneInputSpec(
        "allowed_airspace",
        "allowed_airspace.geojson",
        "optional",
        "clip survey to allowed airspace",
        "",
    ),
    SceneInputSpec(
        "obstacles",
        "obstacles.geojson",
        "optional",
        "obstacle footprints (soft exclusion + buffer)",
        "height_m / horizontal_buffer_m used when present",
    ),
    SceneInputSpec(
        "temporal_airspace",
        "temporal_airspace.geojson",
        "optional",
        "time-bounded airspace constraints",
        "Loaded + warned; H1 MVP does not schedule around windows (H2/H3)",
    ),
    SceneInputSpec(
        "dem",
        "dem.tif",
        "optional",
        "GeoTIFF DEM for AGL / terrain",
        "Absent → flat terrain + warning",
    ),
    SceneInputSpec(
        "mission",
        "mission.json",
        "optional",
        "wind, objectives, mission_window, flags",
        "objectives ∈ {makespan, total_flight}",
    ),
    SceneInputSpec(
        "fleet",
        "fleet.json",
        "optional",
        "UAV instances → KB resolve",
        "models: geoscan_201|401|701|801|gemini",
    ),
    SceneInputSpec(
        "payload_catalog",
        "payload_catalog.json",
        "optional",
        "payload profiles (GSD / overlap / AGL)",
        "",
    ),
    SceneInputSpec(
        "metadata",
        "metadata.json",
        "optional",
        "scenario_id and labels",
        "",
    ),
    SceneInputSpec(
        "expected_assertions",
        "expected_assertions.json",
        "optional",
        "conformance expectations (tests only)",
        "Not consumed by coverage runtime",
    ),
)


ENTRY_POINTS: tuple[tuple[str, str], ...] = (
    ("CLI", "h1-coverage run --scene <dir> --output <bundle.json>"),
    ("CLI", "h1-coverage inspect --scene <dir>"),
    ("CLI", "h1-coverage export-fixtures --out-dir fixtures/h1_h2"),
    ("Python", "h1_coverage.pipeline.run_h1(scene_dir)"),
    ("Python", "h1_coverage.pipeline.run_h1_scene(scene)"),
    ("Python", "h1_coverage.io.scene.load_scene(dir)"),
    ("Python", "h1_coverage.io.inspect.inspect_scene_dir(dir)"),
    ("gmp bridge", "gmp.coverage.engine.build_coverage(scene) → run_h1(scene.source_dir)"),
    ("gmp shim", "gmp.h1.run_h1 / gmp cli coverage"),
)


SURVEY_GEOMETRY_KINDS: tuple[str, ...] = (
    "Polygon",
    "MultiPolygon",
    "LineString (→ corridor buffer via half_width_m)",
)
