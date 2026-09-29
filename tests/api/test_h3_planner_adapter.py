import copy
import math

import pytest
from pyproj import Transformer
from shapely.ops import transform
from dataclasses import asdict
from types import SimpleNamespace

from shapely.geometry import MultiPoint, box, shape

from gmp.api.planner_adapter import _h1_geojson, refresh_h1_view, transfer_coverage
from h1_coverage.models import AtomicTask, Transect


def test_transfer_preserves_h1_tasks_without_private_bridge(monkeypatch):
    import gmp.coverage.engine as engine

    monkeypatch.delattr(engine, "_convert_task", raising=False)
    line = [(0.0, 0.0), (100.0, 0.0)]
    task = AtomicTask(
        id="task", job_id="job", payload_class="rgb", payload_profile_id="camera",
        agl_m=80.0, transects=[Transect(coords=line, length_m=100.0, job_id="job")],
        survey_length_m=100.0, turn_count=0, sweep_angle_deg=0.0,
        entry=line[0], exit=line[-1], geom_coords=line,
        internal_transition_m=0.0, fixed_wing_safe=True, notes=["canonical"],
    )
    before = asdict(task)
    geom = box(0, -10, 100, 10)
    scene = SimpleNamespace(payloads={}, jobs=[SimpleNamespace(id="job", effective_geom=None)])
    result = SimpleNamespace(
        scene=SimpleNamespace(payloads={}, jobs=[SimpleNamespace(id="job", effective_geom=geom)]),
        coverage=SimpleNamespace(tasks={"task": task}, per_job=[{"job_id": "job"}],
                                 exclusions={}, candidates={}, coverage_percent=100.0, warnings=[]),
    )

    converted = transfer_coverage(scene, result)

    assert asdict(task) == before
    assert scene.jobs[0].effective_geom.equals_exact(geom, 0)
    actual = converted.tasks["task"]
    for name, value in before.items():
        if name not in ("geom_coords", "transects", "alternate_fixed_wing_safe"):
            assert getattr(actual, name) == value
    assert actual.route_variants == task.route_variants
    assert list(actual.geom.coords) == line
    assert asdict(actual.transects[0]) == asdict(task.transects[0])
    assert converted.coverage_percent == 100.0


@pytest.mark.parametrize("angle", [0, 15, 90, 105, 165])
@pytest.mark.parametrize("reverse", [False, True])
def test_task_envelopes_ignore_flight_direction_and_utm_origin(angle, reverse):
    angle = math.radians(angle)
    along = (math.cos(angle), math.sin(angle))
    normal = (-along[1], along[0])
    transects = []
    for index in range(3):
        points = [[413000 + along[0] * distance + normal[0] * index * 360,
                   6171000 + along[1] * distance + normal[1] * index * 360]
                  for distance in (0, 1000)]
        if bool(index % 2) != reverse:
            points.reverse()
        transects.append({"coords": points, "length_m": 1000})
    bundle = {"crs": {"metric_epsg": 32637}, "tasks": [
        {"id": "a", "job_id": "job", "payload_class": "rgb", "transects": transects[:2]},
        {"id": "b", "job_id": "job", "payload_class": "rgb", "transects": transects[2:]},
    ]}
    original = copy.deepcopy(bundle)
    view = _h1_geojson(bundle)
    envelopes = [f for f in view["features"] if f["properties"]["kind"] == "task_envelope"]
    assert len(envelopes) == 2
    to_metric = Transformer.from_crs(4326, 32637, always_xy=True).transform
    for task, envelope in zip(bundle["tasks"], envelopes):
        assert envelope["properties"]["offset_m"] == pytest.approx(90, abs=1e-5)
        actual = transform(to_metric, shape(envelope["geometry"]))
        hull = MultiPoint([p for t in task["transects"] for p in t["coords"]]).convex_hull
        assert actual.buffer(.001).covers(hull)
        assert actual.hausdorff_distance(hull.buffer(90)) < .001
    assert bundle == original


def test_old_run_view_rebuilt_without_changing_h1_bundle():
    bundle = {"crs": {"metric_epsg": 32637}, "tasks": [
        {"id": "a", "job_id": "job", "payload_class": "rgb", "transects": [
            {"coords": [[410000, 6171000], [411000, 6171000]]},
            {"coords": [[411000, 6171360], [410000, 6171360]]}]}]}
    record = {"run_code": "original-run", "plan": {"h1_bundle": bundle,
        "h1_output": {"geojson": {"old": True}}}, "progress": [
        {"stage": "h1", "duration_s": 4.5, "h1_output": {"geojson": {"old": True}}}]}
    original = copy.deepcopy(bundle)
    refresh_h1_view(record)
    view = record["plan"]["h1_output"]["geojson"]
    assert view["envelope_version"] == 2
    assert record["progress"][0]["h1_output"]["geojson"] == view
    assert record["progress"][0]["duration_s"] == 4.5
    assert record["plan"]["h1_bundle"] == original
    assert record["run_code"] == "original-run"
    refresh_h1_view(record)
    assert record["plan"]["h1_output"]["geojson"] is view
