"""Safety Certificate issued only by the independent, hash-bound H3 gate."""

from __future__ import annotations

from typing import Any

from ..models import Plan, Scene
from .validator import SAFE, ValidationReport

CERTIFICATE_VERSION = "geoscan.h3.certificate.v1"


def issue_certificate(scene: Scene, plan: Plan, report: ValidationReport) -> dict[str, Any] | None:
    """Never upgrade the legacy sampled diagnostics into a safety certificate."""
    independent = report.checks.get("independent_h3", {})
    if report.status != SAFE or independent.get("passed") is not True or independent.get("status") != SAFE:
        return None
    from .model_adapter import validate_model_plan

    current = validate_model_plan(scene, plan)
    if not current.get("passed") or current.get("status") != SAFE:
        return None
    if any(current.get(key) != independent.get(key) for key in ("input_sha256", "result_sha256")):
        return None
    return current.get("certificate")
