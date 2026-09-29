"""Public deployments bootstrap a normal CSRF-protected planner session."""
from fastapi.testclient import TestClient
import pytest

from gmp.api.app import create_app
from gmp.api.auth import Authentication


def test_public_workspace_without_password(tmp_path, monkeypatch):
    monkeypatch.setenv("GMP_PUBLIC_ACCESS", "1")
    monkeypatch.delenv("GMP_ACCESS_CODE", raising=False)
    monkeypatch.delenv("GMP_AUTH_FILE", raising=False)
    app = create_app(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/api/v1/auth/status")
        status = response.json()
        assert status["authenticated"] and status["public_access"]
        assert status["role"] == "planner"
        assert all(flag in response.headers["set-cookie"] for flag in ("Secure", "HttpOnly", "SameSite=strict"))
        assert client.get("/api/v1/auth/status").json()["csrf_token"] == status["csrf_token"]
        assert len(client.get("/api/v1/scenarios").json()["scenarios"]) == 20
        assert client.get("/api/v1/scenes").json()["scenes"] == []
        assert client.get("/api/v1/admin/status").status_code == 403
        assert client.post("/api/v1/scenes", params={"scenario_id": "S00_smoke_rgb"}).status_code == 403
        client.headers["X-CSRF-Token"] = status["csrf_token"]
        scene = client.post("/api/v1/scenes", params={"scenario_id": "S00_smoke_rgb"})
        assert scene.status_code == 200, scene.text
        saved = client.post(f"/api/v1/scenes/{scene.json()['id']}/save-as", json={"name": "Public project"})
        assert saved.status_code == 200, saved.text


def test_public_access_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("GMP_PUBLIC_ACCESS", raising=False)
    monkeypatch.setenv("GMP_ACCESS_CODE", "test-code")
    with TestClient(create_app(tmp_path), base_url="https://testserver") as client:
        status = client.get("/api/v1/auth/status")
        assert not status.json()["authenticated"]
        assert not status.json()["public_access"]
        assert "set-cookie" not in status.headers
        assert client.get("/api/v1/scenarios").status_code == 401


def test_disabling_public_access_revokes_guest_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("GMP_PUBLIC_ACCESS", "1")
    monkeypatch.setenv("GMP_ACCESS_CODE", "test-code")
    with TestClient(create_app(tmp_path), base_url="https://testserver") as client:
        client.get("/api/v1/auth/status")
        cookies = dict(client.cookies)
    monkeypatch.delenv("GMP_PUBLIC_ACCESS")
    with TestClient(create_app(tmp_path), base_url="https://testserver") as client:
        client.cookies.update(cookies)
        assert not client.get("/api/v1/auth/status").json()["authenticated"]
        assert client.get("/api/v1/scenarios").status_code == 401


def test_internal_deployment_rejects_public_access(monkeypatch):
    monkeypatch.setenv("GMP_PUBLIC_ACCESS", "1")
    monkeypatch.setenv("GMP_DEPLOYMENT_MODE", "internal")
    with pytest.raises(ValueError, match="Internal deployment"):
        Authentication()
