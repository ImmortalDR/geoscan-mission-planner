import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from gmp.api.postgres_store import PostgresWorkspaceStore
from gmp.api.workspace_store import WorkspaceStore, utcnow


@pytest.fixture
def pg(tmp_path):
    dsn = os.environ.get("GMP_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set GMP_TEST_DATABASE_URL to a disposable database ending in _test")
    with psycopg.connect(dsn) as db:
        assert db.info.dbname.endswith("_test"), "Refusing to clear a non-test database"
    store = PostgresWorkspaceStore(tmp_path / "pg-state", dsn)
    with store.connect() as db:
        db.execute("TRUNCATE scenes,plans,sessions,login_attempts,configuration")
    yield store


def test_postgres_durable_jsonb_and_session_concurrency(pg):
    scene = {"id": "scene", "name": "Scene", "created_at": utcnow(), "input_sha256": "hash"}
    pg.put_scene(scene)
    saved = pg.save_scenario("scene")
    assert saved["saved"] and saved["expires_at"] is None
    assert pg.rename_scenario("scene", "New")["name"] == "New"
    pg.put_plan({"id": "plan", "scene_id": "scene", "status": "running", "created_at": utcnow(), "mode": "h1", "input_sha256": "hash", "plan": {"large": "x"*100000}})
    assert "plan" not in pg.plan_headers()[0]
    assert pg.plan_status("plan") == "running" and pg.plan_status("missing") is None
    assert pg.latest_h1_run(saved)["matches_input"]
    with ThreadPoolExecutor(max_workers=16) as pool:
        tokens = list(pool.map(lambda n: pg.create_session(f"user{n}", "viewer"), range(32)))
    assert len({t for t, _ in tokens}) == 32
    assert all(pg.principal(t)["role"] == "viewer" for t, _ in tokens)
    pg.recover()
    assert pg.get_plan("plan")["status"] == "ERROR"
    assert PostgresWorkspaceStore(pg.root, pg.dsn).get_scene("scene")["name"] == "New"
    assert pg.get_scene("' OR 1=1 --") is None


def test_postgres_full_api_and_restart(pg, monkeypatch):
    from gmp.api.app import create_app
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("GMP_DATABASE_URL", pg.dsn)
    monkeypatch.setenv("GMP_ACCESS_CODE", "postgres-api-test-code")
    monkeypatch.delenv("GMP_AUTH_FILE", raising=False)
    with TestClient(create_app(pg.root, root / "data"), base_url="https://testserver") as client:
        auth = client.post("/api/v1/auth/login", json={"code": "postgres-api-test-code"}).json()
        client.headers["X-CSRF-Token"] = auth["csrf_token"]
        assert client.get("/health/ready").json()["storage"] == "postgresql"
        scene = client.post("/api/v1/scenes?scenario_id=S00_smoke_rgb").json()
        response = client.post(f"/api/v1/scenes/{scene['id']}/save-as", json={"name": "Persistent PG scene"})
        assert response.status_code == 200, response.text
        saved = response.json()
    with TestClient(create_app(pg.root, root / "data"), base_url="https://testserver") as client:
        auth = client.post("/api/v1/auth/login", json={"code": "postgres-api-test-code"}).json()
        client.headers["X-CSRF-Token"] = auth["csrf_token"]
        assert client.get(f"/api/v1/scenes/{saved['id']}").json()["saved"]


def test_migration_copies_records_files_and_invalidates_sessions(pg, tmp_path):
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "scripts/migrate_postgres.py"
    spec = importlib.util.spec_from_file_location("migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = WorkspaceStore(tmp_path / "sqlite")
    source.put_scene({"id": "one", "created_at": utcnow(), "name": "One"})
    source.put_plan({"id": "run", "scene_id": "one", "status": "SAFE", "created_at": utcnow(), "certificate": {"sha256": "unchanged"}})
    source.create_session()
    (source.root / "scenes/one").mkdir(parents=True)
    (source.root / "scenes/one/input.json").write_text('{"input":1}')
    result = module.migrate(source.root, pg.root, pg.dsn, execute=True)
    assert result["files_verified"] == 1 and result["sessions_migrated"] == 0
    assert pg.get_plan("run")["certificate"] == {"sha256": "unchanged"}
    assert (pg.root / "scenes/one/input.json").read_text() == '{"input":1}'


def test_connection_setup_retries_once_without_replaying_sql(monkeypatch):
    from gmp.api import postgres_store
    store = object.__new__(PostgresWorkspaceStore)
    store.dsn = "unused"
    attempts = []
    connection = object()
    def connect(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise psycopg.OperationalError("temporary startup timeout")
        return connection
    monkeypatch.setattr(postgres_store.psycopg, "connect", connect)
    monkeypatch.setattr(postgres_store.time, "sleep", lambda _: None)
    assert store.connect() is connection and len(attempts) == 2
