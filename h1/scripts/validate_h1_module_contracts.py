#!/usr/bin/env python3
"""Validate h1_modules.v1.json ↔ MODULES ↔ pytest files (Q-02)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "h1_coverage"
CONTRACT = ROOT / "contracts" / "h1_modules.v1.json"
TESTS = PKG / "tests"


def main() -> int:
    sys.path.insert(0, str(PKG / "src"))
    from h1_coverage.contracts import CHECKERS
    from h1_coverage.modules import MODULES

    data = json.loads(CONTRACT.read_text(encoding="utf-8"))
    ids = [m["id"] for m in data["modules"]]
    reg = [m.id for m in MODULES]
    errors: list[str] = []
    if ids != reg:
        errors.append(f"JSON module order/ids {ids} != registry {reg}")
    for m in MODULES:
        if m.id not in CHECKERS:
            errors.append(f"no checker for {m.id}")
        tpath = TESTS / Path(m.pytest_node).name
        if not tpath.is_file():
            errors.append(f"missing test file {tpath}")
        cj = next((x for x in data["modules"] if x["id"] == m.id), None)
        if cj and cj.get("contract_id") != m.contract_id:
            errors.append(f"{m.id} contract_id mismatch JSON vs registry")
    if errors:
        print("FAIL")
        for e in errors:
            print(" -", e)
        return 1
    print(f"OK: {len(MODULES)} modules wired (contracts + checkers + tests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
