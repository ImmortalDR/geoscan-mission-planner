"""Small read-only browser for H1→H2 fixture bundles."""
from __future__ import annotations

import argparse
from collections import OrderedDict
import json
import mimetypes
import os
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
STATIC = Path(__file__).resolve().parent / "static"
ARTIFACTS = Path(os.environ.get("H2_ARTIFACTS_DIR", ROOT / "artifacts" / "viewer-runs"))
ALGORITHMS = {
    "greedy_baseline": dict(cpsat=False, max_iterations=0, time_budget_s=10),
    "cpsat_warmstart": dict(cpsat=True, max_iterations=0, time_budget_s=12),
    "lns_anytime": dict(cpsat=True, max_iterations=80, time_budget_s=20),
}
RUNS: OrderedDict[str, dict] = OrderedDict()
RESULTS: dict[tuple[str, str, str], dict] = {}
RUNS_LOCK = threading.Lock()
PLANNER_LOCK = threading.Lock()
MAX_RUNS = 8


def fixture_files() -> dict[str, Path]:
    return {path.name: path for path in sorted(FIXTURES.glob("*.bundle.json"))}


def scenario_index() -> list[dict]:
    rows = []
    for name, path in fixture_files().items():
        bundle = json.loads(path.read_text(encoding="utf-8"))
        rows.append({
            "file": name,
            "scene_id": bundle["scene_id"],
            "kind": "h1_output",
            "tasks": len(bundle["tasks"]),
            "uavs": len(bundle["fleet"]),
            "multi_uav": len(bundle["fleet"]) >= 2,
        })
    rows.sort(key=lambda row: (-row["uavs"], -row["tasks"], row["file"]))
    return rows



def result_row(algorithm: str, plan: dict, saved: bool) -> dict:
    deco = plan.get("deconfliction") or {}
    before = deco.get("before") or []
    remaining = deco.get("remaining") or []
    return {
        "algorithm": algorithm,
        "objective": plan["objective"],
        "status": plan["status"],
        "metrics": plan["metrics"],
        "saved": saved,
        "conflicts_before": len(before),
        "conflicts_remaining": len(remaining),
        "ladder_used": deco.get("ladder_used") or [],
    }


def scenario_results(name: str) -> list[dict]:
    target = fixture_files().get(name)
    if target is None:
        raise ValueError("scenario not found")
    bundle = json.loads(target.read_text(encoding="utf-8"))
    scene = re.sub(r"[^A-Za-z0-9_.-]+", "_", bundle["scene_id"])
    rows = {}
    for path in sorted((ARTIFACTS / scene).glob("*/plan.json")):
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
            if plan["scene_id"] != bundle["scene_id"]:
                continue
            row = result_row(path.parent.name, plan, True)
            rows[(row["algorithm"], row["objective"])] = row
        except (OSError, ValueError, KeyError):
            continue
    with RUNS_LOCK:
        for (scenario, algorithm, objective), row in RESULTS.items():
            if scenario == name:
                rows[(algorithm, objective)] = dict(row)
    return [rows[key] for key in sorted(rows)]


def run_scenario(name: str, algorithm: str) -> tuple[str, dict]:
    """Run one bounded, named planner configuration without writing artifacts."""
    target = fixture_files().get(name)
    if target is None:
        raise ValueError("scenario not found")
    if algorithm not in ALGORITHMS:
        raise ValueError("algorithm not found")
    if not PLANNER_LOCK.acquire(blocking=False):
        raise RuntimeError("planner is busy")
    try:
        from h2.contract import load_bundle
        from h2.planner import plan_bundle
        from h2.report import render_html
        from h2.scheduler import Settings

        bundle = load_bundle(target)
        objective = bundle["mission"]["objectives"][0]
        cfg = ALGORITHMS[algorithm]
        settings = Settings(
            objective=objective,
            time_budget_s=cfg["time_budget_s"],
            max_iterations=cfg["max_iterations"],
            seed=42,
            cpsat=cfg["cpsat"],
        )
        plan = plan_bundle(bundle, settings)
        run_id = uuid.uuid4().hex
        item = {
            "id": run_id,
            "algorithm": algorithm,
            "scenario_file": name,
            "scene_id": bundle["scene_id"],
            "plan": plan,
            "report": render_html(plan),
        }
        with RUNS_LOCK:
            RUNS[run_id] = item
            RESULTS[(name, algorithm, objective)] = {**result_row(algorithm, plan, False), "run_id": run_id}
            while len(RUNS) > MAX_RUNS:
                RUNS.popitem(last=False)
        return run_id, item
    finally:
        PLANNER_LOCK.release()


