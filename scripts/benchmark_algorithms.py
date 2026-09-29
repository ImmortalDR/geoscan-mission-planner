#!/usr/bin/env python3
"""Measure independent examples and held-out scenario/seed combinations."""
import csv
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for rel in ("src", "h1/h1_coverage/src", "h2/src"):
    sys.path.insert(0, str(ROOT / rel))


def main():
    from shapely.geometry import LineString, shape
    from shapely.ops import unary_union
    from h1_coverage.coverage.sweep import generate_transects
    from h2.deconfliction import detect_conflicts, resolve_conflicts
    from h2.planner import plan_bundle, Settings
    from gmp.api.runtime import hardware
    rows = []
    example = json.loads((ROOT / "examples/algorithms/sweep.json").read_text())
    for case in example["cases"]:
        area = shape(case["polygon"])
        for angle in example["angles_deg"]:
            start = time.perf_counter()
            lines = generate_transects(area, spacing=example["spacing_m"], angle_deg=angle, job_id=case["id"])
            footprint = unary_union([LineString(t.coords).buffer(example["spacing_m"]/2) for t in lines])
            rows.append({"algorithm":"h1.sweep","scenario":case["id"],"seed_or_angle":angle,"runtime_s":time.perf_counter()-start,"coverage_fraction":area.intersection(footprint).area/area.area,"length_m":sum(t.length_m for t in lines)})
    sample = json.loads((ROOT / "examples/algorithms/conflicts.json").read_text())
    start = time.perf_counter()
    before = detect_conflicts(sample["sorties"], sample["fleet"])
    _, after = resolve_conflicts(sample["sorties"], sample["fleet"])
    rows.append({"algorithm":"h2.departure_shifts","scenario":"crossing","runtime_s":time.perf_counter()-start,"conflicts_before":len(before),"conflicts_after":len(after)})
    for name in ("S00_smoke_rgb", "S02_multipolygon_holes", "S05_4d_deconfliction"):
        path = ROOT / "h2/fixtures" / f"{name}.bundle.json"
        source = json.loads(path.read_text())
        for objective in source["mission"]["objectives"]:
            for seed in (17, 29, 43):
                plan = plan_bundle(source, Settings(objective=objective, seed=seed, time_budget_s=.5, max_iterations=10))
                rows.append({"algorithm":"h2.search","scenario":name,"seed_or_angle":seed,"objective":objective,"status":plan["status"],"checks_passed":plan["checks"]["passed"],"input_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),**plan["metrics"]})
    out = ROOT / "docs/evidence/algorithms"
    out.mkdir(parents=True, exist_ok=True)
    fields = sorted({k for r in rows for k in r})
    with (out / "cross_scenario.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (out / "hardware.json").write_text(json.dumps(hardware(), indent=2)+"\n")
    print(f"Measured {len(rows)} cases. See {out / 'cross_scenario.csv'}")


if __name__ == "__main__":
    main()
