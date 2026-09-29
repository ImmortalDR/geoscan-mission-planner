"""CLI shim: H1 coverage lives in h1_coverage.

Prefer:
  h1-coverage run --scene … --output …
  h1-coverage export-fixtures --out-dir fixtures/h1_h2
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gmp.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("coverage", help="Deprecated shim → h1_coverage")
    c.add_argument("--scene", required=True)
    c.add_argument("--output", required=True)
    c.add_argument("--export-geojson", default=None)
    c.add_argument("--print-meta", action="store_true")
    args = p.parse_args(argv)

    if args.cmd == "coverage":
        try:
            from h1_coverage.config import CoverageConfig
            from h1_coverage.export import export_transects_geojson
            from h1_coverage.pipeline import run_h1_to_file
        except ImportError:
            print(
                "ERROR: install canonical H1:\n"
                "  pip install -e ../h1/h1_coverage",
                file=sys.stderr,
            )
            return 2
        r = run_h1_to_file(args.scene, args.output, CoverageConfig(strict_coverage=False))
        if args.export_geojson:
            export_transects_geojson(r.scene, r.coverage, args.export_geojson)
        print(
            f"OK (via h1_coverage) {r.scene.id} tasks={len(r.coverage.tasks)} "
            f"coverage={r.coverage.coverage_percent:.2f}% → {args.output}"
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
