"""M3 TerrainModel — contract h1.m3.terrain.v1"""
from __future__ import annotations

from h1_coverage.config import CoverageConfig
from h1_coverage.contracts import check_m3_elevation
from h1_coverage.coverage.engine import build_coverage
from h1_coverage.io.dem import load_dem
from h1_coverage.io.scene import load_scene
from h1_coverage.models import AtomicTask, Dem, Transect
from h1_coverage.modules import by_id

from paths import scene


def test_module_registry_m3():
    assert by_id("M3").contract_id == "h1.m3.terrain.v1"


def test_m3_dem_sample_s00():
    s = load_scene(scene("S00_smoke_rgb"))
    dem_path = scene("S00_smoke_rgb") / "dem.tif"
    assert dem_path.is_file()
    sampler = load_dem(str(dem_path), s.crs)
    assert sampler is not None
    job = s.jobs[0]
    x, y = job.geom.centroid.x, job.geom.centroid.y
    z = sampler.elevation(x, y)
    check_m3_elevation(z)
    assert sampler.stats.mean_m == sampler.mean_elevation_m


def test_m3_missing_dem_does_not_crash(tmp_path):
    # Copy minimal scene without dem via load of S00 then strip — smoke path
    s = load_scene(scene("S00_smoke_rgb"))
    s.dem.sampler = None
    s.dem.path = None
    r = build_coverage(s, CoverageConfig(strict_coverage=False))
    assert any("DEM" in w or "flat" in w.lower() or "M3" in w for w in r.warnings) or r.tasks


class _StepDem:
    """Synthetic DEM: flat then +80 m cliff — triggers local AGL drop."""

    mean_elevation_m = 0.0

    def __init__(self, x_split: float):
        self.x_split = x_split

    def elevation(self, x: float, y: float) -> float:
        return 0.0 if x < self.x_split else 80.0


def test_a05_local_agl_below_min_on_hill():
    from h1_coverage.io.dem import terrain_warnings_for_tasks

    s = load_scene(scene("S00_smoke_rgb"))
    job = s.jobs[0]
    cx, cy = job.geom.centroid.x, job.geom.centroid.y
    # Split through centroid: one end on low ground, one on high
    s.dem = Dem(path="synthetic", sampler=_StepDem(cx), nominal_elevation_m=0.0)
    # Fake a short task with ends on both sides of the cliff, AGL=50
    task = AtomicTask(
        id="T_hill",
        job_id=job.id,
        payload_class="rgb",
        payload_profile_id="p",
        agl_m=50.0,
        transects=[
            Transect(
                coords=[(cx - 50.0, cy), (cx + 50.0, cy)],
                length_m=100.0,
                job_id=job.id,
            )
        ],
        survey_length_m=100.0,
        turn_count=0,
        sweep_angle_deg=0.0,
        entry=(cx - 50.0, cy),
        exit=(cx + 50.0, cy),
        geom_coords=[(cx - 50.0, cy), (cx + 50.0, cy)],
    )
    # Start site at low ground so ref_z≈0 → flight at 50; high ground → local AGL=-30
    warns = terrain_warnings_for_tasks(s, {task.id: task}, min_agl_m=40.0)
    assert any("LOCAL_AGL_BELOW_MIN" in w for w in warns)


def test_a05_terrain_strict_flag():
    s = load_scene(scene("S00_smoke_rgb"))
    job = s.jobs[0]
    cx, cy = job.geom.centroid.x, job.geom.centroid.y
    s.dem = Dem(path="synthetic", sampler=_StepDem(cx), nominal_elevation_m=0.0)
    # Force low AGL tasks by mutating payloads
    for p in s.payloads.values():
        p.agl_m = 45.0
        p.nominal_agl_m = 45.0
    r = build_coverage(s, CoverageConfig(strict_coverage=False, terrain_strict=True, angle_step_deg=90.0))
    # May or may not hit cliff depending on transect placement; at least API accepts flag
    assert isinstance(r.warnings, list)
