"""Coverage package — thin bridge to canonical ``h1_coverage`` (no local algo)."""

from .engine import CoverageResult, _eligible_uavs, build_coverage, coverage_geometry, coverage_half_width_m

__all__ = [
    "CoverageResult",
    "build_coverage",
    "coverage_geometry",
    "coverage_half_width_m",
    "_eligible_uavs",
]
