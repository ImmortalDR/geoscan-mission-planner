import json
from pathlib import Path
from h2.cli import main

ROOT=Path(__file__).resolve().parents[1]


def test_cli_validation_and_invalid_version(tmp_path):
    assert main(["validate-bundle",str(ROOT/"fixtures/S00_smoke_rgb.bundle.json")]) == 0
    bad=tmp_path/"bad.json"
    bad.write_text('{"schema_version":"broken"}')
    assert main(["validate-bundle",str(bad)]) == 1


def test_cli_plan_report(tmp_path,bundle):
    source=tmp_path/"bundle.json"
    source.write_text(json.dumps(bundle))
    out=tmp_path/"out"
    assert main(["plan",str(source),"--out",str(out),"--iterations","0","--no-cpsat"]) == 0
    assert json.loads((out/"plan.json").read_text())["status"] == "FEASIBLE"
    assert (out/"report.html").exists()
    assert json.loads((out/"routes.geojson").read_text())["type"] == "FeatureCollection"
