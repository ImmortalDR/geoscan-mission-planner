#!/usr/bin/env python3
"""Validate H1↔H2 bundle fixtures against gmp.h1_h2.v1 hard contract.

Exit 0 = OK, 1 = violations. Safe to run with zero fixture files (exit 0 + note).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

SCHEMA_VERSION = "gmp.h1_h2.v1"
PAYLOAD_CLASSES = {
    "rgb",
    "multispectral",
    "thermal",
    "lidar",
    "geophysics",
    "rgb_video",
}
OBJECTIVES = {"makespan", "total_flight"}
UAV_CLASSES = {"fixed_wing", "multirotor", "vtol"}
SITE_ROLES = {"both", "start", "landing", "reserve"}
MIN_AGL = 40.0

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIRS = [
    ROOT / "fixtures" / "h1_h2",
    ROOT.parent / "h1" / "fixtures" / "h1_h2",
]


def dist(a: list[float], b: list[float]) -> float:
    return math.dist(a, b)


def poly_length(coords: list[list[float]]) -> float:
    return sum(dist(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def fail(errors: list[str], msg: str) -> None:
    errors.append(msg)


def validate_bundle(path: Path, errors: list[str]) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(errors, f"{path}: JSON parse error: {exc}")
        return

    if data.get("schema_version") in ("gmp.h1_h2.v2", "gmp.h1_h2.v3"):
        sys.path.insert(0, str(ROOT / "h2" / "src"))
        from h2.contract import validate_route_variants
        try:
            for task in data.get("tasks", []):
                validate_route_variants(task)
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            fail(errors, f"{path}: {exc}")
    if data.get('schema_version') == 'gmp.h1_h2.v3':
        from h2.contract import validate_bundle as validate_v3
        try:
            validate_v3(data)
        except (ValueError,TypeError,KeyError,IndexError) as exc:
            fail(errors, f'{path}: {exc}')
    pref = str(path)
    if data.get("schema_version") not in (SCHEMA_VERSION, "gmp.h1_h2.v2", "gmp.h1_h2.v3"):
        fail(errors, f"{pref}: schema_version must be {SCHEMA_VERSION!r}")

    for key in (
        "scene_id",
        "crs",
        "generated_at",
        "source",
        "fleet",
        "sites",
        "mission",
        "tasks",
        "feasibility",
        "warnings",
    ):
        if key not in data:
            fail(errors, f"{pref}: missing required key {key!r}")

    if not isinstance(data.get("tasks"), list):
        return

    crs = data.get("crs") or {}
    if "metric_epsg" not in crs:
        fail(errors, f"{pref}: crs.metric_epsg required")

    fleet = data.get("fleet") or []
    sites = data.get("sites") or []
    uav_ids = set()
    site_ids = set()

    for u in fleet:
        uid = u.get("id")
        if not uid or uid in uav_ids:
            fail(errors, f"{pref}: bad/duplicate uav id {uid!r}")
        uav_ids.add(uid)
        if u.get("uav_class") not in UAV_CLASSES:
            fail(errors, f"{pref}: uav {uid} bad uav_class")
        if float(u.get("ground_speed_ms", 0)) <= 0:
            fail(errors, f"{pref}: uav {uid} ground_speed_ms must be > 0")
        if float(u.get("cruise_agl_m", 0)) < MIN_AGL:
            fail(errors, f"{pref}: uav {uid} cruise_agl_m < {MIN_AGL}")
        pcs = u.get("payload_classes") or []
        if not pcs:
            fail(errors, f"{pref}: uav {uid} payload_classes empty")

    for s in sites:
        sid = s.get("id")
        if not sid or sid in site_ids:
            fail(errors, f"{pref}: bad/duplicate site id {sid!r}")
        site_ids.add(sid)
        if s.get("role") not in SITE_ROLES:
            fail(errors, f"{pref}: site {sid} bad role")
        if "x" not in s or "y" not in s:
            fail(errors, f"{pref}: site {sid} needs x,y metres")

    can_start = any(s.get("role") in ("both", "start") for s in sites)
    can_land = any(s.get("role") in ("both", "landing", "reserve") for s in sites)
    if sites and (not can_start or not can_land):
        fail(errors, f"{pref}: need ≥1 start-capable and ≥1 land-capable site")

    mission = data.get("mission") or {}
    for obj in mission.get("objectives") or []:
        if obj not in OBJECTIVES:
            fail(errors, f"{pref}: objective {obj!r} not in {sorted(OBJECTIVES)} for v1")

    feas = data.get("feasibility") or {}
    eligible = feas.get("eligible_uav_ids_by_task") or {}
    task_ids: set[str] = set()

    for t in data["tasks"]:
        tid = t.get("id")
        if not tid or tid in task_ids:
            fail(errors, f"{pref}: bad/duplicate task id {tid!r}")
        task_ids.add(tid)

        if t.get("payload_class") not in PAYLOAD_CLASSES:
            fail(errors, f"{pref}: task {tid} bad payload_class")
        if float(t.get("agl_m", 0)) < MIN_AGL:
            fail(errors, f"{pref}: task {tid} agl_m < {MIN_AGL}")

        transects = t.get("transects") or []
        if not transects:
            fail(errors, f"{pref}: task {tid} needs ≥1 transect")
            continue

        first = transects[0].get("coords") or []
        last = transects[-1].get("coords") or []
        if len(first) < 2 or len(last) < 2:
            fail(errors, f"{pref}: task {tid} transect coords len < 2")
            continue

        entry = t.get("entry")
        exit_ = t.get("exit")
        if entry != first[0]:
            fail(errors, f"{pref}: task {tid} entry != first transect start")
        if exit_ != last[-1]:
            fail(errors, f"{pref}: task {tid} exit != last transect end")

        length_sum = sum(float(tr.get("length_m", 0)) for tr in transects)
        declared = float(t.get("survey_length_m", -1))
        if abs(length_sum - declared) > 1.0:
            fail(
                errors,
                f"{pref}: task {tid} survey_length_m {declared} != sum transects {length_sum}",
            )

        for tr in transects:
            coords = tr.get("coords") or []
            if len(coords) < 2:
                fail(errors, f"{pref}: task {tid} empty transect")
                continue
            approx = poly_length(coords)
            if abs(approx - float(tr.get("length_m", 0))) > 1.0:
                fail(errors, f"{pref}: task {tid} transect length mismatch")

        if tid not in eligible:
            fail(errors, f"{pref}: feasibility missing task {tid}")
        else:
            for uid in eligible[tid]:
                if uid not in uav_ids:
                    fail(errors, f"{pref}: eligible uav {uid} unknown for {tid}")

    epsg = crs.get("metric_epsg")
    if epsg in (4326, "4326", "EPSG:4326"):
        fail(errors, f"{pref}: crs.metric_epsg must be projected metres, not 4326")


def main() -> int:
    paths: list[Path] = []
    for d in FIXTURE_DIRS:
        if d.is_dir():
            paths.extend(sorted(d.glob("*.json")))

    # also allow CLI args
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.is_file():
            paths.append(p)

    errors: list[str] = []
    if not paths:
        print(f"OK: no bundles found under {[str(d) for d in FIXTURE_DIRS]} (nothing to validate)")
        return 0

    for path in paths:
        validate_bundle(path, errors)

    if errors:
        print("H1↔H2 CONTRACT VIOLATIONS:")
        for e in errors:
            print(f"  - {e}")
        print("See docs/contract/H1_H2.md — fix or open contract/h1-h2-vN PR.")
        return 1

    print(f"OK: {len(paths)} bundle(s) match supported H1/H2 contracts (v1/v2/v3)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
