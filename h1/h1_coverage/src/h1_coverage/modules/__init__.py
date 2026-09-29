"""H1 module registry — one entry per ARCHITECTURE M1–M13."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleSpec:
    id: str
    contract_id: str
    title: str
    code: tuple[str, ...]
    public_api: str
    pytest_node: str
    fixtures: tuple[str, ...]


MODULES: tuple[ModuleSpec, ...] = (
    ModuleSpec(
        "M1",
        "h1.m1.scene_loader.v1",
        "SceneLoader",
        ("h1_coverage.io.scene", "h1_coverage.io.kml"),
        "h1_coverage.io.scene:load_scene",
        "tests/test_m01_scene_loader.py",
        ("S00_smoke_rgb",),
    ),
    ModuleSpec(
        "M2",
        "h1.m2.geo_projector.v1",
        "GeoProjector",
        ("h1_coverage.geo",),
        "h1_coverage.geo:CrsPipeline",
        "tests/test_m02_geo.py",
        ("fixtures/h1_modules/m2_roundtrip_points.json",),
    ),
    ModuleSpec(
        "M3",
        "h1.m3.terrain.v1",
        "TerrainModel",
        ("h1_coverage.io.dem",),
        "h1_coverage.io.dem:DemSampler",
        "tests/test_m03_terrain.py",
        ("S00_smoke_rgb/dem.tif",),
    ),
    ModuleSpec(
        "M4",
        "h1.m4.uav_kb.v1",
        "UAVKnowledgeBase",
        ("h1_coverage.kb.catalog", "h1_coverage.kb.seed.yaml"),
        "h1_coverage.kb.catalog:KnowledgeBase",
        "tests/test_m04_kb.py",
        ("h1_coverage/kb/seed.yaml",),
    ),
    ModuleSpec(
        "M5",
        "h1.m5.survey.v1",
        "SurveyModel",
        ("h1_coverage.coverage.survey",),
        "h1_coverage.coverage.survey:derive_payload_geometry",
        "tests/test_m05_survey.py",
        ("fixtures/h1_modules/m5_payload_rgb.json",),
    ),
    ModuleSpec(
        "M6",
        "h1.m6.coverage_engine.v1",
        "CoverageEngine",
        ("h1_coverage.coverage.engine", "h1_coverage.coverage.sweep", "h1_coverage.coverage.backend"),
        "h1_coverage.coverage.engine:build_coverage",
        "tests/test_m06_coverage.py",
        ("S00_smoke_rgb",),
    ),
    ModuleSpec(
        "M7",
        "h1.m7.decomp_holes.v1",
        "DecompositionEngine",
        ("h1_coverage.coverage.decomp", "h1_coverage.coverage.sweep"),
        "h1_coverage.coverage.decomp:decompose_area",
        "tests/test_m07_decomp.py",
        ("S02_multipolygon_holes",),
    ),
    ModuleSpec(
        "M8",
        "h1.m8.nfz.v1",
        "NFZManager",
        ("h1_coverage.coverage.exclusions",),
        "h1_coverage.coverage.exclusions:compute_effective_area",
        "tests/test_m08_nfz.py",
        ("S08_fixed_wing_turnaround", "fixtures/h1_modules/m8_nfz_metric.json"),
    ),
    ModuleSpec(
        "M9",
        "h1.m9.fixed_wing.v1",
        "FixedWingBuffer",
        ("h1_coverage.coverage.fixed_wing",),
        "h1_coverage.coverage.fixed_wing:maneuver_violations",
        "tests/test_m09_fixed_wing.py",
        ("S08_fixed_wing_turnaround",),
    ),
    ModuleSpec(
        "M10",
        "h1.m10.candidates.v1",
        "CoverageCandidateGenerator",
        ("h1_coverage.coverage.candidates",),
        "h1_coverage.coverage.candidates:build_candidates",
        "tests/test_m10_candidates.py",
        ("S09_wind_feasibility",),
    ),
    ModuleSpec(
        "M11",
        "h1.m11.atomic_task.v1",
        "AtomicTaskBuilder",
        ("h1_coverage.coverage.atomic",),
        "h1_coverage.coverage.atomic:build_atomic_tasks",
        "tests/test_m11_atomic.py",
        ("S00_smoke_rgb",),
    ),
    ModuleSpec(
        "M12",
        "h1.m12.feasibility.v1",
        "FeasibilityMatrix",
        ("h1_coverage.feasibility",),
        "h1_coverage.feasibility:build_feasibility",
        "tests/test_m12_feasibility.py",
        ("S09_wind_feasibility", "S11_payload_compatibility"),
    ),
    ModuleSpec(
        "M13",
        "h1.m13.bundle.v1",
        "BundleExporter",
        ("h1_coverage.bundle",),
        "h1_coverage.bundle:export_bundle",
        "tests/test_m13_bundle.py",
        ("gmp.h1_h2.v1",),
    ),
)


def by_id(module_id: str) -> ModuleSpec:
    for m in MODULES:
        if m.id == module_id:
            return m
    raise KeyError(module_id)


def contract_ids() -> list[str]:
    return [m.contract_id for m in MODULES]
