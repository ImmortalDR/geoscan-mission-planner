"""H1 end-to-end pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .bundle import H1H2Bundle, export_bundle
from .config import CoverageConfig
from .coverage.engine import CoverageResult, build_coverage
from .io.scene import load_scene
from .models import Scene


@dataclass
class H1Result:
    scene: Scene
    coverage: CoverageResult
    bundle: H1H2Bundle


def run_h1_scene(scene: Scene, cfg: CoverageConfig | None = None) -> H1Result:
    """In-memory entry: already-built ``Scene`` → coverage → bundle."""
    coverage = build_coverage(scene, cfg)
    bundle = export_bundle(scene, coverage, cfg=cfg)
    return H1Result(scene=scene, coverage=coverage, bundle=bundle)


def run_h1(scene_dir: str | Path, cfg: CoverageConfig | None = None) -> H1Result:
    scene = load_scene(scene_dir)
    return run_h1_scene(scene, cfg)


def run_h1_to_file(scene_dir: str | Path, output: str | Path, cfg: CoverageConfig | None = None) -> H1Result:
    result = run_h1(scene_dir, cfg)
    result.bundle.write_json(output)
    return result
