#!/usr/bin/env python3
"""Generate per-algorithm CSV evidence from real pytest/JUnit outcomes."""
import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--id", help="Single algorithm id; defaults to the full catalog")
    p.add_argument("--output", type=Path, default=ROOT / "docs/evidence/algorithms")
    p.add_argument("--junit", type=Path, help="Collect an existing test run instead of running tests")
    args = p.parse_args()
    catalog = json.loads((ROOT / "algorithms/catalog.json").read_text())["algorithms"]
    entries = [a for a in catalog if not args.id or a["id"] == args.id]
    if not entries:
        p.error("Unknown algorithm id")
    for entry in entries:
        for path in [entry["source"], entry["example"], *entry["tests"]]:
            if not (ROOT / path).exists():
                p.error(f"Catalog path does not exist: {path}")
    args.output.mkdir(parents=True, exist_ok=True)
    report = args.junit or args.output / "junit.xml"
    exit_code = 0
    if not args.junit:
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(str(ROOT / s) for s in ("src", "h1/h1_coverage/src", "h2/src", "h2")))
        tests = sorted({t for entry in entries for t in entry["tests"]})
        exit_code = subprocess.run([sys.executable, "-m", "pytest", *tests, "-q", "--disable-warnings", "-o", "junit_family=xunit1", f"--junitxml={report}"], cwd=ROOT, env=env).returncode
    cases = list(ET.parse(report).iter("testcase"))
    summary = {"generated_at_unix": time.time(), "method": "scenario regression, not ML k-fold", "algorithms": []}
    for entry in entries:
        selected = [c for c in cases if c.get("file") in entry["tests"]]
        if not selected:
            raise RuntimeError(f"No measured tests found for {entry['id']}")
        digest = hashlib.sha256((ROOT / entry["source"]).read_bytes()).hexdigest()
        rows = []
        for c in selected:
            outcome = "failed" if c.find("failure") is not None or c.find("error") is not None else "skipped" if c.find("skipped") is not None else "passed"
            rows.append({"algorithm_id":entry["id"],"test_file":c.get("file"),"test_case":c.get("name"),"outcome":outcome,"duration_s":c.get("time"),"source_sha256":digest})
        with (args.output / f"{entry['id']}.csv").open("w", newline="") as out:
            writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        summary["algorithms"].append({"id":entry["id"],"passed":sum(r["outcome"]=="passed" for r in rows),"failed":sum(r["outcome"]=="failed" for r in rows),"skipped":sum(r["outcome"]=="skipped" for r in rows),"duration_s":sum(float(r["duration_s"]) for r in rows),"source_sha256":digest})
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2)+"\n")
    return exit_code or int(any(r["failed"] for r in summary["algorithms"]))


if __name__ == "__main__":
    raise SystemExit(main())
