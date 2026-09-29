#!/usr/bin/env python3
"""Verify the isolated localhost Compose stack without opening another public port."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import time
from urllib.parse import urlparse

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Certificate hostname/IP, test stack on localhost:4443")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--docker", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    endpoint = urlparse(args.url)
    assert endpoint.scheme == "https" and endpoint.port == 4443
    original = socket.getaddrinfo
    def resolve(host, port, *rest, **kwargs):
        return original("127.0.0.1" if host == endpoint.hostname and port == 4443 else host, port, *rest, **kwargs)
    socket.getaddrinfo = resolve
    values = dict(line.split("=", 1) for line in args.env_file.read_text().splitlines() if line and not line.startswith("#"))
    with httpx.Client(base_url=args.url, trust_env=False, timeout=90) as client:
        def get(path):
            response = client.get(path)
            response.raise_for_status()
            return response
        def post(path, **kwargs):
            response = client.post(path, **kwargs)
            response.raise_for_status()
            return response.json()
        assert get("/health/ready").json()["status"] == "ready"
        assert client.get("/api/v1/scenes").status_code == 401
        auth = post("/api/v1/auth/login", json={"code": values["GMP_ACCESS_CODE"]})
        client.headers["X-CSRF-Token"] = auth["csrf_token"]
        assert len(get("/api/v1/scenarios").json()["scenarios"]) == 12
        scene = post("/api/v1/scenes?scenario_id=S00_smoke_rgb")
        queued = post("/api/v1/plans", json={"scene_id": scene["id"], "time_budget_s": 1})
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            plan = get(f"/api/v1/plans/{queued['id']}").json()
            if plan["status"] == "ERROR" or any(p["stage"] == "complete" for p in plan["progress"]):
                break
            time.sleep(1)
        assert plan["status"] == "SAFE" and plan["mode"] == "live", plan.get("progress")
        assert plan["certificate"]["input_sha256"] == scene["input_sha256"]
        path = f"/api/v1/plans/{plan['id']}/export?format=mission"
        before = hashlib.sha256(get(path).content).hexdigest()
        assert get(f"/api/v1/plans/{plan['id']}/export?format=pdf").content.startswith(b"%PDF")
        subprocess.run([str(args.docker), "-H", "unix:///run/geoscan-h3-docker-test.sock",
                        "restart", "geoscan-h3-verify-app-1"], check=True, capture_output=True)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                if client.get("/health/ready").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        after = hashlib.sha256(get(path).content).hexdigest()
        assert before == after
    report = {"passed": True, "trusted_https": True, "localhost_only_port": 4443,
              "catalog_count": 12, "live_status": "SAFE", "certificate": True,
              "pdf": True, "persistent_volume_after_restart": True,
              "scene_id": scene["id"], "plan_id": plan["id"], "export_sha256": before}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
