"""H1 entrypoint — delegates to canonical ``h1_coverage`` package."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class H1Result:
    scene: Any
    coverage: Any
    bundle: Any

    @property
    def task_count(self) -> int:
        return len(self.coverage.tasks)

    @property
    def coverage_percent(self) -> float:
        return self.coverage.coverage_percent


def run_h1(scene_dir: str | Path, **kwargs: Any) -> H1Result:
    from h1_coverage.config import CoverageConfig
    from h1_coverage.pipeline import run_h1 as _run

    cfg = CoverageConfig(
        angle_step_deg=float(kwargs.get("step_deg", 15.0)),
        keep_candidates=int(kwargs.get("keep_candidates", 4)),
        strict_coverage=False,
    )
    r = _run(scene_dir, cfg)
    return H1Result(scene=r.scene, coverage=r.coverage, bundle=r.bundle)


def run_h1_to_file(scene_dir: str | Path, output: str | Path, **kwargs: Any) -> H1Result:
    result = run_h1(scene_dir, **kwargs)
    result.bundle.write_json(output)
    return result
