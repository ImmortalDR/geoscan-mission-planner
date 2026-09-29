"""Smoke H1 via canonical package."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
try:
    from h1_coverage.config import CoverageConfig
    from h1_coverage.pipeline import run_h1
except ImportError:
    print("pip install -e ../h1/h1_coverage", file=sys.stderr)
    raise SystemExit(2)

sid = sys.argv[1] if len(sys.argv) > 1 else "S00_smoke_rgb"
root = Path(__file__).resolve().parents[1] / "datasets" / "geoscan_customer_conformance"
r = run_h1(root / sid, CoverageConfig(strict_coverage=False))
print(sid, "tasks", len(r.coverage.tasks), "cov", round(r.coverage.coverage_percent, 2))
