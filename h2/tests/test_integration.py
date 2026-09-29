from copy import deepcopy
import json
from pathlib import Path

import pytest

pytest.importorskip('gmp.models')
from h2.integration import to_gmp_plan
from h2.planner import plan_bundle
from h2.scheduler import Settings


def test_h3_bridge_preserves_h1_and_requires_validation():
    source=Path(__file__).resolve().parents[1]/'fixtures/S00_smoke_rgb.bundle.json'
    bundle=json.loads(source.read_text())
    plan=plan_bundle(bundle,settings=Settings(max_iterations=0,cpsat=False))
    original=deepcopy(bundle),deepcopy(plan)
    result=to_gmp_plan(bundle,plan)
    assert (bundle,plan)==original
    assert result.status=='UNKNOWN' and result.certificate is None and result.validation is None
    assert result.diagnosis['requires_h3_validation']
    assert result.metrics['total_flight_min']==plan['metrics']['total_flight_s']/60
    for task in bundle['tasks']:
        assert list(result.tasks[task['id']].geom.coords)==[tuple(p) for p in task['geom_coords']]
    for expected,actual in zip(plan['sorties'],result.sorties):
        assert actual.task_reversed==expected['task_reversed']
        assert actual.duration_s==pytest.approx(expected['end_s']-expected['start_s'],abs=1e-5)
        assert actual.waypoints[-1].amsl_m==expected['waypoints'][-1]['z_m']
        assert actual.waypoints[-1].remaining_endurance_s==pytest.approx(expected['resource_margin_s'])
    bundle['scene_id']='different'
    with pytest.raises(ValueError,match='match'):
        to_gmp_plan(bundle,plan)
