#!/usr/bin/env python3
"""Authenticated HTTPS acceptance; creates ordinary, expiring test projects."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import io
import json
import os
from pathlib import Path
import time
import xml.etree.ElementTree as ET
import zipfile

import httpx
from verify_h3_live import read_code


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--all-fixtures", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    secret = os.environ.get("GMP_ACCESS_CODE")
    if args.env_file:
        secret = read_code(args.env_file)
    secret = secret or getpass.getpass("Access code: ")
    report = {"url": args.url, "checks": [], "plans": [], "scenes": []}

    def check(condition, label):
        report["checks"].append({"name": label, "passed": bool(condition)})
        print(f"{'PASS' if condition else 'FAIL'} {label}", flush=True)
        assert condition, label

    with httpx.Client(base_url=args.url.rstrip("/"), timeout=180, follow_redirects=True) as client:
        def get(path):
            response = client.get(path)
            response.raise_for_status()
            return response.json()

        def post(path, **kwargs):
            response = client.post(path, **kwargs)
            response.raise_for_status()
            return response.json()

        def scene(scenario):
            row = post("/api/v1/scenes", params={"scenario_id": scenario})
            report["scenes"].append(row["id"])
            check(row["validation"]["valid"], f"valid scene {scenario}")
            return row

        def plan(row, objective="makespan", mode=None):
            body = {"scene_id": row["id"], "objective": objective, "time_budget_s": 5}
            if mode:
                body["mode"] = mode
            queued = post("/api/v1/plans", json=body)
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                record = get(f"/api/v1/plans/{queued['id']}")
                if record["status"] == "ERROR" or any(p["stage"] == "complete" for p in record["progress"]):
                    break
                time.sleep(1)
            else:
                raise AssertionError(f"Calculation timed out: {queued['id']}")
            report["plans"].append({"id": record["id"], "scene_id": row["id"],
                                    "status": record["status"], "mode": record["mode"],
                                    "objective": objective, "metrics": record.get("metrics"),
                                    "progress": record.get("progress")})
            check(record["status"] != "ERROR", f"completed {row.get('scenario_id')} {objective} {mode or 'default'}")
            check(record["validation"]["passed"], "independent validation passed")
            if record["status"] == "SAFE":
                check(record["certificate"]["result_sha256"] == digest(record["plan"]), "certificate binds actual plan")
                check(record["certificate"]["input_sha256"] == row["input_sha256"], "certificate binds input snapshot")
            else:
                check(record.get("certificate") is None, "refusal has no safety certificate")
            return record

        try:
            check(client.get("/api/v1/scenes").status_code == 401, "anonymous workspace denied")
            check(get("/health/ready")["status"] == "ready", "service ready")
            login = client.post("/api/v1/auth/login", json={"code": secret})
            login.raise_for_status()
            cookie = login.headers["set-cookie"].lower()
            check(all(x in cookie for x in ("secure", "httponly", "samesite=strict")), "secure session cookie")
            check(client.post("/api/v1/scenes").status_code == 403, "mutation without CSRF denied")
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            catalog = get("/api/v1/scenarios")["scenarios"]
            root = Path(__file__).resolve().parents[1]
            dataset = Path(os.environ.get("GMP_DATASET_DIR", root / "data"))
            if not dataset.is_dir():
                dataset = root.parent / "dataset"
            references = {path.parent.name for path in (dataset / "scenarios").glob("*/scenario.json")}
            check(bool(references) and references <= {item["id"] for item in catalog}, "all versioned reference scenarios are in catalog")
            first = scene("S00_smoke_rgb")
            live = plan(first)
            check(live["mode"] == "live" and live["plan"]["provenance"]["mode"] == "live", "real calculation is default")
            check(live["plan"]["provenance"].get("planner_module") == "h2.planner", "standalone H2 builds live plan")
            check(live["status"] == "SAFE", "live S00 safe")
            check(live["comparison"]["available"] and not live["comparison"]["optimality_proven"], "honest metric comparison")
            for fmt in ("geojson", "kml", "mission", "certificate", "pdf", "docx"):
                response = client.get(f"/api/v1/plans/{live['id']}/export", params={"format": fmt})
                response.raise_for_status()
                content = response.content
                if fmt in ("geojson", "mission", "certificate"):
                    json.loads(content)
                elif fmt == "kml":
                    ET.fromstring(content)
                elif fmt == "pdf":
                    check(content.startswith(b"%PDF"), "PDF signature")
                else:
                    with zipfile.ZipFile(io.BytesIO(content)) as archive:
                        check("План полётного задания" in archive.read("word/document.xml").decode(), "DOCX Cyrillic text")
                check(len(content) > 100, f"export {fmt}")
            archive = client.get(f"/api/v1/scenes/{first['id']}/download")
            archive.raise_for_status()
            uploaded = post("/api/v1/scenes/upload", files=[("files", ("input.zip", archive.content, "application/zip"))])
            report["scenes"].append(uploaded["id"])
            check(uploaded["validation"]["valid"], "download and reupload input ZIP")
            bad = io.BytesIO()
            with zipfile.ZipFile(bad, "w") as output:
                output.writestr("../outside.json", "{}")
            check(client.post("/api/v1/scenes/upload", files=[("files", ("bad.zip", bad.getvalue()))]).status_code == 422,
                  "ZIP traversal rejected")
            fleet = json.loads(json.dumps(first["fleet"]))
            fleet["uavs"][0]["service_time_s"] += 1
            changed = post(f"/api/v1/scenes/{first['id']}/versions", json={"name": "H3 acceptance edited version", "fleet": fleet})
            report["scenes"].append(changed["id"])
            check(changed["input_sha256"] != first["input_sha256"], "edited version has new fingerprint")
            check(get(f"/api/v1/scenes/{first['id']}")["input_sha256"] == first["input_sha256"], "original version immutable")
            check(client.post("/api/v1/plans", json={"scene_id": changed["id"], "mode": "fixture"}).status_code == 409,
                  "changed input cannot reuse fixture")
            refusal = plan(scene("S06_alternate_site"))
            check(refusal["status"] == "INFEASIBLE", "S06 proven refusal")
            check(client.get(f"/api/v1/plans/{refusal['id']}/export?format=kml").status_code == 409,
                  "refused flight export blocked")
            check(bool(refusal["recommendations"]), "recomputed alternatives available")
            rec = refusal["recommendations"][0]
            check(rec["verified"] and rec["certificate"]["status"] == "SAFE", "recommendation independently certified")
            applied = post(f"/api/v1/plans/{refusal['id']}/recommendations/{rec['id']}/apply")
            report["scenes"].append(applied["id"])
            check(applied["id"] != refusal["scene_id"] and get(f"/api/v1/plans/{applied['applied_plan_id']}")["status"] == "SAFE",
                  "user application creates verified new version")
            if args.all_fixtures:
                for item in catalog:
                    if item["id"] not in references:
                        continue
                    row = scene(item["id"])
                    for objective in item["objectives"]:
                        fixture = plan(row, objective=objective, mode="fixture")
                        check(fixture["mode"] == "fixture", "fixture mode stays explicit")
            report["passed"] = True
        except Exception as exc:
            report["passed"] = False
            report["error"] = str(exc)
            raise
        finally:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
