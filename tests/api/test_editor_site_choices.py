"""Browser table values must mean the same thing to the H2 consumer."""
import copy
import json
import subprocess
from pathlib import Path

from h2.contract import validate_bundle
from h2.site_policy import sites_for

ROOT = Path(__file__).resolve().parents[2]


def test_editor_transitions_and_h2_site_policy(tmp_path):
    output = tmp_path / 'choices.json'
    subprocess.run(['node', 'web/tests/editor_rules.cjs', str(output)], cwd=ROOT, check=True)
    cases = json.loads(output.read_text())
    original = json.loads((ROOT / 'fixtures/h1_h2/S00_smoke_rgb.bundle.json').read_text())
    assert len(cases['configs']) == 80
    for choices in cases['configs']:
        bundle = copy.deepcopy(original)
        bundle['sites'] = [{**original['sites'][0], **s, 'candidate': s.get('candidate', False)} for s in cases['sites']]
        uav = bundle['fleet'][0]
        uav.update(choices)
        bundle['fleet'] = [uav]
        validate_bundle(bundle)
        policy = sites_for(bundle, uav)
        assert policy['start'] == ([choices['start_site']] if choices['start_site'] else ['A', 'B', 'S'])
        assert policy['finish'] == ([choices['landing_site']] if choices['landing_site'] else ['A', 'B', 'L'])
        assert policy['refuel'] == (['A', 'B'] if choices['refuel_sites'] is None else choices['refuel_sites'])

        assert policy['emergency'] == ['A', 'B', 'L', 'R']
