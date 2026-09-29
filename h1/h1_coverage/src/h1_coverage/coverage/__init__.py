"""Coverage package — M5–M11."""

from .atomic import build_atomic_tasks, chunk_transects
from .candidates import build_candidates
from .decomp import decompose_area, extract_holes
from .engine import CoverageResult, build_coverage
from .exclusions import apply_exclusions, compute_effective_area
from .fixed_wing import maneuver_violations
from .survey import derive_all, derive_payload_geometry
from .sweep import generate_transects

__all__ = [
    "CoverageResult",
    "apply_exclusions",
    "build_atomic_tasks",
    "build_candidates",
    "build_coverage",
    "chunk_transects",
    "compute_effective_area",
    "decompose_area",
    "derive_all",
    "derive_payload_geometry",
    "extract_holes",
    "generate_transects",
    "maneuver_violations",
]
