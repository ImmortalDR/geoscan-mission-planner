"""Authenticated, persistent H3 workspace and bounded planning queue."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import hmac
import importlib.util
import json
import os
import shutil
import subprocess
from gmp.api.worker_process import BoundedProcess
import sys
import time
import threading
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .inputs import EMPTY, INPUT_FILES, LAYERS, MAX_UPLOAD, acquire_terrain, create_flat_terrain, check_documents, input_hash, legacy_input_hash, read_json, snapshot, terrain_covers, unpack_upload, validate_input, write_json
from .run_log import update_run_log as write_run_log
from .scenario_catalog import scenario_directories, scenario_directory, template_presentation
from .terrain_preview import terrain_summary, terrain_preview
from .workspace_store import WorkspaceStore, expiry, utcnow
from .auth import Authentication, permitted
from .observability import EventLog
from .runtime import capacity, worker_environment
from .configuration import database_url

ROOT = Path(__file__).resolve().parents[3]
COOKIE = "gmp_h3_session"


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_id: str = Field(min_length=1, max_length=128)
    objective: Literal["makespan", "total_flight"] = "makespan"
    time_budget_s: float = Field(default=180, ge=1, le=1800, allow_inf_nan=False)
    search_depth: Literal["quick", "standard", "deep"] = "deep"
    seed: int = Field(default=20260918, ge=0, le=2147483647)
    mode: Literal["live", "fixture", "h1"] = "live"


def create_app(data_dir: Path | None = None, dataset_dir: Path | None = None, web_dir: Path | None = None) -> FastAPI:
    data = Path(data_dir or os.environ.get("GMP_DATA_DIR", ROOT / "artifacts/h3_workspace")).resolve()
    dataset = Path(dataset_dir or os.environ.get("GMP_DATASET_DIR", ROOT / "data")).resolve()
    web = Path(web_dir or os.environ.get("GMP_WEB_DIR", ROOT / "web/h3"))
    dsn = database_url()
    if dsn:
        from .postgres_store import PostgresWorkspaceStore
        store = PostgresWorkspaceStore(data, dsn)
    else:
        if os.environ.get("GMP_DEPLOYMENT_MODE") == "internal":
            raise ValueError("Internal deployment requires GMP_DATABASE_URL")
        store = WorkspaceStore(data)
    auth = Authentication()
    resources = capacity()
    logs = EventLog(data / "logs", int(os.environ.get("GMP_LOG_LIMIT_BYTES", "500000000")))
    executor = ThreadPoolExecutor(max_workers=resources["workers"], thread_name_prefix="h3-plan")
    admission, terrain_lock = threading.Lock(), threading.Lock()

    def update_run_log(record):
        write_run_log(record, write=logs.write_json)

    def cleanup():
        plans = store.plan_headers()
        for scene in store.expire():
            directory = data / "scenes" / scene["id"]
            if directory.is_dir():
                shutil.rmtree(directory)
            for plan in plans:
                if plan["scene_id"] == scene["id"]:
                    for job in (data / "jobs").glob(f"{plan['id']}*"):
                        if job.is_dir():
                            shutil.rmtree(job)

    @asynccontextmanager
    async def lifespan(application):
        lease = (data / ".instance.lock").open("a")
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lease.close()
            raise RuntimeError("This workspace already has an API process; use one API worker")
        database_lease = None
        if store.backend == "postgresql":
            database_lease = store.connect()
            database_lease.autocommit = True
            if not database_lease.execute("SELECT pg_try_advisory_lock(763505)").fetchone()[0]:
                database_lease.close()
                lease.close()
                raise RuntimeError("This PostgreSQL workspace already has an API process")
        logs.emit("service_start", workers=resources["workers"], release=os.environ.get("GMP_RELEASE_ID", "development"))
        store.bind_access_code(auth.version)
        existing = store.scenes()
        saved_libraries = {r.get("library_id", r["id"]) for r in existing if r.get("saved")}
        for scene in existing:
            if "template_readonly" not in scene:
                scene["template_readonly"] = bool(scene.get("scenario_id")) and scene.get("library_id", scene["id"]) not in saved_libraries
                store.put_scene(scene)
            directory = data / "scenes" / scene["id"] / "input"
            if not scene.get("input_hash_scheme") and directory.exists() and legacy_input_hash(directory) == scene.get("input_sha256"):
                scene.update(input_sha256=input_hash(directory), input_hash_scheme="h3-gate-v1")
                store.put_scene(scene)
                for plan in store.plans(scene["id"]):
                    plan["input_sha256"] = scene["input_sha256"]
                    store.put_plan(plan)
        store.recover()
        for header in store.plan_headers():
            if header.get("log_dir") and header["status"] == "ERROR":
                update_run_log(store.get_plan(header["id"]))
        cleanup()
        async def maintenance():
            while True:
                await asyncio.sleep(60)
                await asyncio.to_thread(cleanup)
                await asyncio.to_thread(logs.prune)
        task = asyncio.create_task(maintenance())
        try:
            yield
        finally:
            task.cancel()
            for header in store.plan_headers():
                if header["status"] in ("queued", "running"):
                    record = store.get_plan(header["id"])
                    record.update(status="CANCELLED", certificate=None)
                    store.put_plan(record)
            await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
            if database_lease is not None:
                database_lease.close()
            lease.close()

    app = FastAPI(
        title="Geoscan Integrated Mission Planner",
        version="3.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.state.store, app.state.dataset = store, dataset
    app.state.executor, app.state.runner = executor, None
    app.state.resources, app.state.logs = resources, logs
    from .documentation import documentation_router
    app.include_router(documentation_router(ROOT, web))
    if web.exists():
        app.mount("/static", StaticFiles(directory=str(web)), name="static")

    @app.middleware("http")
    async def security(request: Request, call_next):
        path = request.url.path
        length = request.headers.get("content-length")
        try:
            if length and int(length) > MAX_UPLOAD + 1024 * 1024:
                return JSONResponse({"detail": "Request exceeds 100 MiB"}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
        if request.method in ("POST", "PUT", "PATCH") and "application/json" in request.headers.get("content-type", ""):
            body, size = [], 0
            limit = 1024 if path == "/api/v1/auth/login" else 8 * 1024 * 1024
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    return JSONResponse({"detail": "JSON request exceeds size limit"}, status_code=413)
                body.append(chunk)
            request._body = b"".join(body)
        if path.startswith("/api/") and path not in ("/api/v1/auth/login", "/api/v1/auth/status"):
            if not auth.configured:
                return JSONResponse({"detail": "Access code is not configured"}, status_code=503)
            principal = await asyncio.to_thread(store.principal, request.cookies.get(COOKIE))
            if principal is None:
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
            request.state.principal = principal
            if not permitted(principal["role"], request.method, path):
                return JSONResponse({"detail": "This role cannot perform the operation"}, status_code=403)
            csrf = principal["csrf_token"]
            if request.method not in ("GET", "HEAD", "OPTIONS") and not hmac.compare_digest(request.headers.get("x-csrf-token", "").encode(), csrf.encode()):
                return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin" if path.startswith("/api/") else "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store" if path.startswith("/api/") else "no-cache"
        return response

    @app.middleware("http")
    async def audit(request: Request, call_next):
        started = time.monotonic()
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception as exc:
            await asyncio.to_thread(logs.emit, "request_error", request_id=request_id, error_type=type(exc).__name__)
            response = JSONResponse({"detail": "Internal service error", "request_id": request_id}, status_code=500)
        response.headers["X-Request-ID"] = request_id
        route = request.scope.get("route")
        principal = getattr(request.state, "principal", {})
        await asyncio.to_thread(logs.emit, "request", request_id=request_id, method=request.method,
                  route=getattr(route, "path", "unmatched"), status=response.status_code,
                  duration_ms=round((time.monotonic()-started)*1000, 3),
                  username=principal.get("username"), role=principal.get("role"))
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/", response_class=HTMLResponse)
    def index():
        path = web / "index.html"
        return HTMLResponse(path.read_text(encoding="utf-8") if path.exists() else "H3 interface is not installed", status_code=200 if path.exists() else 503)

    @app.get("/health/live")
    def live():
        return {"status": "live"}

    @app.get("/health/ready")
    def ready():
        store.health()
        components = {name: importlib.util.find_spec(name) is not None for name in ("h1_coverage", "h2")}
        valid = auth.configured and (dataset / "h3check.py").exists() and all(components.values())
        return JSONResponse({"status": "ready" if valid else "not_ready", "storage": store.backend,
                             "pipeline": "h1_coverage -> h2 -> h3", "components": components,
                             "release": os.environ.get("GMP_RELEASE_ID", "development")},
                            status_code=200 if valid else 503)

    def session_cookie(response: Response, request: Request, token: str):
        local_http = os.environ.get("GMP_ALLOW_LOCAL_HTTP") == "1" and request.url.hostname in {"localhost", "127.0.0.1", "::1"}
        response.set_cookie(COOKIE, token, secure=not local_http, httponly=True, samesite="strict", max_age=43200, path="/")

    @app.get("/api/v1/auth/status")
    def auth_status(request: Request, response: Response):
        principal = store.principal(request.cookies.get(COOKIE))
        if principal is None and auth.public:
            token, csrf = store.create_session(username="guest", role="planner")
            session_cookie(response, request, token)
            principal = {"username": "guest", "role": "planner", "csrf_token": csrf}
        return {"authenticated": principal is not None and auth.configured, "configured": auth.configured,
                "csrf_token": principal["csrf_token"] if principal else None,
                "username": principal["username"] if principal else None, "role": principal["role"] if principal else None,
                "named_users": bool(auth.users), "public_access": auth.public,
                "offline": os.environ.get("GMP_OFFLINE") == "1"}

    @app.post("/api/v1/auth/login")
    async def login(request: Request):
        if not auth.configured:
            raise HTTPException(503, "Access code is not configured")
        body = await request.body()
        if len(body) > 1024:
            raise HTTPException(413, "Login request too large")
        try:
            credentials = json.loads(body)
            if not isinstance(credentials, dict):
                raise ValueError("Expected object")
        except (ValueError, AttributeError):
            raise HTTPException(400, "Expected JSON object")
        ip = request.client.host if request.client else "unknown"
        username = credentials.get("username")
        if isinstance(username, str) and username:
            ip += ":" + hashlib.sha256(username.encode()).hexdigest()[:24]
        if not await asyncio.to_thread(store.login_allowed, ip):
            raise HTTPException(429, "Too many attempts; wait 15 minutes", headers={"Retry-After": "900"})
        principal = await asyncio.to_thread(auth.authenticate, credentials)
        if principal is None:
            logs.emit("login_failed", request_id=request.state.request_id)
            raise HTTPException(401, "Invalid access code")
        await asyncio.to_thread(store.login_success, ip)
        token, csrf = await asyncio.to_thread(store.create_session, **principal)
        logs.emit("login_success", **principal)
        response = JSONResponse({"authenticated": True, "csrf_token": csrf, "configured": True, **principal,
                                 "offline": os.environ.get("GMP_OFFLINE") == "1"})
        session_cookie(response, request, token)
        return response

    @app.post("/api/v1/auth/logout")
    def logout(request: Request):
        store.logout(request.cookies.get(COOKIE, ""))
        response = JSONResponse({"authenticated": False, "csrf_token": None})
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="strict")
        return response

    def catalog() -> list[dict]:
        items = []
        for directory in scenario_directories(dataset).values():
            spec = read_json(directory / "scenario.json")
            metadata = read_json(directory / "input/metadata.json")
            fleet = read_json(directory / "input/fleet.json")
            fleet_count = int(spec.get("fleet_count") or len(fleet.get("uavs") or []))
            legacy_path = legacy_bundle_for(directory)
            legacy = read_json(legacy_path) if legacy_path else {}
            items.append({
                "id": directory.name,
                "name": spec.get("title", directory.name),
                "description": metadata.get("description", ""),
                "purpose_ru": spec.get("purpose_ru") or metadata.get("description", ""),
                "role": spec.get("role") or "scenario",
                "catalog_badge_ru": spec.get("catalog_badge_ru") or f"{fleet_count} БВС",
                "fleet_count": fleet_count,
                "objectives": spec.get("objectives", ["makespan"]),
                "expected_status": spec.get("expected_status"),
                "real_elevation": metadata.get("real_elevation", False),
                "legacy_h2_id": legacy.get("scene_id") if legacy else None,
                "legacy_h2_bundle": legacy_path.name if legacy_path and legacy else None,
                "legacy_task_count": len(legacy.get("tasks", [])) if legacy else 0,
                "legacy_fleet_count": len(legacy.get("fleet", [])) if legacy else 0,
                **template_presentation(directory, spec, metadata),
            })
        # Keep real terrain and synthetic test templates in separate contiguous groups.
        items.sort(key=lambda item: (not item["real_elevation"], item["id"]))
        return items

    @app.get("/api/v1/scenarios")
    def scenarios():
        return {"scenarios": catalog()}

    @app.get("/api/v1/delivery")
    def delivery():
        archive = Path(os.environ.get("GMP_DELIVERY_FILE", "/opt/geoscan-h3/delivery.tar.gz"))
        if not archive.is_file():
            raise HTTPException(404, "Delivery archive is not installed")
        return FileResponse(archive, media_type="application/gzip", filename="geoscan-h3-delivery.tar.gz")

    @app.get("/api/v1/uav-models")
    def models():
        from gmp.kb.catalog import default_kb
        kb = default_kb()
        defaults = []
        for name in ("geoscan_201", "geoscan_401", "geoscan_701", "geoscan_801", "gemini"):
            profile = kb.profile({"geoscan_401": "geoscan_401_geo", "gemini": "geoscan_gemini"}.get(name, name))
            defaults.append({"id": "U01", "model": name, "class": profile.vehicle_class,
                "ground_speed_kmh": round(profile.planning_speed_ms * 3.6, 3),
                "operational_endurance_min": profile.operational_endurance_min, "max_wind_ms": profile.max_wind_ms,
                "payload_classes": profile.payload_classes, "energy_reserve_fraction": profile.energy_reserve_fraction,
                "horizontal_separation_m": profile.horizontal_separation_m, "vertical_separation_m": profile.vertical_separation_m,
                "turnaround_buffer_m": profile.turnaround_buffer_m, "start_site": "BASE", "landing_site": "BASE",
                "takeoff_time_s": profile.takeoff_s, "landing_time_s": profile.landing_s, "service_time_s": profile.service_s,
                "parameter_status": "catalog_derived_planning_assumption_not_flight_authorization",
                "catalog_provenance": profile.as_dict()})
        payloads = {}
        for directory in scenario_directories(dataset).values():
            path = directory / "input/payload_catalog.json"
            for profile in read_json(path).get("payload_profiles", []):
                payloads.setdefault(profile["type"], profile)
        return {"models": kb.summary(), "defaults": defaults, "payload_defaults": list(payloads.values())}

    def need_scene(identifier: str) -> dict:
        record = store.get_scene(identifier)
        if record is None:
            raise HTTPException(404, "Scene not found")
        return record

    def directory_for(record: dict) -> Path:
        return data / "scenes" / record["id"] / "input"

    def public_scene(record: dict) -> dict:
        directory = directory_for(record)
        report = validate_input(directory, dataset)
        if record.get("terrain_error"):
            report["warnings" if report["valid"] else "errors"].append(record["terrain_error"])
        # A calculation belongs to its open workspace, not to the saved input.
        ready = record.get("saved") or record.get("ready_to_run") or (
            record.get("template_readonly") and not record.get("parent_id"))
        return {**record, **snapshot(directory), "validation": report,
                "terrain": terrain_summary(directory), "ready_to_run": bool(ready)}

    def register(identifier: str, name: str, parent: dict | None = None, scenario_id: str | None = None, extra: dict | None = None) -> dict:
        record = {"id": identifier, "name": name[:160], "created_at": utcnow(), "expires_at": expiry(), "version": parent["version"] + 1 if parent else 1, "parent_id": parent["id"] if parent else None, "scenario_id": scenario_id if scenario_id is not None else parent.get("scenario_id") if parent else None, "input_sha256": input_hash(data / "scenes" / identifier / "input"), "input_hash_scheme": "h3-gate-v1", "library_id": parent.get("library_id", parent["id"]) if parent else identifier, "saved": False, "template_readonly": parent.get("template_readonly", False) if parent else bool(scenario_id), **(extra or {})}
        store.put_scene(record)
        return record

    def allocate(source: Path | None = None) -> tuple[str, Path]:
        if shutil.disk_usage(data).free < 512 * 1024 * 1024:
            raise HTTPException(507, "Workspace has less than 512 MiB free; delete old projects")
        identifier = uuid.uuid4().hex
        directory = data / "scenes" / identifier / "input"
        directory.mkdir(parents=True, mode=0o700)
        if source:
            for path in source.iterdir():
                if path.name in INPUT_FILES and path.is_file() and not path.is_symlink():
                    shutil.copy2(path, directory / path.name)
        return identifier, directory

    @app.get("/api/v1/scenes")
    def list_scenes(saved_only: bool = False):
        return {"scenes": [r for r in store.scenes() if not r.get("recommendation_only") and (not saved_only or r.get("saved"))],
                "retention_days": None if saved_only else 30}

    @app.post("/api/v1/scenes")
    async def create_scene(request: Request, scenario_id: str | None = None):
        raw = await request.body() if not scenario_id else b""
        body = json.loads(raw) if raw else {}
        if not isinstance(body, dict):
            raise ValueError("Expected JSON object")
        return await asyncio.to_thread(create_scene_files, scenario_id, body)

    def create_scene_files(scenario_id, body):
        if scenario_id:
            item = next((r for r in catalog() if r["id"] == scenario_id), None)
            if item is None:
                raise HTTPException(404, "Unknown scenario")
            source = scenario_directory(dataset, scenario_id) / "input"
            entries = [(p.name, p.read_bytes()) for p in sorted(source.iterdir())
                       if p.name in INPUT_FILES and p.is_file() and not p.is_symlink()]
            title = f'{item["display_code"]} · {item["display_name"]}'
            return upload_scene_files(entries, None, template=(scenario_id, title))
        else:
            identifier, directory = allocate(dataset / "scenarios/S00_smoke_rgb/input")
            for layer in LAYERS:
                write_json(directory / f"{layer}.geojson", EMPTY)
            write_json(directory / "fleet.json", {"uavs": []})
            (directory / "scene.kml").unlink(missing_ok=True)
            (directory / "dem.tif").unlink(missing_ok=True)
            metadata = read_json(directory / "metadata.json")
            metadata.update(scenario_id=identifier, description="User-created mission", terrain={}, real_elevation=False, user_overrides=True)
            write_json(directory / "metadata.json", metadata)
            record = register(identifier, str(body.get("name", "New mission")))
        return public_scene(record)

    @app.post("/api/v1/scenes/upload")
    async def upload_scene(files: list[UploadFile] = File(...), base_scene_id: str | None = None):
        if not 1 <= len(files) <= 20:
            raise HTTPException(413, "Upload 1 to 20 files")
        entries, total = [], 0
        for file in files:
            chunks = []
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD:
                    raise HTTPException(413, "Upload exceeds 100 MiB")
                chunks.append(chunk)
            entries.append((file.filename or "", b"".join(chunks)))
        return await asyncio.to_thread(upload_scene_files, entries, base_scene_id)

    def upload_scene_files(entries, base_scene_id, template=None):
        parent = need_scene(base_scene_id) if base_scene_id else None
        identifier, directory = allocate(directory_for(parent) if parent else None)
        try:
            uploaded_names = set()
            import_warnings = unpack_upload(entries, directory, uploaded_names)
            check_documents(directory)
            metadata = read_json(directory / "metadata.json")
            if not template:
                metadata["user_overrides"] = True
            if "dem.tif" in uploaded_names and not template:
                metadata["terrain"] = {"kind": "user_supplied", "filename": "dem.tif"}
                metadata["real_elevation"] = False
            elif parent and (directory / "dem.tif").exists():
                inherited = read_json(directory_for(parent) / "metadata.json")
                metadata["terrain"] = inherited.get("terrain", {})
                metadata["real_elevation"] = inherited.get("real_elevation", False)
            if import_warnings and not template:
                metadata["import_warnings"] = import_warnings
            if not template:
                write_json(directory / "metadata.json", metadata)
            record = register(identifier, template[1] if template else parent["name"] if parent else str(metadata.get("description", "Uploaded mission")),
                              parent=parent, scenario_id=template[0] if template else None,
                              extra={"ready_to_run": parent is None, "opened_from_files": not bool(template) and parent is None})
            return public_scene(record)
        except Exception:
            shutil.rmtree(directory.parent)
            raise

    def legacy_bundle_for(scenario_directory: Path) -> Path | None:
        candidates = [scenario_directory / "legacy_h2_bundle.json"]
        fixture_root = ROOT / "fixtures/h1_h2"
        stem = scenario_directory.name
        candidates.extend([fixture_root / f"{stem}.bundle.json"])
        prefix = stem.split("_", 1)[0]
        candidates.extend(sorted(fixture_root.glob(f"{prefix}_*.bundle.json")))
        return next((path for path in candidates if path.is_file() and "contract" not in path.name), None)

    @app.get("/api/v1/scenes/{scene_id}")
    def get_scene(scene_id: str):
        return public_scene(need_scene(scene_id))

    @app.post("/api/v1/scenes/{scene_id}/save")
    def save_scenario(scene_id: str):
        record = need_scene(scene_id)
        if record.get("template_readonly"):
            raise HTTPException(409, "Template is read-only; use Save As")
        # Saving an incomplete draft is allowed; computation still validates the input.
        return public_scene(store.save_scenario(scene_id))

    @app.post("/api/v1/scenes/{scene_id}/rename")
    def rename_scenario(scene_id: str, body: dict[str, Any]):
        record = need_scene(scene_id)
        if record.get("template_readonly"):
            raise HTTPException(409, "Template is read-only; use Save As")
        name = body.get("name")
        if set(body) != {"name"} or not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
            raise HTTPException(422, "Provide a scenario name (1–160 characters)")
        return public_scene(store.rename_scenario(scene_id, name.strip()))

    @app.post("/api/v1/scenes/{scene_id}/copy")
    def copy_scenario(scene_id: str):
        parent = need_scene(scene_id)
        identifier, directory = allocate(directory_for(parent))
        return public_scene(register(identifier, f"{parent['name']} — копия", scenario_id=parent.get("scenario_id"), extra={"template_readonly": False}))

    @app.post("/api/v1/scenes/{scene_id}/validate")
    def scene_validate(scene_id: str):
        return validate_input(directory_for(need_scene(scene_id)), dataset)

    @app.get("/api/v1/scenes/{scene_id}/download")
    def scene_download(scene_id: str):
        import io
        import zipfile
        from gmp.safety.h3_gate import INPUT_FILES as CANONICAL_INPUT_FILES
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in directory_for(need_scene(scene_id)).iterdir():
                if path.is_file() and path.name in CANONICAL_INPUT_FILES:
                    archive.write(path, path.name)
        return Response(stream.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{scene_id}-input.zip"'})

    @app.delete("/api/v1/scenes/{scene_id}")
    def scene_delete(scene_id: str):
        record = need_scene(scene_id)
        if scene_id in store.active_scene_ids():
            raise HTTPException(409, "Wait for active calculation before deleting")
        record["expires_at"] = "1970-01-01T00:00:00+00:00"
        store.put_scene(record)
        cleanup()
        return {"deleted": True}

    def make_version(parent: dict, body: dict, force_terrain: bool = False, flat_terrain: bool = False) -> dict:
        identifier, directory = allocate(directory_for(parent))
        terrain_error = None
        try:
            if set(body) - {"name", "layers", "mission", "fleet", "payload_catalog", "auto_dem"}:
                raise ValueError("Unknown scene version fields")
            layers = body.get("layers", {})
            if not isinstance(layers, dict) or set(layers) - set(LAYERS):
                raise ValueError("Unknown layer name")
            for name, value in layers.items():
                write_json(directory / f"{name}.geojson", value)
                (directory / f"{name}.kml").unlink(missing_ok=True)
            if layers:
                (directory / "scene.kml").unlink(missing_ok=True)
            for name in ("mission", "fleet", "payload_catalog"):
                if name in body:
                    write_json(directory / f"{name}.json", body[name])
            metadata = read_json(directory / "metadata.json")
            if "fleet" in body and body["fleet"] != read_json(directory_for(parent) / "fleet.json"):
                metadata["user_overrides"] = True
            write_json(directory / "metadata.json", metadata)
            check_documents(directory)
            if flat_terrain:
                create_flat_terrain(directory)
            elif force_terrain or (body.get("auto_dem", True) and not terrain_covers(directory)):
                try:
                    with terrain_lock:
                        acquire_terrain(directory, data / "cache/copernicus")
                except Exception as exc:
                    terrain_error = f"Automatic DSM unavailable: {exc}. Upload a GeoTIFF input package."
            record = register(identifier, str(body.get("name", parent["name"])), parent=parent)
            result = public_scene(record)
            if terrain_error:
                result["validation"]["errors"].append(terrain_error)
                result["validation"]["valid"] = False
                record["terrain_error"] = terrain_error
                store.put_scene(record)
            return result
        except Exception:
            shutil.rmtree(directory.parent)
            raise

    @app.post("/api/v1/scenes/{scene_id}/versions")
    def scene_version(scene_id: str, body: dict[str, Any]):
        return make_version(need_scene(scene_id), body)

    @app.post("/api/v1/scenes/{scene_id}/save-as")
    def save_scenario_as(scene_id: str, body: dict[str, Any]):
        name = body.get("name")
        if set(body) != {"name"} or not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
            raise HTTPException(422, "Provide a scenario name (1–160 characters)")
        name = name.strip()
        if any(r.get("saved") and r["name"].casefold() == name.casefold() for r in store.scenes()):
            raise HTTPException(409, "A saved scenario with this name already exists")
        result = make_version(need_scene(scene_id), {"name": name, "auto_dem": False})
        record = need_scene(result["id"])
        record.update(library_id=record["id"], template_readonly=False)
        store.put_scene(record)
        return public_scene(store.save_scenario(record["id"]))

    def preview_for(scene_id):
        directory = directory_for(need_scene(scene_id))
        path = directory / "dem.tif"
        if not path.is_file():
            raise HTTPException(404, "Scene has no elevation file")
        try:
            return terrain_preview(path)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/scenes/{scene_id}/terrain-preview")
    def scene_terrain_preview(scene_id: str):
        info, _ = preview_for(scene_id)
        return {**info, "image_url": f"/api/v1/scenes/{scene_id}/terrain-image"}

    @app.get("/api/v1/scenes/{scene_id}/terrain-image")
    def scene_terrain_image(scene_id: str):
        _, image = preview_for(scene_id)
        return Response(image, media_type="image/png")

    @app.post("/api/v1/scenes/{scene_id}/terrain")
    def scene_terrain(scene_id: str):
        return make_version(need_scene(scene_id), {}, force_terrain=True)

    @app.post("/api/v1/scenes/{scene_id}/terrain/flat")
    def scene_flat_terrain(scene_id: str):
        return make_version(need_scene(scene_id), {"auto_dem": False}, flat_terrain=True)

    def need_plan(identifier: str) -> dict:
        record = store.get_plan(identifier)
        if record is None:
            raise HTTPException(404, "Plan not found")
        return record

    def cancelled(plan_id: str) -> bool:
        return store.plan_status(plan_id.split("_")[0]) == "CANCELLED"

    def save_progress(record):
        # Serialize cancellation with worker updates so stale writes cannot revive a job.
        with admission:
            if not cancelled(record["id"]):
                update_run_log(record)
                logs.prune()
                store.put_plan(record)

    def candidate(directory: Path, req: dict, plan_id: str, on_progress=None) -> dict:
        if cancelled(plan_id):
            raise RuntimeError("Calculation cancelled")
        if app.state.runner is not None:
            return app.state.runner(directory, req)
        job_dir = data / "jobs" / plan_id
        job_dir.mkdir(parents=True, exist_ok=True)
        request_file, result_file = job_dir / "request.json", job_dir / "candidate.json"
        progress_file = job_dir / "progress.jsonl"
        progress_file.unlink(missing_ok=True)
        write_json(request_file, {"input_dir": str(directory.resolve()), "dataset": str(dataset),
                                  "objective": req["objective"], "budget": req["time_budget_s"],
                                  "seed": req["seed"], "search_depth": req.get("search_depth", "deep"), "progress_file": str(progress_file),
                                  "action": "h1" if req["mode"] == "h1" else "live"})
        timeout = min(3720, max(180, req["time_budget_s"] * 2 + 120))
        with BoundedProcess([sys.executable, "-m", "gmp.api.planner_adapter", str(request_file), str(result_file)],
                            env=worker_environment()) as process:
            seen = 0
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if cancelled(plan_id):
                    raise RuntimeError("Calculation cancelled")
                if on_progress and progress_file.exists():
                    lines = progress_file.read_text(encoding="utf-8").splitlines()
                    for line in lines[seen:]:
                        try:
                            on_progress(json.loads(line))
                        except json.JSONDecodeError:
                            pass
                    seen = len(lines)
                if time.monotonic() > deadline:
                    raise RuntimeError("Planner process timed out")
                time.sleep(0.15)
            stdout, stderr = process.communicate()
        if on_progress and progress_file.exists():
            lines = progress_file.read_text(encoding="utf-8").splitlines()
            for line in lines[seen:]:
                try:
                    on_progress(json.loads(line))
                except json.JSONDecodeError:
                    pass
        if process.returncode:
            message = (stderr or stdout).strip().splitlines()
            raise RuntimeError(message[-1][:1000] if message else "Planner process failed")
        if not result_file.exists() or result_file.stat().st_size > 100 * 1024 * 1024:
            raise RuntimeError("Planner output missing or exceeds 100 MiB")
        return json.loads(result_file.read_text())

    def gate(directory: Path, result: dict, plan_id: str) -> dict:
        job_dir = data / "jobs" / plan_id
        job_dir.mkdir(parents=True, exist_ok=True)
        candidate_path, request_path, report_path = job_dir / "check_candidate.json", job_dir / "check_request.json", job_dir / "validation.json"
        write_json(candidate_path, result)
        write_json(request_path, {"action": "validate", "input_dir": str(directory.resolve()), "candidate_path": str(candidate_path), "plan_id": plan_id})
        if cancelled(plan_id):
            raise RuntimeError("Calculation cancelled")
        with BoundedProcess([sys.executable, "-m", "gmp.api.planner_adapter", str(request_path), str(report_path)],
                            env=worker_environment()) as process:
            deadline = time.monotonic() + 180
            while True:
                if cancelled(plan_id) or time.monotonic() > deadline:
                    raise RuntimeError("Validation cancelled or timed out")
                try:
                    process.communicate(timeout=.15)
                    break
                except subprocess.TimeoutExpired:
                    pass
        if process.returncode or not report_path.exists():
            raise RuntimeError("Independent safety validation process failed")
        return json.loads(report_path.read_text())

    def comparison(scene: dict, result: dict) -> dict:
        sid = scene.get("scenario_id")
        if not sid:
            return {"available": False, "reason": "No reference scenario"}
        source = scenario_directory(dataset, sid)
        path = source / "expected" / result["objective"] / "result.json"
        if not path.exists() or input_hash(source / "input") != scene["input_sha256"]:
            return {"available": False, "reason": "Input differs from the reference or objective has no reference"}
        reference = json.loads(path.read_text())
        differences = {}
        for key in ("makespan_s", "total_flight_s", "distance_m", "coverage_percent"):
            actual, expected = result.get("metrics", {}).get(key), reference.get("metrics", {}).get(key)
            if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
                differences[key] = {"actual": actual, "reference": expected, "delta": actual - expected, "delta_percent": (actual - expected) / expected * 100 if expected else None}
        return {"available": True, "reference_status": reference["status"], "metrics": differences, "route_identity_required": False, "optimality_proven": False}

    def alternatives(scene: dict, req: dict, plan_id: str) -> list[dict]:
        from gmp.recommend.proposals import generate_proposals
        source = directory_for(scene)
        verified = []
        for index, proposal in enumerate(generate_proposals(source, limit=3)):
            identifier, directory = allocate(source)
            try:
                if set(proposal["documents"]) - {"fleet.json", "mission.json", "landing_sites.geojson", "metadata.json"}:
                    raise ValueError("Recommendation attempted to modify protected geometry or sensors")
                for name, value in proposal["documents"].items():
                    write_json(directory / name, value)
                if not validate_input(directory, dataset)["valid"]:
                    raise ValueError("Proposed input is not complete and valid")
                result = candidate(directory, {**req, "time_budget_s": min(req["time_budget_s"], 15)}, f"{plan_id}_alternative_{index}")
                report = gate(directory, result, plan_id=f"{plan_id}_alternative_{index}")
                if report.get("passed") and report.get("status") == "SAFE":
                    alternative = register(identifier, f"{scene['name']} / {proposal['title']}", parent=scene, extra={"recommendation_only": True})
                    verified.append({"id": proposal["id"], "title": proposal["title"], "type": proposal["type"], "changes": proposal["changes"], "assumptions": proposal["assumptions"], "status": "SAFE", "verified": True, "verification": {"passed": True, "status": "SAFE"}, "scene_id": alternative["id"], "input_sha256": alternative["input_sha256"], "metrics": report.get("metrics", {}), "certificate": report.get("certificate"), "result": result, "validation": report})
                else:
                    shutil.rmtree(directory.parent)
            except Exception:
                shutil.rmtree(directory.parent, ignore_errors=True)
        return verified

    def run_job(plan_id: str):
        record = need_plan(plan_id)
        if cancelled(plan_id):
            return
        scene = need_scene(record["scene_id"])
        directory = directory_for(scene)
        message = "H1: построение галсов и задач" if record["mode"] == "h1" else "Canonical H1/H2 calculation and independent safety validation"
        record.update(status="running", progress=[{"stage": "running", "message": message}])
        save_progress(record)
        try:
            if input_hash(directory) != scene["input_sha256"]:
                raise ValueError("Input snapshot changed after registration")
            if record["mode"] == "fixture":
                source = scenario_directory(dataset, scene["scenario_id"])
                if input_hash(source / "input") != scene["input_sha256"]:
                    raise ValueError("Fixture mode requires exact, unchanged reference input")
                path = source / "expected" / record["objective"] / "result.json"
                if not path.exists():
                    raise ValueError("No fixture for this objective")
                result = json.loads(path.read_text())
                result.setdefault("provenance", {})["mode"] = "fixture"
                legacy_path = legacy_bundle_for(source)
                if legacy_path:
                    from .planner_adapter import _h1_geojson
                    legacy = read_json(legacy_path)
                    result["h1_output"] = {"schema": "geoscan.h1.viewer.v1", "scene_id": legacy.get("scene_id"),
                                           "crs": legacy.get("crs"), "task_count": len(legacy.get("tasks", [])),
                                           "geojson": _h1_geojson(legacy)}
                result["h2_output"] = {"schema": "geoscan.h2.viewer.v1", "status": result.get("status"),
                                           "sorties": result.get("sorties", []), "metrics": result.get("metrics", {})}
                record["progress"].extend([
                    {"stage": "h1", "status": "completed", "message": "H1: открыт сохранённый эталонный результат", "duration_s": None},
                    {"stage": "h2", "status": "completed", "message": "H2: открыт сохранённый эталонный результат", "duration_s": None},
                ])
                save_progress(record)
            else:
                def on_progress(event):
                    record.setdefault("progress", []).append(event)
                    save_progress(record)
                result = candidate(directory, record, plan_id, on_progress=on_progress)
            if cancelled(plan_id):
                return
            if record["mode"] == "h1":
                record.update(status="COMPLETED", plan=result, validation=None, certificate=None, metrics=None)
                record["progress"].append({"stage": "complete", "message": "Галсы и задачи готовы. Выполнен только H1."})
                save_progress(record)
                return
            h3_started = time.time()
            record["progress"].append({"stage": "h3", "status": "running", "message": "H3: независимая проверка маршрута и ограничений", "started_at": h3_started})
            save_progress(record)
            report = gate(directory, result, plan_id=plan_id)
            h3_finished = time.time()
            status = report["status"] if report.get("passed") else "UNSAFE"
            record["progress"].append({"stage": "h3", "status": "completed", "message": f"H3 завершён: {status}", "started_at": h3_started, "finished_at": h3_finished, "duration_s": max(0.0, h3_finished - h3_started)})
            record.update(status=status, plan=result, validation=report, certificate=report.get("certificate") if status == "SAFE" else None, metrics=report.get("metrics", {}), comparison=comparison(scene, result), recommendations=[])
            record["progress"].append({"stage": "validation", "message": status})
            if status != "SAFE" and record["mode"] == "live":
                record["status"] = "running"
                record["result_status"] = status
                record["progress"].append({"stage": "recommendations", "message": "Recalculating available candidate sites"})
                save_progress(record)
                record["recommendations"] = alternatives(scene, record, plan_id)
            record["status"] = status
            record["progress"].append({"stage": "complete", "message": status})
            save_progress(record)
        except Exception as exc:
            record.update(status="ERROR", certificate=None, error=str(exc)[:1500])
            record.setdefault("progress", []).append({"stage": "error", "message": str(exc)[:1500]})
            save_progress(record)

    @app.post("/api/v1/plans")
    def create_plan(req: PlanRequest):
        scene = need_scene(req.scene_id)
        directory = directory_for(scene)
        validation = validate_input(directory, dataset)
        if not validation["valid"]:
            raise HTTPException(409, {"message": "Scene is incomplete or invalid", "validation": validation})
        if req.mode != "h1" and req.objective not in read_json(directory / "mission.json")["objectives"]:
            raise HTTPException(422, "Objective is not enabled in this immutable scene; create a version first")
        if req.mode == "fixture":
            sid = scene.get("scenario_id")
            if not sid or input_hash(scenario_directory(dataset, sid) / "input") != scene["input_sha256"]:
                raise HTTPException(409, "Fixture mode is only available for unchanged reference inputs")
            if not (scenario_directory(dataset, sid) / "expected" / req.objective / "result.json").exists():
                raise HTTPException(409, "Reference objective is unavailable")
        with admission:
            if len(store.active_scene_ids()) >= resources["queue_limit"]:
                raise HTTPException(429, "Calculation queue is full", headers={"Retry-After": "30"})
            plan_id = uuid.uuid4().hex
            record = {"id": plan_id, **req.model_dump(), "status": "queued", "progress": [{"stage": "queued", "message": "Waiting for an available worker"}], "created_at": utcnow(), "metrics": None, "validation": None, "certificate": None, "plan": None, "recommendations": [], "input_sha256": scene["input_sha256"]}
            record.update(run_code=f"{'H1' if req.mode == 'h1' else 'RUN'}-{time.strftime('%Y%m%d')}-{plan_id[:12]}",
                          library_id=scene.get("library_id", scene["id"]), scenario_name=scene["name"])
            log_root = data / "logs/runs"
            log_dir = (log_root / record["run_code"]).resolve()
            log_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
            record["log_dir"] = str(log_dir)
            logs.write_json(log_dir / "scenario.json", {"id": scene["id"], "name": scene["name"],
                        "scenario_id": scene.get("scenario_id"), "input_sha256": scene["input_sha256"],
                        "input_directory": str(directory), **snapshot(directory)})
            store.put_plan(record)
            update_run_log(record)
            logs.emit("plan_submitted", plan_id=plan_id)
            logs.prune()
            executor.submit(run_job, plan_id)
        return {"id": plan_id, "status": "queued", "mode": req.mode, "run_code": record["run_code"], "log_dir": record["log_dir"]}

    @app.post("/api/v1/plans/{plan_id}/cancel")
    def cancel_plan(plan_id: str):
        with admission:
            record = need_plan(plan_id)
            if record["status"] in ("queued", "running"):
                record.update(status="CANCELLED", certificate=None)
                record.setdefault("progress", []).append({"stage": "cancelled", "message": "Расчёт остановлен пользователем"})
                update_run_log(record)
                store.put_plan(record)
        return {"id": plan_id, "status": record["status"]}

    def summary(record: dict) -> dict:
        result = {k: v for k, v in record.items() if k not in ("plan", "certificate")}
        result["recommendations"] = [{k: v for k, v in r.items() if k not in ("result", "validation", "certificate")} for r in record.get("recommendations", [])]
        return result

    @app.get("/api/v1/plans")
    def list_plans(scene_id: str | None = None, limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0, le=100000)):
        return {"plans": store.plan_headers(scene_id, limit=limit, offset=offset), "limit": limit, "offset": offset}

    @app.get("/api/v1/plans/{plan_id}")
    def get_plan(plan_id: str):
        record = need_plan(plan_id)
        from .planner_adapter import refresh_h1_view
        refresh_h1_view(record)
        record["recommendations"] = [{k: v for k, v in r.items() if k not in ("result", "validation")} for r in record.get("recommendations", [])]
        return record

    @app.get("/api/v1/plans/{plan_id}/log/{filename}")
    def download_run_log(plan_id: str, filename: str):
        record = need_plan(plan_id)
        if filename not in ("run.json", "scenario.json", "h1_h2.bundle.json") or not record.get("log_dir"):
            raise HTTPException(404, "Log file not found")
        path = Path(record["log_dir"]) / filename
        if not path.is_file():
            raise HTTPException(404, "Log file not ready")
        return FileResponse(path, media_type="application/json", filename=f"{record['run_code']}-{filename}")

    @app.get("/api/v1/plans/{plan_id}/status")
    def status(plan_id: str):
        return summary(need_plan(plan_id))

    @app.get("/api/v1/plans/{plan_id}/metrics")
    def metrics(plan_id: str):
        return need_plan(plan_id).get("metrics") or {}

    @app.get("/api/v1/plans/{plan_id}/validation")
    def validation(plan_id: str):
        return need_plan(plan_id).get("validation") or {}

    @app.get("/api/v1/plans/{plan_id}/recommendations")
    def recommendations(plan_id: str):
        return {"recommendations": get_plan(plan_id)["recommendations"]}

    @app.post("/api/v1/plans/{plan_id}/recommendations/{recommendation_id}/apply")
    def apply_recommendation(plan_id: str, recommendation_id: str):
        record = need_plan(plan_id)
        selected = next((r for r in record.get("recommendations", []) if r["id"] == recommendation_id), None)
        if selected is None or not selected.get("verified"):
            raise HTTPException(404, "Verified recommendation not found")
        verified_scene = need_scene(selected["scene_id"])
        if input_hash(directory_for(verified_scene)) != selected["input_sha256"]:
            raise HTTPException(409, "Alternative input has changed")
        report = gate(directory_for(verified_scene), selected["result"], plan_id=f"{plan_id}_apply_check")
        if not report.get("passed") or report["status"] != "SAFE":
            raise HTTPException(409, "Alternative is no longer verified")
        identifier, directory = allocate(directory_for(verified_scene))
        new_scene = register(identifier, verified_scene["name"], parent=need_scene(record["scene_id"]), extra={"applied_recommendation_id": recommendation_id})
        new_plan_id = uuid.uuid4().hex
        report = gate(directory, selected["result"], plan_id=new_plan_id)
        store.put_plan({"id": new_plan_id, "scene_id": identifier, "objective": record["objective"], "mode": "live", "status": "SAFE", "created_at": utcnow(), "progress": [{"stage": "complete", "message": "User-selected, recalculated and independently checked alternative"}], "plan": selected["result"], "validation": report, "certificate": report["certificate"], "metrics": report["metrics"], "recommendations": [], "input_sha256": new_scene["input_sha256"]})
        return {**public_scene(new_scene), "applied_plan_id": new_plan_id}

    @app.get("/api/v1/plans/{plan_id}/export")
    def export(plan_id: str, format: str = "geojson", uav_id: str | None = Query(
            default=None, description="KML/GeoJSON only: export all sorties of this UAV; omitted means all UAVs")):
        """Download checked flight assignments; full waypoint schema in docs/flight-export.md."""
        record = need_plan(plan_id)
        result = record.get("plan")
        if result is None:
            raise HTTPException(409, "No completed result")
        if uav_id is not None and format not in ("geojson", "kml"):
            raise HTTPException(422, "UAV selection applies only to KML and GeoJSON")
        if format not in ("geojson", "kml", "mission", "certificate", "pdf", "docx"):
            raise HTTPException(422, "Unknown export format")
        if format in ("geojson", "kml", "certificate") and (record["status"] != "SAFE" or not record.get("certificate")):
            raise HTTPException(409, "Flight export requires an independently verified SAFE result")
        scene = need_scene(record["scene_id"])
        if input_hash(directory_for(scene)) != record["input_sha256"]:
            raise HTTPException(409, "Input changed after verification")
        digest = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        if digest != (record.get("validation") or {}).get("result_sha256"):
            raise HTTPException(409, "Result changed after verification")
        if record.get("certificate") and digest != record["certificate"].get("result_sha256"):
            raise HTTPException(409, "Certificate does not match result")
        if format in ("pdf", "docx"):
            from gmp.report.mission import generate_report_bytes
            metadata = read_json(directory_for(scene) / "metadata.json")
            metadata.update(name=scene["name"], mode=record["mode"], input_sha256=record["input_sha256"], comparison=record.get("comparison"), expires_at=scene["expires_at"])
            content = generate_report_bytes(format, result, record["validation"], metadata)
            media = "application/pdf" if format == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif format == "mission":
            content = json.dumps({"schema": "geoscan.h3.export.v1", "result": result, "status": record["status"], "mode": record["mode"], "validation": record["validation"], "certificate": record.get("certificate")}, ensure_ascii=False, allow_nan=False).encode()
            media = "application/json"
        elif format == "certificate":
            content, media = json.dumps(record["certificate"], ensure_ascii=False).encode(), "application/json"
        else:
            from .flight_export import geojson, kml, select_sorties
            try:
                selected = select_sorties(result, uav_id)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            inputs = directory_for(scene)
            uids = {s["uav_id"] for s in selected}
            site_ids = {s[key] for s in selected for key in ("start_site", "landing_site")}
            context = {
                "plan_id": plan_id, "scene_id": scene["id"], "objective": result["objective"],
                "mode": record["mode"], "status": "SAFE", "metrics_scope": "complete mission",
                "metrics": record["metrics"], "mission": read_json(inputs / "mission.json"),
                "uavs": [u for u in read_json(inputs / "fleet.json")["uavs"] if u["id"] in uids],
                "sites": [f for f in read_json(inputs / "landing_sites.geojson")["features"] if f["properties"]["id"] in site_ids],
                "payload_catalog": read_json(inputs / "payload_catalog.json"),
                "terrain": read_json(inputs / "metadata.json").get("terrain", {}),
                "coordinate_reference": "WGS84 longitude/latitude; AMSL uses the source DEM vertical datum",
                "validator_source_sha256": record["certificate"].get("validator_source_sha256"),
                "input_sha256": record["input_sha256"], "source_result_sha256": digest,
                "certificate_scope": "complete source result; filtered exports are views of that result",
                "model_scope": record["validation"].get("model_scope"),
                "not_checked": record["validation"].get("not_checked", []),
            }
            if format == "geojson":
                content = json.dumps(geojson(result, context, uav_id), ensure_ascii=False, allow_nan=False).encode()
                media = "application/geo+json"
            else:
                content, media = kml(result, context, uav_id), "application/vnd.google-earth.kml+xml"
        extension = "json" if format in ("mission", "certificate") else format
        aircraft_suffix = ""
        if uav_id is not None:
            safe_uid = "".join(c if c.isascii() and (c.isalnum() or c in "_-") else "_" for c in uav_id)[:60]
            aircraft_suffix = "-" + safe_uid + "-" + hashlib.sha256(uav_id.encode()).hexdigest()[:8]
        return Response(content=content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{plan_id}{aircraft_suffix}-{format}.{extension}"'})

    return app


app = create_app()
