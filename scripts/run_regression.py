#!/usr/bin/env python3
"""Run the canonical service regression and write machine-readable evidence."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SUITES = ["tests/safety", "tests/api", "tests/infrastructure", "tests/property", "tests/report", "h1/h1_coverage/tests", "h2/tests"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/evidence")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = args.output / "system.junit.xml"
    started = time.time()
    result = subprocess.run([sys.executable, "-m", "pytest", *SUITES, "-q", "-o", "junit_family=xunit1", f"--junitxml={report}"], cwd=ROOT)
    cases = list(ET.parse(report).iter("testcase"))
    failed = sum(c.find("failure") is not None or c.find("error") is not None for c in cases)
    skipped = sum(c.find("skipped") is not None for c in cases)
    summary = {"generated_at_unix": time.time(), "duration_s": round(time.time()-started, 3), "passed": len(cases)-failed-skipped,
               "failed": failed, "skipped": skipped, "exit_code": result.returncode, "suites": SUITES,
               "postgres_configured": bool(os.environ.get("GMP_TEST_DATABASE_URL"))}
    (args.output / "system.json").write_text(json.dumps(summary, indent=2)+"\n")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
