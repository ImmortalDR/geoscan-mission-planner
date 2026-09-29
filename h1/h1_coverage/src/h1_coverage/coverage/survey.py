"""M5 SurveyModel — contract h1.m5.survey.v1

GSD / overlap / camera → agl_m (≥40) and swath_spacing_m.
Formulas aligned with QGroundControl survey intuition.
"""
from __future__ import annotations

from ..config import MIN_SAFE_AGL_M
from ..models import PayloadProfile


def derive_payload_geometry(profile: PayloadProfile) -> list[str]:
    """Mutate profile in place. Returns warnings (e.g. AGL raised to MIN_SAFE)."""
    warnings: list[str] = []
    cam = profile.camera
    if profile.type in ("lidar", "geophysics") and profile.line_spacing_m:
        profile.agl_m = max(profile.nominal_agl_m, MIN_SAFE_AGL_M)
        profile.swath_spacing_m = float(profile.line_spacing_m)
        return warnings

    if cam is None:
        profile.agl_m = max(profile.nominal_agl_m, MIN_SAFE_AGL_M)
        profile.swath_spacing_m = max(profile.line_spacing_m or 40.0, 1.0)
        return warnings

    if profile.gsd_cm is not None:
        agl = cam.agl_for_gsd(profile.gsd_cm / 100.0)
    else:
        agl = profile.nominal_agl_m
    if agl < MIN_SAFE_AGL_M:
        warnings.append(
            f"M5: payload {profile.id} GSD-derived AGL {agl:.1f}m < {MIN_SAFE_AGL_M}m — raised"
        )
        agl = MIN_SAFE_AGL_M
    fw, fh = cam.footprint(agl)
    side = profile.side_overlap if profile.side_overlap is not None else 0.3
    profile.agl_m = agl
    profile.footprint_across_m = fw
    profile.footprint_along_m = fh
    profile.swath_spacing_m = fw * (1.0 - side)
    return warnings


def derive_all(payloads: dict[str, PayloadProfile]) -> list[str]:
    warnings: list[str] = []
    for p in payloads.values():
        warnings.extend(derive_payload_geometry(p))
    return warnings
