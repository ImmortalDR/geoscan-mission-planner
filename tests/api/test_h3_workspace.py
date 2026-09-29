from __future__ import annotations

import copy
import io
import json
import os
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gmp.api.app import create_app
from gmp.api.inputs import check_documents, input_hash, open_geotiff, parse_kml, read_json, terrain_covers, write_json
from gmp.api.workspace_store import WorkspaceStore

DATASET = Path(os.environ.get("GMP_DATASET_DIR", Path(__file__).resolve().parents[2] / "data"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GMP_ACCESS_CODE", "test-access-code")
    app = create_app(tmp_path / "workspace", DATASET)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def login(client):
    response = client.post("/api/v1/auth/login", json={"code": "test-access-code"})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response


def clone(client, scenario="S00_smoke_rgb"):
    response = client.post("/api/v1/scenes", params={"scenario_id": scenario})
    assert response.status_code == 200, response.text
    return response.json()


def await_plan(client, plan_id, limit=30):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        record = client.get(f"/api/v1/plans/{plan_id}").json()
        if record["progress"][-1]["stage"] in ("complete", "error"):
            return record
        time.sleep(.05)
    pytest.fail("Job did not finish")


def test_packaged_user_templates_available_in_fresh_workspace(client):
    import hashlib
    from gmp.api.scenario_catalog import TEMPLATE_ROOT

    login(client)
    assert client.get("/api/v1/scenes?saved_only=true").json()["scenes"] == []
    catalog = {item["id"]: item for item in client.get("/api/v1/scenarios").json()["scenarios"]}
    templates = sorted(TEMPLATE_ROOT.glob("S*"))
    assert {p.name.split("_")[0] for p in templates} == {f"S{i}" for i in range(12, 20)}
    assert {item.split("_")[0] for item in catalog} == {f"S{i:02d}" for i in range(20)}
    for directory in templates:
        spec = read_json(directory / "scenario.json")
        assert catalog[directory.name]["name"] == spec["title"]
        for filename, digest in spec.get("promotion", {}).get("file_sha256", {}).items():
            assert hashlib.sha256((directory / "input" / filename).read_bytes()).hexdigest() == digest
        scene = clone(client, directory.name)
        assert scene["input_sha256"] == spec.get("promotion", {}).get("input_sha256", input_hash(directory / "input"))
        assert scene["name"] == f'{catalog[directory.name]["display_code"]} · {catalog[directory.name]["display_name"]}'
        assert scene["template_readonly"] is True
        assert scene["validation"]["valid"] is True
        assert client.post(f"/api/v1/scenes/{scene['id']}/save").status_code == 409
        assert client.post(f"/api/v1/scenes/{scene['id']}/rename", json={"name": "Changed"}).status_code == 409
        copied = client.post(f"/api/v1/scenes/{scene['id']}/save-as", json={"name": spec["title"] + " copy"})
        assert copied.status_code == 200, copied.text
        assert copied.json()["saved"] is True and copied.json()["template_readonly"] is False


def test_auth_cookie_csrf_and_protected_exports(client):
    assert client.get("/api/v1/scenarios").status_code == 401
    assert client.get("/api/v1/plans/unknown/export").status_code == 401
    response = login(client)
    assert all(value in response.headers["set-cookie"] for value in ("HttpOnly", "Secure", "SameSite=strict"))
    assert len(client.get("/api/v1/scenarios").json()["scenarios"]) == 20
    catalog = client.get("/api/v1/scenarios").json()["scenarios"]
    assert [item["real_elevation"] for item in catalog] == [True] * 10 + [False] * 10
    from gmp.api.scenario_catalog import scenario_directories
    directories = scenario_directories(DATASET)
    for item in catalog:
        inputs = directories[item["id"]] / "input"
        assert item["display_name"] and item["summary"]
        assert item["folder"] == ("complex" if item["real_elevation"] else "simple")
        assert item["zone_count"] == len(read_json(inputs / "survey_areas.geojson")["features"])
        assert item["site_count"] == len(read_json(inputs / "landing_sites.geojson")["features"])
        assert item["wind_ms"] == read_json(inputs / "mission.json")["wind"]["speed_ms"]
    by_id = {item["id"]: item for item in catalog}
    assert by_id["S01_msu_100km2"]["fleet_count"] == 4
    assert by_id["S01_msu_100km2"]["role"] == "scale"
    assert "purpose_ru" in by_id["S05_multi_uav"]
    assert by_id["S11_payload_compatibility"]["fleet_count"] == 5
    assert by_id["S01_msu_100km2"]["legacy_h2_id"] == "S01_full_customer_acceptance_100km2"
    assert by_id["S01_msu_100km2"]["legacy_task_count"] == 110
    presets = client.get("/api/v1/uav-models").json()["defaults"]
    assert {p["model"] for p in presets} == {"geoscan_201", "geoscan_401", "geoscan_701", "geoscan_801", "gemini"}
    token = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/v1/scenes", params={"scenario_id": "S00_smoke_rgb"}).status_code == 403
    client.headers["X-CSRF-Token"] = token
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/scenes").status_code == 401


def test_fixture_result_exposes_h1_view_and_pipeline_stages(client):
    login(client)
    scene = clone(client, "S00_smoke_rgb")
    response = client.post("/api/v1/plans", json={"scene_id": scene["id"], "objective": "makespan", "mode": "fixture"})
    assert response.status_code == 200, response.text
    record = await_plan(client, response.json()["id"])
    assert record["status"] == "SAFE"
    output = record["plan"]["h1_output"]
    assert output["schema"] == "geoscan.h1.viewer.v1"
    assert output["task_count"] == 2
    assert output["geojson"]["features"]
    stages = {entry["stage"] for entry in record["progress"]}
    assert {"h1", "h2", "h3"}.issubset(stages)


def test_login_rate_limit_survives_new_store(client):
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"code": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"code": "test-access-code"}).status_code == 429
    store = WorkspaceStore(client.app.state.store.root)
    assert store.login_allowed("testclient") is False


