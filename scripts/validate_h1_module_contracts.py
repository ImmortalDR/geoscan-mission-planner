#!/usr/bin/env python3
"""Redirect: H1 module contracts live under h1."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

target = Path(__file__).resolve().parents[2] / "h1" / "scripts" / "validate_h1_module_contracts.py"
if not target.is_file():
    print(f"missing {target}", file=sys.stderr)
    raise SystemExit(2)
sys.argv[0] = str(target)
runpy.run_path(str(target), run_name="__main__")
