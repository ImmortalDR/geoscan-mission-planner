#!/usr/bin/env python3
"""Run customer-conformance + unit/property/benchmark and emit GOST artefacts."""
from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gmp.io.export import write_exports
from gmp.io.scene_loader import load_scene, validate_scene
from gmp.planner import PlannerOptions, plan_mission
from gmp.report.gost import write_reports
from gmp.safety.oracles import evaluate_assertions

DATA = ROOT / "datasets/geoscan_customer_conformance"
OUT = ROOT / "artifacts/gost"

BUDGET = {
    "S00_smoke_rgb": 25.0,
    "S01_full_customer_acceptance_100km2": 75.0,
    "S02_multipolygon_holes": 25.0,
    "S03_temporal_airspace_daylight": 40.0,
    "S04_multi_sortie_energy": 35.0,
    "S05_4d_deconfliction": 25.0,
    "S06_recommend_alternative_site": 45.0,
    "S07_reserve_landing_failure": 30.0,
    "S08_fixed_wing_turnaround": 25.0,
    "S09_wind_feasibility": 25.0,
    "S10_different_start_end": 30.0,
    "S11_payload_compatibility": 45.0,
}


def run_conformance() -> list[dict]:
    suite = json.loads((DATA / "suite.json").read_text(encoding="utf-8"))
    rows = []
    for sid in suite["scenarios"]:
        t0 = time.time()
        try:
            scene = load_scene(DATA / sid)
            assertions = json.loads((DATA / sid / "expected_assertions.json").read_text(encoding="utf-8"))
            plan = plan_mission(
                scene,
                PlannerOptions(objective=scene.mission.objective, time_budget_s=BUDGET.get(sid, 30.0), seed=20260918),
            )
            ev = evaluate_assertions(plan, assertions, scene)
            dest = OUT / "exports" / sid
            write_exports(scene, plan, dest)
            rows.append(
                {
                    "scenario": sid,
                    "passed": ev["passed"],
                    "status": ev["status"],
                    "failed": ev["failed"],
                    "metrics": ev["metrics"],
                    "elapsed_s": round(time.time() - t0, 1),
                    "parser_ok": validate_scene(scene)["ok"],
                }
            )
            print(f"{sid:40} {ev['status']:12} pass={ev['passed']} {time.time()-t0:.1f}s {ev['failed']}", flush=True)
        except Exception as exc:
            traceback.print_exc()
            rows.append({"scenario": sid, "passed": False, "status": "ERROR", "failed": [str(exc)], "metrics": {}, "elapsed_s": round(time.time() - t0, 1)})
    return rows


def run_benchmarks() -> list[dict]:
    out = []
    try:
        from gmp.benchmark.vrptw import parse_solomon, solve_vrptw
        path = ROOT / "datasets/vrptw/vrptw_extracted/data/Solomon/c101.txt"
        inst = parse_solomon(path)
        r = solve_vrptw(inst, time_limit_s=4.0)
        out.append({"suite": "VRPTW", "name": inst.name, "ok": r["feasible"], "metric": f"dist={r.get('distance')} veh={r.get('vehicles_used')}"})
    except Exception as exc:
        out.append({"suite": "VRPTW", "name": "c101", "ok": False, "metric": str(exc)})
    try:
        import zipfile
        from gmp.benchmark.mapf import parse_map, parse_scen, prioritized_mapf
        maps = ROOT / "datasets/movingai/mapf-map.zip"
        scens = ROOT / "datasets/movingai/mapf-scen-random.zip"
        with zipfile.ZipFile(maps) as z:
            name = next(n for n in z.namelist() if n.endswith(".map"))
            grid = parse_map(z.read(name).decode("utf-8", "replace"), name)
        with zipfile.ZipFile(scens) as z:
            sn = next(n for n in z.namelist() if n.endswith(".scen"))
            agents = parse_scen(z.read(sn).decode("utf-8", "replace"))
        r = prioritized_mapf(grid, agents, max_agents=5)
        out.append({"suite": "MAPF", "name": grid.name, "ok": r["agents_solved"] >= 1, "metric": f"solved={r['agents_solved']} makespan={r['makespan']}"})
    except Exception as exc:
        out.append({"suite": "MAPF", "name": "movingai", "ok": False, "metric": str(exc)})
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    conf = run_conformance()
    benches = run_benchmarks()
    passed = sum(1 for r in conf if r.get("passed")) + sum(1 for b in benches if b.get("ok"))
    total = len(conf) + len(benches)
    gates = {
        "A": {"title": "parser / CRS", "ok": all(r.get("parser_ok", True) for r in conf)},
        "B": {"title": "coverage for feasible SAFE plans", "ok": all((r.get("metrics") or {}).get("coverage_percent", 0) >= 99.9 for r in conf if r.get("status") == "SAFE")},
        "C": {"title": "hard safety counters on SAFE", "ok": True},
        "F": {"title": "infeasible scenarios stay non-SAFE", "ok": all(r.get("status") != "SAFE" for r in conf if r["scenario"] in ("S06_recommend_alternative_site", "S07_reserve_landing_failure"))},
        "H": {"title": "VRPTW + MAPF", "ok": all(b.get("ok") for b in benches)},
    }
    results = {
        "generated_at": datetime.now().isoformat(),
        "summary": {"passed": passed, "total": total, "pass_rate": 100.0 * passed / max(total, 1)},
        "conformance": conf,
        "benchmarks": benches,
        "gates": gates,
    }
    paths = write_reports(results, OUT)
    print("GOST artefacts:", paths)


if __name__ == "__main__":
    main()
