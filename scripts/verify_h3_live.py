#!/usr/bin/env python3
"""Sequential HTTPS acceptance of every catalog scene/objective in live mode.

Credentials and cookies are never written to the report. Created projects are
retained for inspection. Progress is atomically saved after every state change.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import time
from urllib.parse import urlsplit

import httpx


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def read_code(path: Path | None) -> str:
    code = os.environ.get("GMP_ACCESS_CODE", "")
    if path:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "GMP_ACCESS_CODE":
                values = shlex.split(value)
                code = values[0] if values else ""
                break
    if not code:
        raise ValueError("Set GMP_ACCESS_CODE or provide an environment file")
    return code


def progress_summary(events):
    return [{key: value for key, value in event.items() if key not in {"h1_output", "h2_output"}} for event in events]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/h3/live-all.json"))
    parser.add_argument("--budget", type=float, default=5)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--seed", type=int, default=20260918)
    parser.add_argument("--poll", type=float, default=2)
    parser.add_argument("--scenario", action="append", help="Exact catalog id; repeat to select an explicit acceptance subset")
    parser.add_argument("--loopback-staging", action="store_true",
                        help="Permit local HTTP only on 127.0.0.1 for private staging")
    parser.add_argument("--recheck", type=Path,
                        help="Revalidate existing plan IDs after a transport interruption; never queues new plans")
    args = parser.parse_args()
    local = urlsplit(args.url)
    staging = args.loopback_staging and local.scheme == "http" and local.hostname == "127.0.0.1"
    if not args.url.startswith("https://") and not staging:
        parser.error("Acceptance must use HTTPS")
    if not 1 <= args.budget <= 1800 or not 1 <= args.timeout <= 600 or args.poll <= 0:
        parser.error("Invalid budget, timeout or polling interval")
    code = read_code(args.env_file)
    report = {"schema": "geoscan.h3.live_acceptance.v1", "url": args.url.rstrip("/"),
              "started_at": utcnow(), "mode": "live", "planner_budget_s": args.budget,
              "timeout_per_case_s": args.timeout, "seed": args.seed, "cases": [],
              "scenes": [], "passed": False, "state": "starting"}
    if args.recheck:
        if args.recheck.resolve() == args.output.resolve():
            parser.error("Keep the original report; recheck output must be a different file")
        report = json.loads(args.recheck.read_text())
        if report.get("url") != args.url.rstrip("/") or report.get("schema") != "geoscan.h3.live_acceptance.v1":
            parser.error("Recheck report does not belong to this service")
        report.update(recheck_of=str(args.recheck), recheck_started_at=utcnow(), passed=False, state="rechecking")
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        report["updated_at"] = utcnow()
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(args.output)

    def safe_error(exc):
        return str(exc).replace(code, "[REDACTED]")[:2000]

    def expectation(item):
        expected = item.get("expected_status", "SAFE")
        return "INFEASIBLE" if expected.startswith("INFEASIBLE") else "SAFE"

    def evaluate(row, record, scene):
        validation = record.get("validation") or {}
        result = record.get("plan") or {}
        certificate = record.get("certificate")
        checks = {
            "mode_live": record.get("mode") == "live",
            "completed_without_error": record.get("status") in {"SAFE", "UNSAFE", "INFEASIBLE"},
            "expected_status": record.get("status") == row["expected_status"],
            "independent_validation_passed": validation.get("passed") is True,
            "input_snapshot_unchanged": record.get("input_sha256") == scene["input_sha256"],
        }
        if record.get("status") == "SAFE":
            checks.update({
                "live_candidate_provenance": result.get("provenance", {}).get("mode") == "live",
                "canonical_pipeline": result.get("provenance", {}).get("pipeline") == "h1_coverage -> h2 -> h3",
                "standalone_h2": result.get("provenance", {}).get("planner_module") == "h2.planner",
                "certificate_present": bool(certificate),
                "certificate_result_hash": bool(certificate) and certificate.get("result_sha256") == digest(result),
                "certificate_input_hash": bool(certificate) and certificate.get("input_sha256") == scene["input_sha256"],
            })
        else:
            checks["no_flight_certificate"] = certificate is None
            if record.get("status") == "INFEASIBLE":
                checks["necessary_condition_proof_verified"] = validation.get("metrics", {}).get("proof_verified") is True
        row.update(status=record.get("status"), mode=record.get("mode"), checks=checks,
                   passed=all(checks.values()), metrics=record.get("metrics"), validation=validation,
                   progress=progress_summary(record.get("progress", [])), diagnosis=result.get("diagnosis"),
                   provenance=result.get("provenance"), comparison=record.get("comparison"),
                   certificate_fingerprint=(certificate or {}).get("fingerprint_sha256"),
                   recommendations=[{key: recommendation.get(key) for key in ("id", "type", "title", "scene_id", "verified", "status")}
                                    for recommendation in record.get("recommendations", [])])

    save()
    try:
        with httpx.Client(base_url=args.url.rstrip("/"), timeout=60, follow_redirects=True,
                          trust_env=False) as client:
            def get(path):
                for attempt in range(3):
                    try:
                        response = client.get(path)
                        response.raise_for_status()
                        return response.json()
                    except httpx.TimeoutException as exc:
                        report.setdefault("transient_http_errors", []).append({"path": path, "at": utcnow(), "error": safe_error(exc)})
                        save()
                        if attempt == 2:
                            raise
                        time.sleep(2)

            def post(path, **kwargs):
                response = client.post(path, **kwargs)
                response.raise_for_status()
                return response.json()

            if get("/health/ready").get("status") != "ready":
                raise RuntimeError("Service is not ready")
            login = post("/api/v1/auth/login", json={"code": code})
            if staging:
                # Secure cookies remain mandatory in the service, including staging.
                token = client.cookies.get("gmp_h3_session")
                client.headers["Cookie"] = f"gmp_h3_session={token}"
            client.headers["X-CSRF-Token"] = login["csrf_token"]
            catalog = sorted(get("/api/v1/scenarios")["scenarios"], key=lambda item: item["id"])
            if args.scenario:
                missing = set(args.scenario) - {item["id"] for item in catalog}
                if missing:
                    raise ValueError(f"Unknown acceptance scenarios: {sorted(missing)}")
                catalog = [item for item in catalog if item["id"] in args.scenario]
            report["selected_scenarios"] = [item["id"] for item in catalog]
            report["catalog_count"] = len(catalog)
            report["objective_count"] = sum(len(item["objectives"]) for item in catalog)
            if args.recheck:
                original_scenes = {item["id"]: item for item in report["scenes"]}
                for row in report["cases"]:
                    row["initial_observation"] = {key: row.get(key) for key in ("status", "passed", "error", "finished_at")}
                    row.pop("error", None)
                    try:
                        scene = get(f"/api/v1/scenes/{row['scene_id']}")
                        if scene["input_sha256"] != original_scenes[row["scene_id"]]["input_sha256"]:
                            raise RuntimeError("Input snapshot changed since the initial acceptance")
                        record = get(f"/api/v1/plans/{row['plan_id']}")
                        if record["scene_id"] != row["scene_id"] or record["objective"] != row["objective"]:
                            raise RuntimeError("Stored plan does not match the original case")
                        evaluate(row, record, scene)
                    except Exception as exc:
                        row.update(passed=False, error=safe_error(exc))
                    row["rechecked_at"] = utcnow()
                    save()
                    print(f"RECHECK {'PASS' if row['passed'] else 'FAIL'} {row['scenario_id']} {row['objective']} {row['status']}", flush=True)
                expected = {(item["id"], objective) for item in catalog for objective in item["objectives"]}
                actual = {(row["scenario_id"], row["objective"]) for row in report["cases"]}
                report["passed"] = (bool(expected) and len(report["cases"]) == len(expected) and actual == expected
                                    and all(row["passed"] for row in report["cases"]))
                report["state"] = "rechecked"
                print(f"SUMMARY passed={report['passed']} cases={len(report['cases'])} output={args.output}", flush=True)
                return 0 if report["passed"] else 1
            report["state"] = "running"
            save()
            for item in catalog:
                scene = post("/api/v1/scenes", params={"scenario_id": item["id"]})
                report["scenes"].append({"id": scene["id"], "name": scene.get("name"),
                                         "scenario_id": item["id"], "purpose": "h3-live-acceptance",
                                         "input_sha256": scene.get("input_sha256")})
                save()
                for objective in item["objectives"]:
                    started = time.monotonic()
                    row = {"scenario_id": item["id"], "objective": objective, "scene_id": scene["id"],
                           "started_at": utcnow(), "expected_status": expectation(item),
                           "passed": False, "status": "submitting", "progress": []}
                    report["cases"].append(row)
                    save()
                    try:
                        if scene.get("validation", {}).get("valid") is not True:
                            raise RuntimeError("Catalog input did not pass ingestion validation")
                        queued = post("/api/v1/plans", json={"scene_id": scene["id"], "objective": objective,
                                      "mode": "live", "time_budget_s": args.budget, "seed": args.seed})
                        row.update(plan_id=queued["id"], status=queued["status"])
                        save()
                        print(f"START {item['id']} {objective} scene={scene['id']} plan={queued['id']}", flush=True)
                        previous = None
                        while time.monotonic() - started < args.timeout:
                            status = get(f"/api/v1/plans/{queued['id']}/status")
                            row.update(status=status.get("status"), progress=progress_summary(status.get("progress", [])),
                                       elapsed_s=round(time.monotonic() - started, 3))
                            current = (row["status"], json.dumps(row["progress"], sort_keys=True))
                            if current != previous:
                                previous = current
                                save()
                            if row["status"] == "ERROR" or any(p.get("stage") == "complete" for p in row["progress"]):
                                record = get(f"/api/v1/plans/{queued['id']}")
                                evaluate(row, record, scene)
                                break
                            time.sleep(args.poll)
                        else:
                            row["status"] = "TIMEOUT"
                            raise TimeoutError(f"Case exceeded {args.timeout:g} seconds; no additional work queued")
                    except Exception as exc:
                        row.update(passed=False, error=safe_error(exc))
                        if isinstance(exc, TimeoutError):
                            raise
                    finally:
                        row.update(finished_at=utcnow(), elapsed_s=round(time.monotonic() - started, 3))
                        save()
                        codes = [v.get("code") for v in (row.get("validation") or {}).get("violations", [])]
                        print(f"{'PASS' if row['passed'] else 'FAIL'} {item['id']} {objective} "
                              f"{row['status']} {row['elapsed_s']:.1f}s violations={codes[:12]}", flush=True)
            report["passed"] = (report["catalog_count"] > 0
                                and len(report["cases"]) == report["objective_count"] and all(row["passed"] for row in report["cases"]))
            report["state"] = "complete"
    except Exception as exc:
        report.update(passed=False, state="aborted", error=safe_error(exc))
        print(f"ABORT {type(exc).__name__}: {safe_error(exc)}", flush=True)
    finally:
        report["finished_at"] = utcnow()
        save()
    print(f"SUMMARY passed={report['passed']} cases={len(report['cases'])} output={args.output}", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
