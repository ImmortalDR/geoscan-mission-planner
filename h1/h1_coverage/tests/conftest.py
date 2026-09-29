"""Pytest configuration — paths live in paths.py for importability."""
from __future__ import annotations

# Ensure tests/ is on path for `import paths`
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))
