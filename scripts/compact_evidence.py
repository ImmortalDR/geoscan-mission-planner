#!/usr/bin/env python3
"""Drop duplicate intermediate geometries, preserving every acceptance check."""
import argparse
import json
from pathlib import Path
from verify_h3_live import progress_summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, nargs="+")
    args = parser.parse_args()
    for path in args.reports:
        report = json.loads(path.read_text())
        if report.get("schema") != "geoscan.h3.live_acceptance.v1":
            parser.error(f"Not an acceptance report: {path}")
        for case in report["cases"]:
            case["progress"] = progress_summary(case.get("progress", []))
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")


if __name__ == "__main__":
    main()
