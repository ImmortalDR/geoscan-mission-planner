import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gmp.api.app import create_app
from gmp.api.auth import password_hash, verify_password
from gmp.api.observability import EventLog
from gmp.api.runtime import capacity, cgroup_limits, worker_environment

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "integration-test-password"


@pytest.fixture
def named_app(tmp_path, monkeypatch):
    auth_file = tmp_path / "users.json"
    digest = password_hash(PASSWORD)
    auth_file.write_text(json.dumps({"users": {r: {"role": r, "password_hash": digest} for r in ("viewer", "planner", "admin")}}))
    monkeypatch.delenv("GMP_ACCESS_CODE", raising=False)
    monkeypatch.delenv("GMP_DATABASE_URL", raising=False)
    monkeypatch.setenv("GMP_AUTH_FILE", str(auth_file))
    app = create_app(tmp_path / "state", ROOT / "data")
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def login(client, role):
    response = client.post("/api/v1/auth/login", json={"username": role, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response


def test_server_enforces_roles_and_audits_without_credentials(named_app):
    c = named_app
    login(c, "viewer")
    assert c.get("/api/v1/scenarios").status_code == 200
    assert c.post("/api/v1/scenes").status_code == 403
    login(c, "planner")
    scene = c.post("/api/v1/scenes?scenario_id=S00_smoke_rgb").json()
    assert c.delete(f"/api/v1/scenes/{scene['id']}").status_code == 403
    login(c, "admin")
    assert c.delete(f"/api/v1/scenes/{scene['id']}").status_code == 200
    content = "".join(p.read_text() for p in (c.app.state.store.root / "logs").glob("*.jsonl"))
    assert PASSWORD not in content and c.headers["X-CSRF-Token"] not in content
    assert '"event": "login_success"' in content


def test_password_hashes_are_salted_and_checked():
    a, b = password_hash(PASSWORD), password_hash(PASSWORD)
    assert a != b and PASSWORD not in a
    assert verify_password(PASSWORD, a)
    assert not verify_password("incorrect", a)
    assert not verify_password(PASSWORD, "broken")


def test_log_cap_and_parallel_writes(tmp_path):
    log = EventLog(tmp_path / "logs", limit_bytes=12000)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: log.emit("load", request_id=str(n), detail="x"*100), range(1000)))
    files = list(log.root.glob("*.jsonl"))
    assert sum(p.stat().st_size for p in files) <= 12000
    assert all(json.loads(line)["event"] == "load" for p in files for line in p.read_text().splitlines())
    assert (log.root / "service.jsonl").stat().st_size > 0


def test_run_artifacts_share_log_budget(tmp_path):
    log = EventLog(tmp_path / "logs", limit_bytes=12000)
    for index in range(20):
        log.write_json(log.root / "runs" / str(index) / "run.json", {"data": "x"*3000})
        log.emit("artifact", plan_id=str(index))
    assert sum(p.stat().st_size for p in log.root.rglob("*") if p.is_file()) <= 12000
    assert not (log.root / "runs/0/run.json").exists()
    assert (log.root / "runs/19/run.json").exists()
    log.write_json(log.root / "oversized.json", {"data": "x"*15000})
    assert not (log.root / "oversized.json").exists()


def test_hardware_profiles_and_secret_free_worker(monkeypatch):
    monkeypatch.delenv("GMP_WORKERS", raising=False)
    monkeypatch.delenv("GMP_QUEUE_LIMIT", raising=False)
    assert capacity({"cpus": 2, "memory_mb": 6000})["workers"] == 1
    assert capacity({"cpus": 16, "memory_mb": 65536})["workers"] == 8
    monkeypatch.setenv("GMP_WORKERS", "1000")
    assert capacity({"cpus": 16, "memory_mb": 65536})["workers"] == 8
    monkeypatch.setenv("GMP_DATABASE_URL", "secret")
    monkeypatch.setenv("GMP_AUTH_FILE", "secret")
    assert not {"GMP_DATABASE_URL", "GMP_AUTH_FILE"} & worker_environment().keys()
    assert worker_environment()["OPENBLAS_NUM_THREADS"] == "1"


def test_systemd_parent_resource_limits_are_honored(tmp_path):
    service = tmp_path / "system.slice/geoscan.service"
    service.mkdir(parents=True)
    (tmp_path / "system.slice/memory.max").write_text(str(3 * 1024**3))
    (service / "memory.max").write_text("max")
    (service / "cpu.max").write_text("200000 100000")
    assert cgroup_limits(16, 64 * 1024**3, tmp_path, "0::/system.slice/geoscan.service") == (2, 3 * 1024**3)


def test_second_api_process_is_rejected_without_revoking_sessions(named_app):
    c = named_app
    login(c, "admin")
    with pytest.raises(RuntimeError, match="already has an API process"):
        with TestClient(create_app(c.app.state.store.root, ROOT / "data")):
            pass
    assert c.get("/api/v1/scenarios").status_code == 200


def test_document_access_and_csrf_are_enforced(named_app):
    c = named_app
    assert c.get("/api/v1/documentation").status_code == 401
    response = login(c, "admin")
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert c.get("/api/v1/documentation").status_code == 200
    for path in ("../../etc/passwd", "src/gmp/api/auth.py", "/etc/passwd"):
        assert c.get("/api/v1/documentation/file", params={"path": path}).status_code == 404
    del c.headers["X-CSRF-Token"]
    assert c.post("/api/v1/scenes?scenario_id=S00_smoke_rgb").status_code == 403


def test_malformed_auth_and_oversized_login_are_rejected(named_app):
    c = named_app
    assert c.post("/api/v1/auth/login", json=[]).status_code == 400
    assert c.post("/api/v1/auth/login", json={"username": ["admin"], "password": PASSWORD}).status_code == 401
    assert c.post("/api/v1/auth/login", json={"password": "x" * 2048}).status_code == 413


def test_offline_terrain_does_not_contact_provider(tmp_path, monkeypatch):
    from gmp.api.inputs import acquire_terrain
    monkeypatch.setenv("GMP_OFFLINE", "1")
    monkeypatch.setattr("gmp.api.inputs.urlopen", lambda *a, **k: pytest.fail("Network called in offline mode"))
    with pytest.raises(ValueError, match="Offline"):
        acquire_terrain(ROOT / "data/scenarios/S00_smoke_rgb/input", tmp_path / "cache")
