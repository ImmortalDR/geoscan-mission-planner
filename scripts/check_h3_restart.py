#!/usr/bin/env python3
"""Verify completed projects and exports across a restart of the H3 unit only."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

import httpx
from verify_h3_live import read_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--service", choices=("geoscan-h3.service", "geoscan-mvp.service"), default="geoscan-h3.service")
    args = parser.parse_args()
    code = read_code(args.env_file)
    with httpx.Client(base_url=args.url, timeout=60) as client:
        def login():
            response = client.post("/api/v1/auth/login", json={"code": code})
            response.raise_for_status()
        def read():
            response = client.get(f"/api/v1/plans/{args.plan_id}/export?format=mission")
            response.raise_for_status()
            return hashlib.sha256(response.content).hexdigest()
        login()
        plans = client.get("/api/v1/plans").json()["plans"]
        assert not any(p["status"] in {"queued", "running"} for p in plans), "Wait for active work before restart"
        before = read()
        subprocess.run(["systemctl", "restart", args.service], check=True)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if client.get("/health/ready").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        login()
        after = read()
        assert before == after, "Completed export changed across restart"
        payload = {"passed": True, "plan_id": args.plan_id, "export_sha256": before,
                   "service": args.service, "unchanged_after_restart": True}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n")
        print(json.dumps(payload))


if __name__ == "__main__":
    main()
