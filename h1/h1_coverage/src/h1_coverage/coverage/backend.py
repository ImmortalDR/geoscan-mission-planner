"""M6 CoverageEngine backends — DIY lawnmower is the production path.

ARCHITECTURE (§4.5 / §14, updated): Fields2Cover remains an *optional future*
native dependency. Until a real Shapely↔F2CField bridge lands, we never advertise
`fields2cover` as the active generator while still running DIY geometry.
"""
from __future__ import annotations

from typing import Protocol

from shapely.geometry.base import BaseGeometry

from ..models import Transect
from .sweep import generate_transects


class SweepBackend(Protocol):
    name: str

    def generate(
        self,
        area: BaseGeometry,
        spacing: float,
        angle_deg: float,
        job_id: str,
        overshoot_m: float = 15.0,
    ) -> list[Transect]: ...


class DiySweepBackend:
    """Production MVP backend — Shapely parallel-swath lawnmower."""

    name = "diy_lawnmower_v1"

    def generate(
        self,
        area: BaseGeometry,
        spacing: float,
        angle_deg: float,
        job_id: str,
        overshoot_m: float = 15.0,
    ) -> list[Transect]:
        return generate_transects(area, spacing, angle_deg, job_id, overshoot_m=overshoot_m)


class Fields2CoverBackend:
    """Reserved adapter — raises until Shapely↔F2C geometry bridge is wired.

    Importability of the native `fields2cover` package is *not* sufficient to
    claim F2C coverage. Callers that need DIY must use `DiySweepBackend`.
    """

    name = "fields2cover"

    def __init__(self) -> None:
        import fields2cover as f2c  # noqa: F401

        self._f2c = f2c

    def generate(
        self,
        area: BaseGeometry,
        spacing: float,
        angle_deg: float,
        job_id: str,
        overshoot_m: float = 15.0,
    ) -> list[Transect]:
        raise NotImplementedError(
            "Fields2Cover geometry bridge is not wired (TODO: F2CField/F2CSwath). "
            "Use DiySweepBackend / get_sweep_backend() for production coverage."
        )


def fields2cover_importable() -> bool:
    try:
        import fields2cover  # noqa: F401

        return True
    except Exception:
        return False


_DEFAULT_BACKEND: SweepBackend = DiySweepBackend()


def get_sweep_backend() -> SweepBackend:
    return _DEFAULT_BACKEND


def set_sweep_backend(backend: SweepBackend) -> None:
    global _DEFAULT_BACKEND
    _DEFAULT_BACKEND = backend


def backend_status() -> dict[str, str | bool]:
    active = get_sweep_backend().name
    importable = fields2cover_importable()
    if active == "fields2cover":
        note = "Fields2Cover selected — generate() will raise until bridge is wired"
    elif importable:
        note = "DIY lawnmower active; fields2cover importable but bridge unwired (honest)"
    else:
        note = "DIY lawnmower (production path; optional fields2cover not installed)"
    return {
        "active": active,
        "fields2cover_importable": importable,
        "fields2cover_bridge_wired": False,
        "note": note,
    }