def test_basemap_referrer_policy_keeps_api_private(client):
    assert client.get("/").headers["referrer-policy"] == "strict-origin-when-cross-origin"
    login(client)
    assert client.get("/api/v1/scenarios").headers["referrer-policy"] == "same-origin"


def test_missing_secret_failclosed(tmp_path, monkeypatch):
    monkeypatch.delenv("GMP_ACCESS_CODE", raising=False)
    with TestClient(create_app(tmp_path, DATASET), base_url="https://testserver") as client:
        assert client.get("/health/ready").status_code == 503
        assert client.post("/api/v1/auth/login", json={"code": "anything"}).status_code == 503


def test_readiness_requires_both_planning_modules(client, monkeypatch):
    monkeypatch.setattr("gmp.api.app.importlib.util.find_spec", lambda name: object())
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["pipeline"] == "h1_coverage -> h2 -> h3"
    monkeypatch.setattr("gmp.api.app.importlib.util.find_spec", lambda name: None if name == "h2" else object())
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["components"]["h2"] is False


def test_delivery_archive_requires_authentication(client, tmp_path, monkeypatch):
    path = tmp_path / "delivery.tar.gz"
    path.write_bytes(b"test-archive")
    monkeypatch.setenv("GMP_DELIVERY_FILE", str(path))
    assert client.get("/api/v1/delivery").status_code == 401
    login(client)
    response = client.get("/api/v1/delivery")
    assert response.status_code == 200 and response.content == b"test-archive"
    path.unlink()
    assert client.get("/api/v1/delivery").status_code == 404


def test_scene_version_preserves_original_and_invalidates_fixture(client):
    login(client)
    scene = clone(client)
    assert scene["validation"]["valid"], scene["validation"]
    fleet = copy.deepcopy(scene["fleet"])
    fleet["uavs"][0]["ground_speed_kmh"] = 70
    response = client.post(f"/api/v1/scenes/{scene['id']}/versions", json={"fleet": fleet, "auto_dem": False})
    assert response.status_code == 200, response.text
    version = response.json()
    assert version["parent_id"] == scene["id"] and version["version"] == 2
    assert version["metadata"]["user_overrides"]
    assert version["input_sha256"] != scene["input_sha256"]
    assert client.get(f"/api/v1/scenes/{scene['id']}").json()["fleet"] == scene["fleet"]
    assert client.post("/api/v1/plans", json={"scene_id": version["id"], "mode": "fixture"}).status_code == 409


def test_geometry_and_policy_rejected(client):
    login(client)
    scene = clone(client)
    layer = copy.deepcopy(scene["layers"]["survey_areas"])
    layer["features"][0]["geometry"]["coordinates"] = [[[37, 55], [38, 56], [37, 56], [38, 55], [37, 55]]]
    assert client.post(f"/api/v1/scenes/{scene['id']}/versions", json={"layers": {"survey_areas": layer}, "auto_dem": False}).status_code == 422
    mission = copy.deepcopy(scene["mission"])
    mission["validation_policy"]["coverage_tolerance_fraction"] = .5
    assert client.post(f"/api/v1/scenes/{scene['id']}/versions", json={"mission": mission}).status_code == 422
    assert client.post("/api/v1/plans", json={"scene_id": scene["id"], "objective": "invalid"}).status_code == 422
    assert client.post("/api/v1/plans", json={"scene_id": scene["id"], "time_budget_s": 1801}).status_code == 422


def test_zip_traversal_rejected(client):
    login(client)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("../escape.json", "{}")
    response = client.post("/api/v1/scenes/upload", files=[("files", ("scene.zip", stream.getvalue(), "application/zip"))])
    assert response.status_code == 422, response.text
    assert not (client.app.state.store.root / "escape.json").exists()


