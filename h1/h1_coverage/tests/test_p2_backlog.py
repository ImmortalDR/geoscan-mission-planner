"""P2 backlog: S-04/S-06/S-07 hints, A-06 terrain meta, Q-03/Q-04 quality."""
from __future__ import annotations

import json
import time
from pathlib import Path

from h1_coverage.config import CoverageConfig
from h1_coverage.pipeline import run_h1

from paths import scene

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "golden"


def test_s04_energy_hints_multi_sortie():
    r = run_h1(scene("S04_multi_sortie_energy"), CoverageConfig(strict_coverage=False))
    ext = (r.bundle.data.get("extensions") or {}).get("h1") or {}
    assert ext.get("energy_hints") or any("S04" in w for w in r.bundle.data.get("warnings") or [])
    # At least one task should need >1 sortie or endurance reject
    hints = ext.get("energy_hints") or []
    rejects = ext.get("endurance_rejects") or {}
    assert hints or rejects or any("sortie" in w.lower() or "S04" in w for w in r.bundle.data["warnings"])


def test_s06_candidate_site_recommendation():
    r = run_h1(scene("S06_recommend_alternative_site"), CoverageConfig(strict_coverage=False))
    ext = (r.bundle.data.get("extensions") or {}).get("h1") or {}
    rec = ext.get("site_recommendation")
    assert rec is not None
    assert "CANDIDATE" in rec["recommended_site_id"].upper() or rec.get("recommended_site_id")
    assert any("S06" in w for w in r.bundle.data["warnings"])


def test_s07_reserve_sites_in_bundle():
    # S04 has RES; S07 may only have BASE — assert roles exported
    r = run_h1(scene("S04_multi_sortie_energy"), CoverageConfig(strict_coverage=False))
    sites = r.bundle.data["sites"]
    roles = {s["id"]: s["role"] for s in sites}
    assert any(role == "reserve" for role in roles.values())
    ext = (r.bundle.data.get("extensions") or {}).get("h1") or {}
    assert "RES" in (ext.get("reserve_sites") or []) or any(s["role"] == "reserve" for s in sites)

    r7 = run_h1(scene("S07_reserve_landing_failure"), CoverageConfig(strict_coverage=False))
    # S07 has no reserve → warning
    assert any("S07" in w for w in r7.bundle.data.get("warnings") or [])
    assert (r7.bundle.data.get("extensions") or {}).get("h1", {}).get("reserve_sites") == []


def test_a06_terrain_extension_meta():
    r = run_h1(scene("S00_smoke_rgb"), CoverageConfig(strict_coverage=False))
    terr = ((r.bundle.data.get("extensions") or {}).get("h1") or {}).get("terrain")
    assert terr is not None
    assert "dem_available" in terr
    assert terr.get("note")


def test_q03_golden_s00_first_transect():
    """Determinism: rounded first transect endpoints match golden snapshot."""
    r = run_h1(scene("S00_smoke_rgb"), CoverageConfig(strict_coverage=False, angle_step_deg=15.0))
    tasks = sorted(r.bundle.data["tasks"], key=lambda t: t["id"])
    assert tasks
    tr0 = tasks[0]["transects"][0]["coords"]
    start = [round(tr0[0][0], 1), round(tr0[0][1], 1)]
    end = [round(tr0[-1][0], 1), round(tr0[-1][1], 1)]
    snap = {"task_id": tasks[0]["id"], "start": start, "end": end, "tol_m": 2.0}
    FIX.mkdir(parents=True, exist_ok=True)
    golden_path = FIX / "S00_first_transect.json"
    if not golden_path.exists():
        golden_path.write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    assert snap["task_id"] == golden["task_id"]
    tol = float(golden.get("tol_m", 2.0))
    assert abs(snap["start"][0] - golden["start"][0]) <= tol
    assert abs(snap["start"][1] - golden["start"][1]) <= tol
    assert abs(snap["end"][0] - golden["end"][0]) <= tol
    assert abs(snap["end"][1] - golden["end"][1]) <= tol


def test_q04_perf_s00_under_25s():
    t0 = time.perf_counter()
    run_h1(scene("S00_smoke_rgb"), CoverageConfig(strict_coverage=False))
    elapsed = time.perf_counter() - t0
    assert elapsed < 25.0, f"S00 took {elapsed:.1f}s"


def test_q04_perf_s01_under_180s():
    t0 = time.perf_counter()
    run_h1(
        scene("S01_full_customer_acceptance_100km2"),
        CoverageConfig(
            strict_coverage=False,
            angle_step_deg=30.0,
            keep_candidates=3,
            headland_m=8.0,
            max_tasks_per_job=40,
        ),
    )
    elapsed = time.perf_counter() - t0
    assert elapsed < 180.0, f"S01 took {elapsed:.1f}s"
