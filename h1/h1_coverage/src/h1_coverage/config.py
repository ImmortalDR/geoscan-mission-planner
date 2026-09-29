"""H1 coverage configuration — no magic numbers scattered in algorithms."""
from __future__ import annotations

from dataclasses import dataclass

SCHEMA_VERSION = "gmp.h1_h2.v2"
MIN_SAFE_AGL_M = 40.0
NFZ_PLANNING_BUFFER_M = 30.0
ALLOWED_INSET_M = 20.0
COVERAGE_PASS_PERCENT = 99.9
MAX_TASKS_PER_JOB = 80
DEFAULT_ANGLE_STEP_DEG = 15.0
DEFAULT_KEEP_CANDIDATES = 4
MANEUVER_VIOLATION_PENALTY = 50_000.0
MVP_UAV_MODELS = (
    "geoscan_201",
    "geoscan_401",
    "geoscan_401_geo",  # seed alias
    "geoscan_701",
    "geoscan_801",
    "geoscan_gemini",
)


@dataclass(frozen=True)
class CoverageConfig:
    nfz_buffer_m: float = NFZ_PLANNING_BUFFER_M
    allowed_inset_m: float = ALLOWED_INSET_M
    coverage_pass_percent: float = COVERAGE_PASS_PERCENT
    angle_step_deg: float = DEFAULT_ANGLE_STEP_DEG
    keep_candidates: int = DEFAULT_KEEP_CANDIDATES
    max_tasks_per_job: int = MAX_TASKS_PER_JOB
    strict_coverage: bool = True
    crosswind_weight: float = 1.0
    headland_m: float = 0.0  # optional DIY headland (large scenes)
    terrain_strict: bool = False  # A-05: treat LOCAL_AGL_BELOW_MIN as hard fail note