def test_vrt_renamed_as_dem_is_rejected_before_gdal_open(client, monkeypatch, tmp_path):
    login(client)
    scene = clone(client)
    vrt = b'''<VRTDataset rasterXSize="1" rasterYSize="1"><SRS>EPSG:32637</SRS>
      <GeoTransform>400000,30,0,6200000,0,-30</GeoTransform><VRTRasterBand dataType="Float32" band="1">
      <SimpleSource><SourceFilename relativeToVRT="0">/vsicurl/https://example.invalid/private.tif</SourceFilename>
      <SourceBand>1</SourceBand><SrcRect xOff="0" yOff="0" xSize="1" ySize="1"/>
      <DstRect xOff="0" yOff="0" xSize="1" ySize="1"/></SimpleSource></VRTRasterBand></VRTDataset>'''
    opened = []
    def forbidden_open(*args, **kwargs):
        opened.append(args)
        raise AssertionError("Untrusted VRT reached GDAL")
    monkeypatch.setattr("gmp.api.inputs.rasterio.open", forbidden_open)
    response = client.post("/api/v1/scenes/upload", params={"base_scene_id": scene["id"]}, files=[("files", ("dem.tif", vrt, "image/tiff"))])
    assert response.status_code == 422, response.text
    assert "TIFF/BigTIFF" in response.json()["detail"]
    assert opened == []
    import shutil
    directory = tmp_path / "scene"
    shutil.copytree(DATASET / "scenarios/S00_smoke_rgb/input", directory)
    (directory / "dem.tif").write_bytes(vrt)
    assert terrain_covers(directory) is False
    assert opened == []


def test_truncated_tiff_returns_422(client):
    login(client)
    scene = clone(client)
    response = client.post("/api/v1/scenes/upload", params={"base_scene_id": scene["id"]}, files=[("files", ("dem.tif", b"II*\x00", "image/tiff"))])
    assert response.status_code == 422, response.text
    assert "damaged or unreadable GeoTIFF" in response.json()["detail"]


def test_tiff_driver_must_be_gtiff(tmp_path, monkeypatch):
    path = tmp_path / "dem.tif"
    path.write_bytes(b"II*\x00")
    class OtherDriver:
        driver = "VRT"
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    monkeypatch.setattr("gmp.api.inputs.rasterio.open", lambda *a, **kw: OtherDriver())
    with pytest.raises(ValueError, match="GeoTIFF driver"):
        with open_geotiff(path):
            pytest.fail("Unexpected raster driver accepted")


def test_loose_overlay_and_input_zip(client):
    login(client)
    scene = clone(client)
    layer = scene["layers"]["no_fly_zones"]
    response = client.post("/api/v1/scenes/upload", params={"base_scene_id": scene["id"]}, files=[("files", ("no_fly_zones.geojson", json.dumps(layer), "application/geo+json"))])
    assert response.status_code == 200, response.text
    assert response.json()["parent_id"] == scene["id"]
    downloaded = client.get(f"/api/v1/scenes/{scene['id']}/download")
    assert downloaded.status_code == 200
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        assert "dem.tif" in archive.namelist() and "fleet.json" in archive.namelist()
        assert len(archive.namelist()) == 11
        assert not any(name.endswith(".kml") for name in archive.namelist())


def survey_kml(scene, longitude_shift=.0001):
    feature = scene["layers"]["survey_areas"]["features"][0]
    coordinates = copy.deepcopy(feature["geometry"]["coordinates"][0])
    coordinates[0][0] += longitude_shift
    coordinates[-1][0] += longitude_shift
    root = ET.Element("kml")
    document = ET.SubElement(root, "Document")
    placemark = ET.SubElement(document, "Placemark")
    ET.SubElement(placemark, "name").text = feature["properties"]["id"]
    ring = ET.SubElement(ET.SubElement(ET.SubElement(placemark, "Polygon"), "outerBoundaryIs"), "LinearRing")
    ET.SubElement(ring, "coordinates").text = " ".join(f"{lon},{lat},0" for lon, lat in coordinates)
    place = ET.SubElement(document, "Placemark")
    ET.SubElement(place, "name").text = "BASE"
    ET.SubElement(ET.SubElement(place, "Point"), "coordinates").text = "37.53,55.70,0"
    return ET.tostring(root), coordinates


def test_scene_kml_overlay_changes_survey_and_preserves_site_layers(client):
    login(client)
    scene = clone(client)
    content, expected = survey_kml(scene)
    response = client.post("/api/v1/scenes/upload", params={"base_scene_id": scene["id"]}, files=[("files", ("scene.kml", content, "application/vnd.google-earth.kml+xml"))])
    assert response.status_code == 200, response.text
    changed = response.json()
    feature = changed["layers"]["survey_areas"]["features"][0]
    assert feature["geometry"]["coordinates"][0] == expected
    assert feature["properties"] == scene["layers"]["survey_areas"]["features"][0]["properties"]
    assert changed["layers"]["landing_sites"] == scene["layers"]["landing_sites"]
    assert changed["input_sha256"] != scene["input_sha256"]
    assert changed["validation"]["valid"], changed["validation"]
    assert any("survey polygons only" in warning for warning in changed["validation"]["warnings"])
    assert client.get(f"/api/v1/scenes/{scene['id']}").json()["layers"] == scene["layers"]


