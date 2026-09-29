"""Authoritative H3 result gate, independent of planner and optimizer state."""

from __future__ import annotations

import hashlib
from importlib.metadata import version
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pyproj import Transformer
from pyproj.exceptions import ProjError
from shapely.errors import ShapelyError

from .h3_core import MODEL_SCOPE, check_result, compatible, load_scene

GATE_VERSION = "geoscan.h3.validator.v1"
INPUT_FILES = (
    "metadata.json", "mission.json", "fleet.json", "payload_catalog.json",
    "survey_areas.geojson", "allowed_airspace.geojson", "no_fly_zones.geojson",
    "temporal_airspace.geojson", "obstacles.geojson", "landing_sites.geojson", "dem.tif",
)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def input_fingerprint(directory: Path) -> tuple[str, dict[str, str]]:
    """Hash every required source byte, including DEM and all geometry inputs."""
    files = {}
    for name in INPUT_FILES:
        digest = hashlib.sha256()
        with (Path(directory) / name).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        files[name] = digest.hexdigest()
    return _digest(files), files


def recompute_metrics(input_dir: Path, result: dict) -> dict:
    """Construction helper, not a safety verdict: ignores only metric claims."""
    return check_result(Path(input_dir), result, check_claims=False)


def validate_result(input_dir: Path, result: dict, plan_id: str | None = None) -> dict:
    """Fail closed, and issue a hash-bound certificate only for verified SAFE."""
    try:
        before_hash, _ = input_fingerprint(Path(input_dir))
    except OSError:
        before_hash = None
    try:
        before_result_hash = _digest(result)
    except (ValueError, TypeError):
        before_result_hash = None
    report = check_result(Path(input_dir), result, check_claims=True)
    # The immutable dataset's 0.1% acceptance threshold is too loose for the
    # service's full-coverage promise. Allow only polygon arithmetic roundoff.
    report["coverage_policy"] = {
        "required_fraction": 1.0,
        "numerical_area_tolerance": "max(0.001 square metre, required_area * 1e-12)",
        "input_fractional_tolerance_does_not_relax_service_coverage": True,
    }
    if isinstance(result, dict) and result.get("status") == "SAFE":
        for job_id, row in report["metrics"].get("per_job", {}).items():
            missing = row["required_area_m2"] - row["covered_area_m2"]
            tolerance = max(0.001, row["required_area_m2"] * 1e-12)
            if missing > tolerance:
                report["passed"] = False
                report["violations"].append({"code": "COVERAGE_NOT_COMPLETE", "job_id": job_id,
                    "message": "Full original job coverage is required; input fractional tolerance cannot permit a partial mission",
                    "uncovered_area_m2": missing, "numerical_tolerance_m2": tolerance})
    claimed = result.get("status") if isinstance(result, dict) else None
    report["claimed_status"] = claimed
    report["status"] = claimed if report["passed"] and claimed in {"SAFE", "INFEASIBLE"} else "UNSAFE"
    report["certificate"] = None
    report["validator_version"] = GATE_VERSION
    report["validated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        input_hash, files = input_fingerprint(Path(input_dir))
        result_hash = _digest(result)
        if before_hash != input_hash:
            raise ValueError("Input changed during validation")
        if before_result_hash != result_hash:
            raise ValueError("Result changed during validation")
        report["input_sha256"] = input_hash
        report["result_sha256"] = result_hash
    except (OSError, ValueError, TypeError) as exc:
        report["passed"] = False
        report["status"] = "UNSAFE"
        report["violations"].append({"code": "FINGERPRINT_FAILED", "message": str(exc)})
        return report
    if report["passed"] and report["status"] == "SAFE":
        certificate = {
            "schema": "geoscan.h3.certificate.v1", "validator_version": GATE_VERSION,
            "validator_source_sha256": hashlib.sha256(Path(__file__).with_name("h3_core.py").read_bytes()).hexdigest(),
            "validator_gate_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "validator_geometry_source_sha256": hashlib.sha256(Path(__file__).with_name("trajectory_geometry.py").read_bytes()).hexdigest(),
            "software_versions": {"python": platform.python_version(), **{
                package: version(package) for package in ("numpy", "shapely", "pyproj", "rasterio")}},
            "issued_at": report["validated_at"], "plan_id": plan_id,
            "scene_id": result["scene_id"], "objective": result["objective"],
            "status": "SAFE", "input_sha256": input_hash, "input_files_sha256": files,
            "result_sha256": result_hash, "metrics": report["metrics"],
            "coverage_policy": report["coverage_policy"],
            "checks": ["input_schema", "original_required_coverage", "full_segment_airspace",
                       "raster_cell_surface_clearance", "continuous_temporal_airspace",
                       "continuous_pairwise_separation", "resource_and_landing_suffix",
                       "payload_gsd", "site_roles", "service_and_mission_windows",
                       "wind_limits", "declared_survey_turn_buffers", "metric_claims"],
            **MODEL_SCOPE,
        }
        certificate["fingerprint_sha256"] = _digest(certificate)
        report["certificate"] = certificate
    return report


def infer_infeasibility(input_dir: Path, objective: str) -> dict | None:
    """Try necessary-condition proofs, never infer impossibility from timeout."""
    try:
        scene = load_scene(Path(input_dir))
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OSError, ProjError, ShapelyError):
        return None
    unproject = Transformer.from_crs(scene.metadata["metric_crs"], "EPSG:4326", always_xy=True)
    for job_id, job in scene.jobs.items():
        available = [u for u in scene.fleet.values() if compatible(scene, job, u)]
        eligible = [u for u in available if scene.mission["wind"]["speed_ms"] <= u["max_wind_ms"]]
        if not available:
            proof = {"type": "no_compatible_uav", "job_id": job_id}
        elif not eligible:
            proof = {"type": "wind_excludes_all", "job_id": job_id}
        else:
            witness = job["geometry"].representative_point()
            proof = {"type": "unreachable_required_point", "job_id": job_id,
                     "point": list(unproject.transform(witness.x, witness.y))}
        candidate = {
            "schema": "geoscan.h3.result.v1", "scene_id": scene.metadata["scenario_id"],
            "objective": objective, "status": "INFEASIBLE", "optimality": {"status": "unknown"},
            "metrics": {}, "sorties": [],
            "diagnosis": {"proof": proof, "reason_codes": [proof["type"]],
                          "scope": "Necessary-condition proof within the declared input model"},
        }
        report = validate_result(input_dir, candidate)
        if report["passed"]:
            candidate["metrics"] = report["metrics"]
            return candidate
    return None
