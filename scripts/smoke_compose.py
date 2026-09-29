#!/usr/bin/env python3
"""Verify the isolated localhost TLS Compose stack with generated credentials."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import ssl
import time
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPSHandler
from urllib.error import HTTPError
from http.cookiejar import CookieJar


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("secrets", type=Path)
    parser.add_argument("--url", default="https://localhost")
    parser.add_argument("--existing-plan", help="Previously tested SAFE plan to verify after a restart")
    parser.add_argument("--output", type=Path, default=Path("docs/evidence/compose.json"))
    args = parser.parse_args()
    passwords = dict(line.split(": ", 1) for line in (args.secrets / "credentials.txt").read_text().splitlines())
    context = ssl.create_default_context(cafile=str(args.secrets / "web.crt"))
    report = {"roles": [], "tls_verified": True, "url": args.url,
              "checked_at": datetime.now(timezone.utc).isoformat()}
    for role in ("viewer", "planner", "admin"):
        client = build_opener(HTTPCookieProcessor(CookieJar()), HTTPSHandler(context=context))
        def request(path, data=None, csrf=None):
            headers = {"Content-Type": "application/json"}
            if csrf:
                headers["X-CSRF-Token"] = csrf
            payload = json.dumps(data).encode() if data is not None else None
            with client.open(Request(args.url.rstrip("/") + path, data=payload, headers=headers), timeout=120) as response:
                return json.load(response)
        assert request("/health/ready")["storage"] == "postgresql"
        login = request("/api/v1/auth/login", {"username": role, "password": passwords[role]})
        assert login["role"] == role
        assert "h2.routing.solver" in {item["id"] for item in request("/api/v1/documentation")["catalog"]["algorithms"]}
        if role == "viewer":
            try:
                request("/api/v1/scenes?scenario_id=S00_smoke_rgb", {}, login["csrf_token"])
                raise AssertionError("Viewer mutation allowed")
            except HTTPError as error:
                assert error.code == 403
        if role == "planner":
            if args.existing_plan:
                existing = request(f"/api/v1/plans/{args.existing_plan}")
                assert existing["status"] == "SAFE"
                certificate = request(f"/api/v1/plans/{args.existing_plan}/export?format=certificate")
                assert certificate == existing["certificate"]
                report["persisted_plan_and_certificate"] = args.existing_plan
            scene = request("/api/v1/scenes?scenario_id=S00_smoke_rgb", {}, login["csrf_token"])
            plan = request("/api/v1/plans", {"scene_id": scene["id"], "mode": "live", "time_budget_s": 1}, login["csrf_token"])
            report["plan_id"] = plan["id"]
            for _ in range(180):
                result = request(f"/api/v1/plans/{plan['id']}/status")
                if result["status"] not in {"queued", "running"}:
                    break
                time.sleep(1)
            assert result["status"] == "SAFE", result["status"]
            report["live_status"] = result["status"]
        report["roles"].append(role)
    report["passed"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