def test_explicit_geojson_precedence_over_scene_kml_is_reported(client):
    login(client)
    scene = clone(client)
    content, _ = survey_kml(scene)
    response = client.post("/api/v1/scenes/upload", params={"base_scene_id": scene["id"]}, files=[
        ("files", ("scene.kml", content, "application/vnd.google-earth.kml+xml")),
        ("files", ("survey_areas.geojson", json.dumps(scene["layers"]["survey_areas"]), "application/geo+json")),
    ])
    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed["layers"]["survey_areas"] == scene["layers"]["survey_areas"]
    assert any("survey_areas.geojson takes precedence over scene.kml" in warning for warning in changed["validation"]["warnings"])


@pytest.mark.parametrize("content", [b"<kml><Placemark>", b"<kml><Placemark><Point><coordinates/></Point></Placemark></kml>"])
def test_malformed_kml_returns_422(client, content):
    login(client)
    scene = clone(client)
    response = client.post("/api/v1/scenes/upload", params={"base_scene_id": scene["id"]}, files=[("files", ("scene.kml", content, "application/vnd.google-earth.kml+xml"))])
    assert response.status_code == 422, response.text


def test_kml_properties_preserve_types():
    kml = b'<kml><Placemark><name>SITE</name><ExtendedData><Data name="candidate"><value>false</value></Data><Data name="role"><value>both</value></Data></ExtendedData><Point><coordinates>37,55,0</coordinates></Point></Placemark></kml>'
    assert parse_kml(kml, "landing_sites")["features"][0]["properties"]["candidate"] is False
    with pytest.raises(ValueError):
        parse_kml(b'<!DOCTYPE bad><kml/>', "landing_sites")


def test_queue_is_bounded(client):
    login(client)
    scene = clone(client)
    for index in range(20):
        client.app.state.store.put_plan({"id": f"queued_{index}", "scene_id": scene["id"], "status": "queued", "created_at": scene["created_at"]})
    assert client.post("/api/v1/plans", json={"scene_id": scene["id"]}).status_code == 429


@pytest.mark.parametrize("options,expected", [({}, ("deep", 180)),
    ({"search_depth": "quick", "time_budget_s": 15}, ("quick", 15))])
def test_automatic_search_defaults_and_technical_overrides_reach_runner(client, options, expected):
    login(client)
    scene = clone(client)
    seen = []
    result = json.loads((DATASET / "scenarios/S00_smoke_rgb/expected/makespan/result.json").read_text())
    def runner(directory, request):
        seen.append(request)
        return result
    client.app.state.runner = runner
    response = client.post("/api/v1/plans", json={"scene_id": scene["id"], **options})
    assert response.status_code == 200, response.text
    record = await_plan(client, response.json()["id"])
    assert record["status"] == "SAFE", record
    assert (seen[0]["search_depth"], seen[0]["time_budget_s"]) == expected


def test_planner_timeout_never_becomes_safe_or_infeasible(client, monkeypatch):
    import subprocess
    login(client)
    scene = clone(client)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("planner", 180)
    monkeypatch.setattr("gmp.api.app.subprocess.Popen", timeout)
    response = client.post("/api/v1/plans", json={"scene_id": scene["id"], "time_budget_s": 1})
    record = await_plan(client, response.json()["id"])
    assert record["status"] == "ERROR" and record["certificate"] is None


def test_access_code_rotation_revokes_existing_sessions(client, monkeypatch):
    login(client)
    cookies = dict(client.cookies)
    monkeypatch.setenv("GMP_ACCESS_CODE", "rotated-test-code")
    client.__exit__(None, None, None)
    with TestClient(create_app(client.app.state.store.root, DATASET), base_url="https://testserver") as rotated:
        rotated.cookies.update(cookies)
        assert rotated.get("/api/v1/scenes").status_code == 401


def test_fixture_checks_and_exports_survive_restart(client, monkeypatch):
    login(client)
    scene = clone(client)
    response = client.post("/api/v1/plans", json={"scene_id": scene["id"], "mode": "fixture", "time_budget_s": 1})
    assert response.status_code == 200, response.text
    record = await_plan(client, response.json()["id"])
    assert record["status"] == "SAFE", record
    assert record["certificate"]["result_sha256"] == record["validation"]["result_sha256"]
    assert record["certificate"]["input_sha256"] == scene["input_sha256"]
    assert record["comparison"]["available"]
    plan_id = record["id"]
    for fmt in ("kml", "geojson", "mission", "certificate", "pdf", "docx"):
        exported = client.get(f"/api/v1/plans/{plan_id}/export", params={"format": fmt})
        assert exported.status_code == 200, (fmt, exported.text)
    exported = client.get(f"/api/v1/plans/{plan_id}/export", params={"format": "mission"}).json()
    assert exported["result"] == record["plan"]
    uid = record["plan"]["sorties"][0]["uav_id"]
    subset = client.get(f"/api/v1/plans/{plan_id}/export", params={"format": "geojson", "uav_id": uid}).json()
    assert {f["properties"]["uav_id"] for f in subset["features"]} == {uid}
    assert subset["context"]["input_sha256"] == scene["input_sha256"]
    assert len([f for f in subset["features"] if f["geometry"]["type"] == "Point"]) == sum(
        len(s["waypoints"]) for s in record["plan"]["sorties"] if s["uav_id"] == uid)
    assert client.get(f"/api/v1/plans/{plan_id}/export?format=kml&uav_id=unknown").status_code == 422
    client.__exit__(None, None, None)
    app = create_app(client.app.state.store.root, DATASET)
    with TestClient(app, base_url="https://testserver") as restarted:
        login(restarted)
        assert restarted.get(f"/api/v1/plans/{plan_id}/export?format=kml").status_code == 200
        assert restarted.get(f"/api/v1/scenes/{scene['id']}").status_code == 200
        saved = app.state.store.get_plan(plan_id)
        saved["plan"]["sorties"][0]["waypoints"][0]["lon"] += .001
        app.state.store.put_plan(saved)
        assert restarted.get(f"/api/v1/plans/{plan_id}/export?format=kml").status_code == 409


