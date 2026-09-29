from copy import deepcopy
import json
from pathlib import Path
import re

from h2.planner import plan_bundle
from h2.report import export_geojson, write_html
from h2.scheduler import Settings


def example():
    source=Path(__file__).resolve().parents[1]/'fixtures/S00_smoke_rgb.bundle.json'
    return plan_bundle(json.loads(source.read_text()),settings=Settings(max_iterations=0,cpsat=False))


def test_geojson_wgs84_preserves_original():
    plan=example()
    before=deepcopy(plan)
    data=export_geojson(plan)
    assert plan==before
    assert data['type']=='FeatureCollection'
    assert len(data['features'])==len(plan['sorties'])
    lon,lat,z=data['features'][0]['geometry']['coordinates'][0]
    assert -180<=lon<=180 and -90<=lat<=90
    assert lon!=plan['sorties'][0]['waypoints'][0]['x']


def test_offline_report_escapes_embedded_data(tmp_path):
    plan=example()
    plan['scene_id']='</script><script>alert(1)</script>'
    plan['assumptions']=['<img src=x onerror=alert(1)>']
    output=write_html(plan,tmp_path/'report.html').read_text()
    assert plan['scene_id'] not in output
    assert '<img src=x' not in output
    assert 'type="range"' in output
    assert 'https://' not in output
    encoded=re.search(r'<script type="application/json" id="plan-data">(.*?)</script>',output,re.S).group(1)
    assert json.loads(encoded)==plan
