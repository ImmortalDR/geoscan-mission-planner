#!/usr/bin/env python3
"""Run the production-independent gate against every immutable reference pair."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

from gmp.safety.h3_gate import validate_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.dataset / "manifest.json").read_text())
    failures = []
    for item in manifest["files"]:
        path = args.dataset / item["path"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            failures.append(item["path"])
    rows = []
    paths = sorted((args.dataset / "scenarios").rglob("expected/*/result.json"))
    # Recommendation fixtures have their own input snapshot.
    for path in paths:
        root = path.parent.parent.parent
        started = time.monotonic()
        result = json.loads(path.read_text())
        report = validate_result(root / "input", result)
        row = {"pair": str(path.relative_to(args.dataset)), "passed": report["passed"],
               "status": report["status"], "seconds": round(time.monotonic() - started, 3),
               "certificate": bool(report["certificate"]), "metrics": report["metrics"],
               "violations": report["violations"]}
        rows.append(row)
        print(f"{row['status']:10} {row['passed']} {row['pair']}", flush=True)
    payload = {"manifest_mismatches": failures, "pairs": rows,
               "passed": not failures and len(rows) == 14 and all(r["passed"] for r in rows)}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"Verified {len(rows)} pairs; manifest mismatches: {len(failures)}")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