def save_run(run_id: str) -> Path:
    with RUNS_LOCK:
        item = RUNS.get(run_id)
    if item is None:
        raise ValueError("run not found")
    from h2.cli import save_plan

    safe_scene = re.sub(r"[^A-Za-z0-9_.-]+", "_", item["scene_id"])
    target = ARTIFACTS / safe_scene / item["algorithm"]
    save_plan(item["plan"], target)
    with RUNS_LOCK:
        key = (item["scenario_file"], item["algorithm"], item["plan"]["objective"])
        if RESULTS.get(key, {}).get("run_id") == run_id:
            RESULTS[key]["saved"] = True
    return target


class Handler(BaseHTTPRequestHandler):
    server_version = "H2FixtureViewer/1"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def send_bytes(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value, status=200):
        self.send_bytes(status, json.dumps(value, ensure_ascii=False).encode(),
                        "application/json; charset=utf-8")

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 4096:
            raise ValueError("invalid request size")
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in {"/health", "/health/ready"}:
            self.send_json({"status": "ok", "service": "h2-fixture-viewer"})
            return
        if path == "/api/algorithms":
            self.send_json(sorted(ALGORITHMS))
            return
        if path == "/api/scenarios":
            self.send_json(scenario_index())
            return
        if path.startswith("/api/results/"):
            try:
                self.send_json(scenario_results(path.removeprefix("/api/results/")))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 404)
            return
        if path.startswith("/api/scenarios/"):
            name = path.removeprefix("/api/scenarios/")
            target = fixture_files().get(name)
            if target is None:
                self.send_json({"error": "scenario not found"}, 404)
                return
            self.send_bytes(200, target.read_bytes(), "application/json; charset=utf-8")
            return
        match = re.fullmatch(r"/api/runs/([0-9a-f]{32})/report", path)
        if match:
            with RUNS_LOCK:
                item = RUNS.get(match.group(1))
            if item is None:
                self.send_json({"error": "run not found"}, 404)
            else:
                self.send_bytes(200, item["report"].encode(), "text/html; charset=utf-8")
            return
        if path == "/":
            path = "/index.html"
        target = (STATIC / path.lstrip("/")).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            self.send_json({"error": "not found"}, 404)
            return
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if mime.startswith("text/") or mime in {"application/javascript", "application/json"}:
            mime += "; charset=utf-8"
        self.send_bytes(200, target.read_bytes(), mime)

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/runs":
                request = self.read_json()
                run_id, item = run_scenario(request.get("scenario", ""),
                                            request.get("algorithm", ""))
                plan = item["plan"]
                self.send_json({"run_id": run_id, "status": plan["status"],
                                "metrics": plan["metrics"],
                                "report_url": f"/api/runs/{run_id}/report"})
                return
            match = re.fullmatch(r"/api/runs/([0-9a-f]{32})/save", path)
            if match:
                target = save_run(match.group(1))
                self.send_json({"saved": True, "artifact_dir": str(Path("artifacts") / "viewer-runs" / target.parent.name / target.name)})
                return
            self.send_json({"error": "not found"}, 404)
        except RuntimeError as exc:
            self.send_json({"error": str(exc)}, 409)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": f"planner failed: {exc}"}, 500)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"H2 fixture viewer: http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