def test_unsafe_candidate_no_certificate_or_flight_export(client):
    login(client)
    scene = clone(client)
    result = json.loads((DATASET / "scenarios/S00_smoke_rgb/expected/makespan/result.json").read_text())
    result["sorties"] = []
    client.app.state.runner = lambda directory, request: result
    response = client.post("/api/v1/plans", json={"scene_id": scene["id"], "time_budget_s": 1})
    record = await_plan(client, response.json()["id"])
    assert record["status"] == "UNSAFE"
    assert record["certificate"] is None
    assert client.get(f"/api/v1/plans/{record['id']}/export?format=kml").status_code == 409
    assert client.get(f"/api/v1/plans/{record['id']}/export?format=pdf").status_code == 200


def test_restart_marks_interrupted_and_retention_is_durable(client):
    login(client)
    scene = clone(client)
    store = client.app.state.store
    store.put_plan({"id": "interrupted", "scene_id": scene["id"], "status": "running", "created_at": scene["created_at"], "certificate": {"wrong": True}})
    WorkspaceStore(store.root).recover()
    assert store.get_plan("interrupted")["status"] == "ERROR"
    assert store.get_plan("interrupted")["certificate"] is None
    record = store.get_scene(scene["id"])
    record["expires_at"] = "2000-01-01T00:00:00+00:00"
    store.put_scene(record)
    assert len(store.expire()) == 1
    assert store.get_scene(scene["id"]) is None
    assert (DATASET / "scenarios/S00_smoke_rgb/input/dem.tif").exists()


@pytest.mark.slow
def test_live_pipeline_and_verified_site_recommendation(client, monkeypatch):
    workspace = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(str(workspace / path) for path in ("src", "h1/h1_coverage/src", "h2/src")))
    login(client)
    scene = clone(client, "S06_alternate_site")
    response = client.post("/api/v1/plans", json={"scene_id": scene["id"], "mode": "live", "time_budget_s": 1})
    assert response.status_code == 200, response.text
    record = await_plan(client, response.json()["id"], limit=120)
    assert record["status"] == "INFEASIBLE", record
    assert len(record["recommendations"]) >= 1, record
    rec = record["recommendations"][0]
    assert rec["verified"] and rec["status"] == "SAFE"
    applied = client.post(f"/api/v1/plans/{record['id']}/recommendations/{rec['id']}/apply")
    assert applied.status_code == 200, applied.text
    assert applied.json()["parent_id"] == scene["id"]
    result = client.get(f"/api/v1/plans/{applied.json()['applied_plan_id']}").json()
    assert result["status"] == "SAFE" and result["certificate"]
    assert client.get(f"/api/v1/plans/{result['id']}/export?format=kml").status_code == 200


def test_h1_only_finishes_without_scheduling_or_certificate(client):
    login(client)
    scene = clone(client)
    response = client.post('/api/v1/plans', json={'scene_id': scene['id'], 'mode': 'h1'})
    assert response.status_code == 200, response.text
    record = await_plan(client, response.json()['id'])
    assert record['status'] == 'COMPLETED', record.get('error')
    assert record['validation'] is None and record['certificate'] is None
    assert record['plan']['h1_output']['task_count'] > 0
    assert record['plan']['h1_bundle']['tasks']
    assert not {'h2', 'h3'}.intersection(e['stage'] for e in record['progress'])
    assert record['plan']['provenance']['h2_executed'] is False
    assert 'sorties' not in record['plan']
    assert client.get(f"/api/v1/plans/{record['id']}/export?format=kml").status_code == 409
    directory = Path(record['log_dir'])
    manifest = read_json(directory / 'run.json')
    assert manifest['status'] == 'COMPLETED'
    assert manifest['run_code'] == record['run_code']
    assert manifest['task_count'] == len(record['plan']['h1_bundle']['tasks'])
    assert manifest['transect_count'] > 0
    assert read_json(directory / 'scenario.json')['id'] == scene['id']
    assert read_json(directory / 'h1_h2.bundle.json') == record['plan']['h1_bundle']
    download = client.get(f"/api/v1/plans/{record['id']}/log/h1_h2.bundle.json")
    assert download.status_code == 200 and download.json() == record['plan']['h1_bundle']
    assert client.get(f"/api/v1/plans/{record['id']}/log/nope.json").status_code == 404
    # Cancelling a completed run is idempotent and preserves its result.
    assert client.post(f"/api/v1/plans/{record['id']}/cancel").json()['status'] == 'COMPLETED'


