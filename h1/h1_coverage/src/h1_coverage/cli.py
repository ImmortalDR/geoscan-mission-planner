"""CLI for H1 coverage."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import CoverageConfig
from .export import export_angle_report, export_html_map, export_transects_geojson
from .modules import MODULES
from .pipeline import run_h1_to_file


LIVE_SCENES = (
    "S00_smoke_rgb",
    "S01_full_customer_acceptance_100km2",
    "S02_multipolygon_holes",
    "S08_fixed_wing_turnaround",
    "S09_wind_feasibility",
    "S10_different_start_end",
    "S11_payload_compatibility",
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="h1-coverage")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("run", help="Scene dir → H1H2Bundle")
    c.add_argument("--scene", type=Path, required=True)
    c.add_argument("--output", type=Path, required=True)
    c.add_argument("--no-strict-coverage", action="store_true")
    c.add_argument("--print-meta", action="store_true")
    c.add_argument("--export-geojson", type=Path, default=None)
    c.add_argument("--export-html", type=Path, default=None)
    c.add_argument("--export-report", type=Path, default=None, help="W-07 markdown angle report")
    c.add_argument("--large-scene", action="store_true", help="coarser angles + headland for S01-scale")

    sub.add_parser("modules", help="List M1–M13 contract map")

    i = sub.add_parser("inspect", help="List H1 inputs present in a scene directory")
    i.add_argument("--scene", type=Path, required=True)
    i.add_argument("--json", action="store_true", help="machine-readable report")
    i.add_argument("--no-load", action="store_true", help="only check files, do not load_scene")

    e = sub.add_parser("export-fixtures", help="Q-01: write live h1_h2 bundles")
    e.add_argument(
        "--conformance",
        type=Path,
        default=None,
        help="conformance root (default: package fixtures/conformance)",
    )
    e.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="output fixtures/h1_h2 directory",
    )
    e.add_argument("--no-strict-coverage", action="store_true", default=True)

    args = p.parse_args(argv)

    if args.cmd == "modules":
        from .coverage.backend import backend_status
        from .io.manifest import ENTRY_POINTS, SCENE_INPUTS

        for m in MODULES:
            print(f"{m.id}  {m.contract_id}  {m.title}")
            print(f"     api={m.public_api}")
            print(f"     test={m.pytest_node}")
        st = backend_status()
        print(f"\nM6 backend: {st['active']} (f2c_importable={st['fields2cover_importable']})")
        print(f"  {st['note']}")
        print("\n--- scene inputs ---")
        for spec in SCENE_INPUTS:
            print(f"  {spec.filename:28} [{spec.presence}] {spec.role}")
        print("\n--- entry points ---")
        for kind, how in ENTRY_POINTS:
            print(f"  {kind:12} {how}")
        return 0

    if args.cmd == "inspect":
        from .io.inspect import inspect_scene_dir

        report = inspect_scene_dir(args.scene, try_load=not args.no_load)
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"scene dir: {report['path']}")
            print(f"load_ok:   {report['load_ok']}  id={report.get('scene_id')}")
            for _key, info in report["files"].items():
                mark = "✓" if info["present"] else "·"
                print(f"  {mark} {info['filename']:28} {info['presence']:12} {info['role']}")
            if report["gaps"]:
                print("gaps:")
                for g in report["gaps"]:
                    print(f"  - {g}")
            if report.get("summary"):
                print("summary:", json.dumps(report["summary"], ensure_ascii=False))
            for w in report.get("warnings") or []:
                print(f"  warn: {w}")
        return 0 if (report.get("load_ok") or (args.no_load and not report["gaps"])) else 1

    if args.cmd == "export-fixtures":
        pkg = Path(__file__).resolve().parents[2]
        conf = args.conformance or (pkg / "fixtures" / "conformance")
        out = args.out_dir
        out.mkdir(parents=True, exist_ok=True)
        cfg = CoverageConfig(strict_coverage=False)
        ok = 0
        for name in LIVE_SCENES:
            scene = conf / name
            if not scene.is_dir():
                print(f"SKIP missing {name}")
                continue
            bundle_path = out / f"{name}.bundle.json"
            r = run_h1_to_file(scene, bundle_path, cfg)
            gj = out / f"{name}_transects.geojson"
            html = out / f"{name}_map.html"
            export_transects_geojson(r.scene, r.coverage, gj)
            export_html_map(r.scene, r.coverage, html)
            print(
                f"OK {name} tasks={len(r.coverage.tasks)} "
                f"cov={r.coverage.coverage_percent:.2f}% → {bundle_path.name}"
            )
            ok += 1
        print(f"exported {ok} scenes → {out}")
        return 0 if ok else 1

    if args.cmd == "run":
        if args.large_scene:
            cfg = CoverageConfig(
                strict_coverage=not args.no_strict_coverage,
                angle_step_deg=30.0,
                keep_candidates=3,
                headland_m=8.0,
                max_tasks_per_job=40,
            )
        else:
            cfg = CoverageConfig(strict_coverage=not args.no_strict_coverage)
        r = run_h1_to_file(args.scene, args.output, cfg)
        if args.export_geojson:
            export_transects_geojson(r.scene, r.coverage, args.export_geojson)
            print(f"geojson → {args.export_geojson}")
        if args.export_html:
            export_html_map(r.scene, r.coverage, args.export_html)
            print(f"html → {args.export_html}")
        if args.export_report:
            export_angle_report(r.scene, r.coverage, args.export_report)
            print(f"report → {args.export_report}")
        print(
            f"OK {r.scene.id} tasks={len(r.coverage.tasks)} "
            f"coverage={r.coverage.coverage_percent:.2f}% → {args.output}"
        )
        if args.print_meta:
            meta = r.bundle.data.get("coverage_meta") or {}
            print(json.dumps(meta, indent=2, ensure_ascii=False)[:4000])
            feas = r.bundle.data.get("feasibility") or {}
            human = feas.get("ineligible_reasons_human") or {}
            if human:
                print("--- rejects (human) ---")
                for k, v in list(human.items())[:12]:
                    print(f"  {k}: {v}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
