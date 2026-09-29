"""Per-module contract checkers (ARCHITECTURE M1–M13 + ACCEPTANCE)."""
from __future__ import annotations

import math
from typing import Any, Callable

from shapely.geometry import LineString, Point, shape
from shapely.geometry.base import BaseGeometry

from ..config import MIN_SAFE_AGL_M, SCHEMA_VERSION
from ..models import AtomicTask


class ContractViolation(AssertionError):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise ContractViolation(msg)


# --- M1 -----------------------------------------------------------------


def check_m1_scene(scene: Any) -> None:
    require(bool(scene.jobs), "M1: scene.jobs non-empty")
    require(bool(scene.sites), "M1: scene.sites non-empty")
    require(scene.crs is not None and scene.crs.metric_epsg, "M1: metric_epsg set")
    require(
        any(s.role in ("both", "start", "landing") for s in scene.sites),
        "M1: need site role start/landing/both",
    )
    for job in scene.jobs:
        require(job.geom.is_valid and not job.geom.is_empty, f"M1: job {job.id} geom invalid")


# --- M2 -----------------------------------------------------------------


def check_m2_roundtrip(pipeline: Any, lon: float, lat: float, tol_deg: float = 1e-6) -> None:
    x, y = pipeline.lonlat_to_xy(lon, lat)
    lon2, lat2 = pipeline.xy_to_lonlat(x, y)
    require(abs(lon2 - lon) < tol_deg and abs(lat2 - lat) < tol_deg, "M2: roundtrip deg")
    require(not (abs(x) <= 180.0 and abs(y) <= 90.0), "M2: coords look like lon/lat")
    err_m = pipeline.roundtrip_error_m(lon, lat)
    require(err_m < 1.0, f"M2: roundtrip {err_m}m not ≪ 1m")



# --- M3 -----------------------------------------------------------------


def check_m3_elevation(z: float) -> None:
    require(math.isfinite(z), "M3: elevation not finite")


# --- M4 -----------------------------------------------------------------


def check_m4_kb(kb: Any) -> None:
    ids = set(kb.model_ids())
    for mid in ("geoscan_201", "geoscan_401", "geoscan_701", "geoscan_801", "geoscan_gemini"):
        require(mid in ids, f"M4: missing {mid}")
    require(len(ids) >= 5, "M4: need >=5 MVP models")
    for mid in ("geoscan_201", "geoscan_401", "geoscan_701", "geoscan_801", "geoscan_gemini"):
        prov = kb.provenance_for(mid)
        require(bool(prov.get("source")), f"M4: provenance.source missing for {mid}")


# --- M5 -----------------------------------------------------------------


def check_m5_payload(profile: Any) -> None:
    require(profile.agl_m >= MIN_SAFE_AGL_M, "M5: agl < 40")
    require(profile.swath_spacing_m > 0, "M5: swath_spacing")


# --- M6 -----------------------------------------------------------------


def check_m6_coverage(result: Any, *, min_tasks: int = 1, min_pct: float = 99.0) -> None:
    require(len(result.tasks) >= min_tasks, "M6: task_count")
    require(result.coverage_percent + 1e-6 >= min_pct, f"M6: coverage {result.coverage_percent}")
    for t in result.tasks.values():
        require(t.survey_length_m > 0, f"M6: length {t.id}")
        require(all(abs(c[0]) > 180 or abs(c[1]) > 90 for c in t.transects[0].coords), "M6: metres")


# --- M7 -----------------------------------------------------------------


def check_m7_holes(transects: list[Any], holes: list[BaseGeometry]) -> None:
    for t in transects:
        mid = LineString(t.coords).interpolate(0.5, normalized=True)
        for hole in holes:
            require(not hole.contains(mid), "M7: midpoint inside hole")


# --- M8 -----------------------------------------------------------------


def check_m8_effective(effective: BaseGeometry, nfz_core: BaseGeometry) -> None:
    require(effective.intersection(nfz_core).area < 1e-3, "M8: effective intersects NFZ core")


def check_m8_g1(transects: list[Any], hard_nfz: BaseGeometry) -> None:
    if hard_nfz.is_empty:
        return
    for t in transects:
        require(not LineString(t.coords).intersects(hard_nfz), "M8/G1: transect hits hard NFZ")


# --- M9 -----------------------------------------------------------------


def check_m9_fw_eligibility(task: AtomicTask, eligible_fw_ids: list[str]) -> None:
    if not task.fixed_wing_safe:
        require(not eligible_fw_ids, "M9/G5: FW eligible on unsafe task")


# --- M10 ----------------------------------------------------------------


def check_m10_candidates(cands: list[Any], *, min_n: int = 2) -> None:
    require(len(cands) >= min_n, "M10: need >=2 candidates")
    angles = {round(c.angle_deg, 1) for c in cands}
    require(len(angles) >= 2, "M10: distinct angles")


# --- M11 ----------------------------------------------------------------


def check_m11_atomic(task: AtomicTask) -> None:
    require(task.transects, f"M11: empty transects {task.id}")
    require(task.entry == task.transects[0].coords[0], f"M11/G2 entry {task.id}")
    require(task.exit == task.transects[-1].coords[-1], f"M11/G2 exit {task.id}")
    total = sum(t.length_m for t in task.transects)
    require(abs(task.survey_length_m - total) < 1e-3, f"M11/G3 length {task.id}")


# --- M12 ----------------------------------------------------------------


def check_m12_feasibility(feas: Any, task_ids: list[str]) -> None:
    for tid in task_ids:
        require(tid in feas.eligible_uav_ids_by_task, f"M12: missing eligible for {tid}")


# --- M13 ----------------------------------------------------------------


def check_m13_bundle(data: dict[str, Any]) -> None:
    require(data.get("schema_version") == SCHEMA_VERSION, "M13/G7 schema")
    for key in ("scene_id", "crs", "fleet", "sites", "mission", "tasks", "feasibility", "warnings"):
        require(key in data, f"M13: missing {key}")
    ids = [t["id"] for t in data["tasks"]]
    require(len(ids) == len(set(ids)), "M13/G6 duplicate ids")


CHECKERS: dict[str, Callable[..., None]] = {
    "M1": check_m1_scene,
    "M2": check_m2_roundtrip,
    "M3": check_m3_elevation,
    "M4": check_m4_kb,
    "M5": check_m5_payload,
    "M6": check_m6_coverage,
    "M7": check_m7_holes,
    "M8": check_m8_effective,
    "M9": check_m9_fw_eligibility,
    "M10": check_m10_candidates,
    "M11": check_m11_atomic,
    "M12": check_m12_feasibility,
    "M13": check_m13_bundle,
}


def geojson_shape(obj: dict[str, Any]) -> BaseGeometry:
    return shape(obj)