def test_cancel_kills_worker_and_skips_queued_run(client, monkeypatch):
    import subprocess
    import sys
    login(client)
    scene = clone(client)
    processes = []
    original = subprocess.Popen

    def slow_worker(*args, **kwargs):
        process = original([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr('gmp.api.app.subprocess.Popen', slow_worker)
    first = client.post('/api/v1/plans', json={'scene_id': scene['id'], 'mode': 'h1'}).json()['id']
    end = time.monotonic() + 5
    while not processes and time.monotonic() < end:
        time.sleep(.02)
    assert len(processes) == 1
    second = client.post('/api/v1/plans', json={'scene_id': scene['id'], 'mode': 'h1'}).json()['id']
    for plan_id in (second, first):
        assert client.post(f'/api/v1/plans/{plan_id}/cancel').json()['status'] == 'CANCELLED'
    end = time.monotonic() + 5
    while processes[0].poll() is None and time.monotonic() < end:
        time.sleep(.02)
    assert processes[0].poll() is not None, 'Worker must actually stop'
    time.sleep(.3)
    assert len(processes) == 1, 'Cancelled queued run must never start'
    for plan_id in (first, second):
        record = client.get(f'/api/v1/plans/{plan_id}').json()
        assert record['status'] == 'CANCELLED'
        assert record['certificate'] is None
        assert read_json(Path(record['log_dir']) / 'run.json')['status'] == 'CANCELLED'
        assert not (Path(record['log_dir']) / 'h1_h2.bundle.json').exists()
        assert client.post(f'/api/v1/plans/{plan_id}/cancel').json()['status'] == 'CANCELLED'


def test_saved_scenarios_replace_revision_and_survive_restart(client):
    login(client)
    original_hash = input_hash(DATASET / 'scenarios/S00_smoke_rgb/input')
    scene = clone(client)
    assert client.get('/api/v1/scenes?saved_only=true').json()['scenes'] == []
    scene = client.post(f"/api/v1/scenes/{scene['id']}/save-as", json={"name": "Сохранённый сценарий"}).json()
    saved = scene
    assert saved['saved'] and saved['expires_at'] is None
    fleet = copy.deepcopy(scene['fleet'])
    fleet['uavs'][0]['ground_speed_kmh'] = 65
    response = client.post(f"/api/v1/scenes/{scene['id']}/versions", json={
        'name': 'Мой сценарий', 'fleet': fleet, 'auto_dem': False})
    assert response.status_code == 200, response.text
    revision = response.json()
    assert not revision['saved']
    assert client.get('/api/v1/scenes?saved_only=true').json()['scenes'][0]['id'] == scene['id']
    assert client.post(f"/api/v1/scenes/{revision['id']}/save").status_code == 200
    assert client.post(f"/api/v1/scenes/{revision['id']}/save").status_code == 200
    listing = client.get('/api/v1/scenes?saved_only=true').json()
    assert listing['retention_days'] is None
    assert len(listing['scenes']) == 1
    assert listing['scenes'][0]['name'] == 'Мой сценарий'
    assert listing['scenes'][0]['library_id'] == scene['library_id']
    assert input_hash(DATASET / 'scenarios/S00_smoke_rgb/input') == original_hash
    client.__exit__(None, None, None)
    with TestClient(create_app(client.app.state.store.root, DATASET), base_url='https://testserver') as restarted:
        login(restarted)
        records = restarted.get('/api/v1/scenes?saved_only=true').json()['scenes']
        assert [r['id'] for r in records] == [revision['id']]
        assert restarted.get(f"/api/v1/scenes/{revision['id']}").json()['fleet'] == fleet
        assert restarted.delete(f"/api/v1/scenes/{revision['id']}").status_code == 200
        assert restarted.get('/api/v1/scenes?saved_only=true').json()['scenes'] == []


def test_blank_and_independent_copy_can_be_saved(client):
    login(client)
    blank = client.post('/api/v1/scenes', json={'name': 'С нуля'}).json()
    assert blank['fleet']['uavs'] == []
    assert all(not layer['features'] for layer in blank['layers'].values())
    assert not blank['validation']['valid']
    assert client.post(f"/api/v1/scenes/{blank['id']}/save").status_code == 200
    scene = clone(client)
    scene = client.post(f"/api/v1/scenes/{scene['id']}/save-as", json={"name": "Основа копии"}).json()
    copied = client.post(f"/api/v1/scenes/{scene['id']}/copy").json()
    assert copied['library_id'] != scene['library_id']
    assert not copied['saved']
    assert copied['layers'] == scene['layers']
    assert client.post(f"/api/v1/scenes/{copied['id']}/save").status_code == 200
    assert len(client.get('/api/v1/scenes?saved_only=true').json()['scenes']) == 3
    defaults = client.get('/api/v1/uav-models').json()['payload_defaults']
    assert {p['type'] for p in defaults} >= {'rgb', 'thermal', 'multispectral', 'lidar', 'geophysics'}


def test_templates_are_runnable_and_calculations_do_not_change_saved_input(client):
    login(client)
    template = clone(client)
    assert template['template_readonly'] and template['ready_to_run']
    assert client.post(f"/api/v1/scenes/{template['id']}/save").status_code == 409
    changed = client.post(f"/api/v1/scenes/{template['id']}/versions", json={'name': 'Changed template', 'auto_dem': False}).json()
    assert changed['template_readonly'] and not changed['ready_to_run']
    assert client.post(f"/api/v1/scenes/{changed['id']}/save").status_code == 409
    assert client.post(f"/api/v1/scenes/{changed['id']}/save-as", json={'name': '  '}).status_code == 422
    saved = client.post(f"/api/v1/scenes/{changed['id']}/save-as", json={'name': 'My flight'}).json()
    assert saved['saved'] and not saved['template_readonly']
    assert saved['library_id'] != template['library_id']
    assert 'last_run' not in saved and saved['ready_to_run']
    assert client.post(f"/api/v1/scenes/{saved['id']}/save-as", json={'name': 'My flight'}).status_code == 409
    response = client.post('/api/v1/plans', json={'scene_id': saved['id'], 'mode': 'h1'})
    run = await_plan(client, response.json()['id'])
    assert response.json()['run_code'] == run['run_code']
    assert run['status'] == 'COMPLETED'
    assert client.get(f"/api/v1/scenes/{saved['id']}").json() == saved
    assert 'last_run' not in client.get('/api/v1/scenes?saved_only=true').json()['scenes'][0]
    fleet = copy.deepcopy(saved['fleet'])
    fleet['uavs'][0]['ground_speed_kmh'] = 66
    changed = client.post(f"/api/v1/scenes/{saved['id']}/versions", json={'fleet': fleet, 'auto_dem': False}).json()
    changed = client.post(f"/api/v1/scenes/{changed['id']}/save").json()
    assert 'last_run' not in changed and changed['ready_to_run']
    other = client.post(f"/api/v1/scenes/{changed['id']}/save-as", json={'name': 'Independent'}).json()
    assert 'last_run' not in other and other['ready_to_run']
    assert len(client.get('/api/v1/scenes?saved_only=true').json()['scenes']) == 2


def test_existing_scenarios_get_template_protection_on_restart(client):
    login(client)
    template = clone(client)
    saved = client.post(f"/api/v1/scenes/{template['id']}/save-as", json={'name': 'Existing user scenario'}).json()
    store = client.app.state.store
    for scene in (template, saved):
        record = store.get_scene(scene['id'])
        record.pop('template_readonly')
        store.put_scene(record)
    client.__exit__(None, None, None)
    with TestClient(create_app(store.root, DATASET), base_url='https://testserver') as restarted:
        login(restarted)
        assert restarted.get(f"/api/v1/scenes/{template['id']}").json()['template_readonly']
        assert not restarted.get(f"/api/v1/scenes/{saved['id']}").json()['template_readonly']
        assert restarted.post(f"/api/v1/scenes/{template['id']}/save").status_code == 409
        assert restarted.post(f"/api/v1/scenes/{saved['id']}/save").status_code == 200


def test_terrain_preview_uses_scene_file_and_requires_auth(client):
    assert client.get('/api/v1/scenes/unknown/terrain-preview').status_code == 401
    login(client)
    flat = clone(client)
    assert flat['terrain']['source'] == 'test'
    assert flat['terrain']['covers_scene']
    preview = client.get(f"/api/v1/scenes/{flat['id']}/terrain-preview")
    assert preview.status_code == 200, preview.text
    info = preview.json()
    assert info['flat'] and info['min_m'] == info['max_m'] == 160
    from PIL import Image
    image = client.get(info['image_url'])
    assert image.headers['content-type'] == 'image/png'
    with Image.open(io.BytesIO(image.content)) as png:
        assert png.size == (info['width'], info['height'])
        assert max(png.size) <= 768
    assert client.get(f"/api/v1/scenes/{flat['id']}").json()['input_sha256'] == flat['input_sha256']
    real = clone(client, 'S12_MSU_4bpla_4zones')
    assert real['terrain']['source'] == 'copernicus'
    real_info = client.get(f"/api/v1/scenes/{real['id']}/terrain-preview").json()
    assert not real_info['flat'] and real_info['max_m']-real_info['min_m'] > 140
    assert len(real_info['coordinates']) == 4


def test_uploaded_dem_source_overrides_template_metadata(client):
    login(client)
    base = clone(client, 'S12_MSU_4bpla_4zones')
    dem = (DATASET/'scenarios/S01_msu_100km2/input/dem.tif').read_bytes()
    response = client.post('/api/v1/scenes/upload', params={'base_scene_id':base['id']},
                           files={'files':('dem.tif',dem,'image/tiff')})
    assert response.status_code == 200, response.text
    scene = response.json()
    assert scene['terrain']['source'] == 'user'
    assert scene['metadata']['terrain']['kind'] == 'user_supplied'
    # A later geometry-only import retains the file's source.
    layer = json.dumps(scene['layers']['survey_areas']).encode()
    inherited = client.post('/api/v1/scenes/upload', params={'base_scene_id':scene['id']},
                            files={'files':('survey_areas.geojson',layer,'application/json')})
    assert inherited.status_code == 200, inherited.text
    assert inherited.json()['terrain']['source'] == 'user'


def test_missing_dem_import_waits_for_explicit_acquisition(client, monkeypatch):
    import importlib
    module = importlib.import_module('gmp.api.app')
    calls = []
    def acquire(directory, cache):
        import shutil
        calls.append(directory)
        shutil.copy2(DATASET/'scenarios/S00_smoke_rgb/input/dem.tif', directory/'dem.tif')
        metadata = read_json(directory/'metadata.json')
        metadata.update(real_elevation=True, terrain={'kind':'real_dsm'})
        write_json(directory/'metadata.json',metadata)
        return metadata['terrain']
    monkeypatch.setattr(module,'acquire_terrain',acquire)
    login(client)
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        for path in (DATASET/'scenarios/S00_smoke_rgb/input').iterdir():
            if path.is_file() and path.name != 'dem.tif':archive.writestr(path.name,path.read_bytes())
    response=client.post('/api/v1/scenes/upload',files={'files':('scene.zip',stream.getvalue(),'application/zip')})
    assert response.status_code == 200, response.text
    scene=response.json()
    assert scene['terrain']['source']=='missing' and not scene['validation']['valid']
    assert not calls
    assert client.get(f"/api/v1/scenes/{scene['id']}/terrain-preview").status_code==404
    result=client.post(f"/api/v1/scenes/{scene['id']}/terrain")
    assert result.status_code==200, result.text
    assert len(calls)==1 and result.json()['terrain']['source']=='copernicus'
    assert result.json()['terrain']['covers_scene']
    assert client.get(f"/api/v1/scenes/{scene['id']}").json()['terrain']['source']=='missing'


def test_explicit_flat_terrain_is_offline_and_preserves_original(client, monkeypatch):
    import importlib
    module = importlib.import_module('gmp.api.app')
    def forbidden(*args, **kwargs):
        pytest.fail('Flat terrain must not download DSM')
    monkeypatch.setattr(module, 'acquire_terrain', forbidden)
    login(client)
    original = clone(client, 'S12_MSU_4bpla_4zones')
    response = client.post(f"/api/v1/scenes/{original['id']}/terrain/flat")
    assert response.status_code == 200, response.text
    flat = response.json()
    assert flat['id'] != original['id'] and flat['parent_id'] == original['id']
    assert flat['terrain']['source'] == 'test' and flat['terrain']['covers_scene']
    assert flat['validation']['valid'], flat['validation']
    assert flat['metadata']['terrain']['selection'] == 'user_requested_flat'
    assert flat['metadata']['real_elevation'] is False
    preview = client.get(f"/api/v1/scenes/{flat['id']}/terrain-preview").json()
    assert preview['flat'] and preview['min_m'] == preview['max_m'] == 0
    unchanged = client.get(f"/api/v1/scenes/{original['id']}").json()
    assert unchanged['input_sha256'] == original['input_sha256']
    assert unchanged['terrain']['source'] == 'copernicus'


def test_template_and_uploaded_files_use_same_live_pipeline(client):
    from gmp.api.planner_adapter import build_live
    login(client)
    template = clone(client)
    assert template['ready_to_run'] and template['template_readonly']
    package = client.get(f"/api/v1/scenes/{template['id']}/download").content
    imported_response = client.post('/api/v1/scenes/upload', files=[('files', ('input.zip', package, 'application/zip'))])
    assert imported_response.status_code == 200, imported_response.text
    imported = imported_response.json()
    assert imported['ready_to_run'] and not imported['template_readonly']
    assert imported['validation']['valid'] == template['validation']['valid']
    for field in ('layers', 'fleet', 'mission', 'payload_catalog'):
        assert imported[field] == template[field]
    client.app.state.runner = lambda directory, req: build_live(directory, DATASET, req['objective'], req['time_budget_s'], req['seed'], search_depth=req['search_depth'])
    results = []
    for scene in (template, imported):
        before = client.get(f"/api/v1/scenes/{scene['id']}").json()
        response = client.post('/api/v1/plans', json={'scene_id': scene['id'], 'mode': 'live'})
        assert response.status_code == 200, response.text
        result = await_plan(client, response.json()['id'], limit=60)
        assert result['status'] == 'SAFE', result.get('error') or result.get('validation')
        assert client.get(f"/api/v1/scenes/{scene['id']}").json() == before
        results.append(result)
    assert results[0]['plan']['provenance']['routing']['solution_sha256'] == results[1]['plan']['provenance']['routing']['solution_sha256']
    assert results[0]['metrics'] == results[1]['metrics']
    version = client.post(f"/api/v1/scenes/{imported['id']}/versions", json={'name': 'Changed', 'auto_dem': False}).json()
    assert not version['ready_to_run']
    assert client.post(f"/api/v1/scenes/{version['id']}/save").json()['ready_to_run']
