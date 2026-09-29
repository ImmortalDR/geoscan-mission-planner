"""Joint smoke: H1 fixtures → H2 plan → optional H3-shaped summary.

Does not claim SAFE/certificate (H3 owns that). Records H2 status + conflict
counts for the P0 feasible path: S00, S02, S05 (+ S03 with scene sidecar).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from h2.contract import load_bundle
from h2.deconfliction import detect_conflicts
from h2.planner import plan_bundle
from h2.scheduler import Settings

# Feasible-path smoke (INFEASIBLE scenes belong in acceptance.py, not here).
DEFAULT = (
    ("S00_smoke_rgb.bundle.json", None),
    ("S02_multipolygon_holes.bundle.json", None),
    ("S05_4d_deconfliction.bundle.json", None),
    ("S03_temporal_airspace_daylight.bundle.json", "scenes/S03_temporal.scene.json"),
)


def _load_scene(rel: str | None):
    if not rel:
        return None
    path = ROOT / "fixtures" / rel
    return json.loads(path.read_text(encoding="utf-8"))


def run_one(bundle_name: str, scene_rel: str | None, budget: float) -> dict:
    bundle = load_bundle(ROOT / "fixtures" / bundle_name)
    scene = _load_scene(scene_rel)
    settings = Settings(
        objective=bundle["mission"]["objectives"][0],
        time_budget_s=budget,
        max_iterations=40,
        cpsat=True,
    )
    plan = plan_bundle(bundle, settings, scene)
    residual = detect_conflicts(plan["sorties"], bundle["fleet"])
    before = plan.get("deconfliction", {}).get("before") or []
    return {
        "bundle": bundle_name,
        "scene_sidecar": scene_rel,
        "scene_id": plan["scene_id"],
        "status": plan["status"],
        "metrics": plan["metrics"],
        "conflicts_before": len(before),
        "residual_conflicts": len(residual),
        "unassigned": len(plan.get("unassigned") or []),
        "assumptions": plan.get("assumptions"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget", type=float, default=15.0)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "e2e_acceptance.json")
    args = parser.parse_args()
    rows = []
    failed = False
    for bundle_name, scene_rel in DEFAULT:
        row = run_one(bundle_name, scene_rel, args.budget)
        rows.append(row)
        ok = row["status"] == "FEASIBLE" and row["residual_conflicts"] == 0
        print(
            row["scene_id"],
            row["status"],
            "residual=",
            row["residual_conflicts"],
            "OK" if ok else "FAIL",
            flush=True,
        )
        if not ok:
            failed = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"rows": rows, "pass": not failed}, indent=2) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
