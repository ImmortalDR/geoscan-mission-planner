#!/usr/bin/env python3
"""Run real named-user HTTP and LIVE-planning load against an isolated PostgreSQL service."""
from __future__ import annotations
import argparse
import asyncio
import csv
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import httpx
import psycopg
from gmp.api.auth import password_hash
from gmp.api.runtime import hardware


def rss_tree(pid):
    try:
        status = (Path("/proc") / str(pid) / "status").read_text()
        value = next((int(line.split()[1]) for line in status.splitlines() if line.startswith("VmRSS:")), 0)
        children = (Path("/proc") / str(pid) / "task" / str(pid) / "children").read_text().split()
        return value + sum(rss_tree(int(child)) for child in children)
    except (OSError, ProcessLookupError):
        return 0


async def run_profile(url, users, password, rows):
    accepted, terminal = [], {}
    async def user(index):
        async with httpx.AsyncClient(base_url=url, timeout=60) as client:
            async def request(method, path, **kwargs):
                start = time.perf_counter()
                response = await client.request(method, path, **kwargs)
                rows.append({"users":users,"actor":index,"method":method,"endpoint":path.split("?")[0],"status":response.status_code,"duration_ms":round((time.perf_counter()-start)*1000,3)})
                if response.status_code >= 500:
                    raise RuntimeError(f"Server error {response.status_code}")
                return response
            login = await request("POST", "/api/v1/auth/login", json={"username":f"load{index}","password":password})
            login.raise_for_status()
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            for endpoint in ("/api/v1/scenarios", "/api/v1/plans", "/api/v1/documentation"):
                (await request("GET", endpoint)).raise_for_status()
            scene = await request("POST", "/api/v1/scenes?scenario_id=S00_smoke_rgb")
            scene.raise_for_status()
            plan = await request("POST", "/api/v1/plans", json={"scene_id":scene.json()["id"],"mode":"live","time_budget_s":1})
            if plan.status_code == 429:
                return
            plan.raise_for_status()
            identifier = plan.json()["id"]
            accepted.append(identifier)
            deadline = time.monotonic()+180
            while time.monotonic() < deadline:
                status = (await request("GET", f"/api/v1/plans/{identifier}/status")).json()
                if status["status"] not in {"queued","running"}:
                    terminal[identifier] = status["status"]
                    return
                await asyncio.sleep(1)
            raise TimeoutError("Accepted calculation did not finish in 180 seconds")
    await asyncio.gather(*(user(index) for index in range(users)))
    selected = [r for r in rows if r["users"] == users]
    latencies = sorted(r["duration_ms"] for r in selected)
    return {"users":users,"requests":len(selected),"p95_ms":latencies[max(0, int(len(latencies)*.95)-1)],
            "server_errors":sum(r["status"]>=500 for r in selected),"rate_limited":sum(r["status"]==429 for r in selected),
            "accepted_plans":len(accepted),"terminal_statuses":{status:list(terminal.values()).count(status) for status in set(terminal.values())},
            "all_accepted_completed_safe":len(terminal)==len(accepted) and all(s=="SAFE" for s in terminal.values())}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--users", default="1,8,32")
    p.add_argument("--output", type=Path, default=ROOT / "docs/evidence/load")
    args = p.parse_args()
    dsn = os.environ.get("GMP_TEST_DATABASE_URL", "")
    with psycopg.connect(dsn) as db:
        if not db.info.dbname.endswith("_test"):
            p.error("Use an isolated database ending in _test")
        if db.execute("SELECT EXISTS(SELECT FROM pg_tables WHERE tablename='scenes')").fetchone()[0]:
            p.error("Use a fresh disposable database; existing workspace tables were found")
    profiles = [int(n) for n in args.users.split(",")]
    if not profiles or min(profiles)<1 or max(profiles)>64 or len(set(profiles)) != len(profiles):
        p.error("Unique user counts between 1 and 64 are required")
    args.output.mkdir(parents=True, exist_ok=True)
    rows, runs = [], []
    failure = None
    with tempfile.TemporaryDirectory(prefix="geoscan-load-") as temp:
        state = Path(temp)
        password = secrets.token_urlsafe(32)
        digest = password_hash(password)
        auth = state / "users.json"
        auth.write_text(json.dumps({"users":{f"load{i}":{"role":"planner","password_hash":digest} for i in range(max(profiles))}}))
        auth.chmod(0o600)
        with socket.socket() as bound:
            bound.bind(("127.0.0.1",0))
            port = bound.getsockname()[1]
        env = {k:v for k,v in os.environ.items() if k not in {"GMP_ACCESS_CODE","GMP_DATABASE_URL_FILE"}}
        env.update(GMP_DATABASE_URL=dsn,GMP_DATA_DIR=str(state/"workspace"),GMP_AUTH_FILE=str(auth),GMP_OFFLINE="1",GMP_DEPLOYMENT_MODE="internal",GMP_ALLOW_LOCAL_HTTP="1",PYTHONPATH=os.pathsep.join(str(ROOT/s) for s in ("src","h1/h1_coverage/src","h2/src")))
        with (state/"server.log").open("w") as log:
            proc = subprocess.Popen([sys.executable,"-m","uvicorn","gmp.api.app:app","--host","127.0.0.1","--port",str(port),"--no-access-log"],cwd=ROOT,env=env,stdout=log,stderr=log)
            stop = threading.Event()
            peak = [0]
            def sample():
                while not stop.wait(.1):
                    peak[0] = max(peak[0],rss_tree(proc.pid))
            sampler = threading.Thread(target=sample,daemon=True)
            sampler.start()
            try:
                url = f"http://127.0.0.1:{port}"
                for _ in range(120):
                    if proc.poll() is not None:
                        raise RuntimeError("Staging service exited before readiness")
                    try:
                        if httpx.get(url+"/health/ready",timeout=1).status_code==200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.25)
                else:
                    raise RuntimeError("Staging service did not become ready")
                for count in profiles:
                    try:
                        result = asyncio.run(run_profile(url,count,password,rows))
                    except Exception as exc:
                        failure = {"users": count, "error_type": type(exc).__name__}
                        break
                    runs.append(result)
                    print(json.dumps(result),flush=True)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                stop.set()
                sampler.join(timeout=2)
    summary = {"generated_at_unix":time.time(),"hardware":hardware(),"peak_api_worker_rss_mb":round(peak[0]/1024,2),"database":"PostgreSQL","transport":"HTTP loopback for test only; production requires TLS","target_16cpu_64gb_tested":False,"failure":failure,"runs":runs}
    (args.output/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    with (args.output/"requests.csv").open("w",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=["users","actor","method","endpoint","status","duration_ms"],lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return int(bool(failure) or any(r["server_errors"] or not r["all_accepted_completed_safe"] for r in runs))


if __name__ == "__main__":
    raise SystemExit(main())
