"""Shared paths for module acceptance tests (importable without package)."""
from __future__ import annotations

from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parents[1]
H1_ROOT = PKG_ROOT.parent
CONFORMANCE = PKG_ROOT / "fixtures" / "conformance"
H1_FIX = H1_ROOT / "fixtures" / "h1_modules"


def scene(name: str) -> Path:
    p = CONFORMANCE / name
    if not p.is_dir():
        pytest.skip(f"missing conformance scene {name}")
    return p
