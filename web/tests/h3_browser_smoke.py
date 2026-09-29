"""Authenticated H3 browser smoke; creates and removes its own test projects.

Run with a separate test environment when possible. The access code is read
from GMP_ACCESS_CODE or the specified private environment file, never logged.
"""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import shlex
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import sync_playwright
from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/h3/browser"))
    args = parser.parse_args()
    code = os.environ.get("GMP_ACCESS_CODE", "")
    if not code and args.env_file:
        values = dict(line.split("=", 1) for line in args.env_file.read_text().splitlines()
                      if "=" in line and not line.lstrip().startswith("#"))
        code = shlex.split(values.get("GMP_ACCESS_CODE", ""))[0]
    if not code:
        raise SystemExit("GMP_ACCESS_CODE or --env-file is required")
    args.output.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    created: list[str] = []
    report: dict = {"passed": False, "url": args.url,
                    "checked_at": datetime.now(timezone.utc).isoformat()}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=[
            "--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
        ])
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        def remember_created(response):
            path = urlsplit(response.url).path
            if (response.request.method == "POST" and response.ok
                    and (path == "/api/v1/scenes" or
                         path.startswith("/api/v1/scenes/") and
                         path.endswith(("/versions", "/save-as")))):
                identifier = response.json().get("id")
                if identifier and identifier not in created:
                    created.append(identifier)

        page.on("response", remember_created)
        page.set_default_timeout(30000)
        try:
            page.goto(args.url, wait_until="domcontentloaded")
            page.locator("#access-code").fill(code)
            page.locator("#login-form button[type=submit]").click()
            page.wait_for_function("state.csrf && state.scenarios.length >= 18")
            page.locator("#catalog-open").click()
            page.locator("#open-templates").click()
            page.locator('[data-template-folder="simple"]').click()
            assert page.locator("#catalog-list .scenario-item").count() == page.evaluate("state.scenarios.filter(s => !s.real_elevation).length")
            page.locator(".scenario-item").filter(has_text="S00").click()
            page.wait_for_function("state.mapReady && state.scene?.scenario_id === 'S00_smoke_rgb'")
            assert page.locator("#catalog-list .terrain-catalog-group").count() == 2
            assert not page.locator("#terrain-dialog").is_visible()
            page.locator("[data-basemap=terrain]").click()
            page.wait_for_function("basemap.mode === 'terrain' && basemap.active.flat")
            assert page.evaluate("basemap.active.min_m") == 160
            page.locator("[data-basemap=map]").click()
            original = page.evaluate("state.scene")
            assert original["id"] in created
            page.wait_for_timeout(1200)
            assert page.evaluate("state.draw.getAll().features.length") >= 4
            report["catalog_count"] = page.evaluate("state.scenarios.length")
            report["scene_validation"] = page.evaluate("state.scene.validation.valid")

            # Actual Draw interaction, not programmatic geometry replacement.
            count = page.evaluate("state.draw.getAll().features.length")
            page.locator("[data-tool=landing_sites]").click()
            bounds = page.locator("#map").bounding_box()
            page.mouse.click(bounds["x"] + bounds["width"] * 0.6,
                             bounds["y"] + bounds["height"] * 0.6)
            page.wait_for_function(f"state.draw.getAll().features.length === {count + 1}")
            assert page.evaluate("state.dirty")
            page.locator("#delete-feature").click()
            assert page.evaluate("state.draw.getAll().features.length") == count
            page.locator('[data-sidebar=fleet]').click()
            page.locator('.fleet-item').first.evaluate('e=>e.open=true')
            refuel=page.locator('.fleet-item').first.get_by_label('Дозарядка: Любая', exact=True)
            refuel.uncheck()
            assert page.evaluate('state.draft.fleet.uavs[0].refuel_sites')==[]
            refuel.check()
            assert page.evaluate('state.draft.fleet.uavs[0].refuel_sites') is None
            name = f"H3 browser smoke {uuid4().hex[:10]}"
            page.locator("#save-as-scene").click()
            page.locator("#save-as-name").fill(name)
            page.locator("#save-as-form button[type=submit]").click()
            page.wait_for_function("name => state.scene.name === name && !state.dirty && !state.busy",
                                   arg=name, timeout=120000)
            saved = page.evaluate("state.scene")
            assert saved["id"] != original["id"] and saved["id"] in created
            assert saved.get("parent_id") in created and not saved["template_readonly"]
            unchanged_response = page.request.get(args.url.rstrip("/") + f"/api/v1/scenes/{original['id']}")
            assert unchanged_response.ok
            unchanged = unchanged_response.json()
            for key in ("name", "input_sha256", "layers", "mission", "fleet"):
                assert unchanged[key] == original[key], key
            report["immutable_version"] = True

            # Reopen the saved input and compute it through the real H1/H2/H3 pipeline.
            page.locator("#catalog-open").click()
            page.locator("#open-saved").click()
            page.locator(f"[data-scene-id=\"{saved['id']}\"]").click()
            page.wait_for_function("id => state.scene.id === id && !state.sceneLoading", arg=saved["id"])
            assert not page.locator("#terrain-dialog").is_visible()
            page.locator("[data-sidebar=run]").click()
            assert page.locator("#search-depth, #budget").count() == 0
            page.locator("#run-live").click()
            page.wait_for_function("state.plan?.mode === 'live' && TERMINAL.has(state.plan.status.toUpperCase())", timeout=180000)
            report["live_status"] = page.evaluate("state.plan.status")
            assert report["live_status"] == "SAFE"
            report["live_planner"] = page.evaluate("state.plan.plan.provenance.planner_module")
            assert report["live_planner"] == "h2.planner"
            assert page.evaluate("state.plan.plan.provenance.routing.depth") == "deep"
            assert page.evaluate("state.plan.plan.provenance.h2_settings.time_budget_s") == 180
            assert page.evaluate("state.plan.scene_id === state.scene.id && state.plan.certificate.input_sha256 === state.scene.input_sha256")
            report["certificate_binds_saved_input"] = True
            assert page.locator("#result-content .objective-value").is_visible()
            assert page.locator("#plan-overlay .objective-value").is_visible()
            assert page.evaluate("state.plan.plan.provenance.routing.seed") == 20260918
            report["primary_metric"] = page.locator("#result-content .objective-value").inner_text()
            page.locator('[data-result=graph]').click()
            assert page.locator('#graph-profile option').count()>0
            assert page.evaluate("state.map.getLayoutProperty('routing-graph-edges','visibility')")=='visible'
            assert page.evaluate("state.map.getSource('routing-graph')._data.features.some(f=>f.properties.directed)")
            assert page.evaluate("state.plan.plan.h2_output.routing_graph.summary.base_connections")=='all_corners'
            report['routing_graph_visible']=True
            page.locator("[data-result=timeline]").click()
            page.locator("#playback-play").click()
            page.wait_for_timeout(500)
            assert page.evaluate("state.playbackValue") > 0
            assert page.evaluate("state.map.getSource('playback')._data.features.length") > 0
            page.locator("#playback-play").click()
            report["playback_moves"] = True
            page.locator("[data-result=export]").click()
            formats = {}
            for link in page.locator(".export-link:not(.disabled)").all():
                href = link.get_attribute("href")
                response = page.request.get(args.url.rstrip("/") + href)
                formats[href.rsplit("=", 1)[-1]] = response.status
                assert response.ok, href
            report["exports"] = formats
            assert {"geojson", "kml", "mission", "certificate", "pdf", "docx"} <= formats.keys()
            page.locator("[data-result=summary]").click()
            page.wait_for_timeout(1200)
            page.screenshot(path=str(args.output / "desktop.png"), timeout=120000, animations="disabled")
            colors = Image.open(io.BytesIO(page.locator("#map canvas").screenshot())).convert("RGB").resize((128, 128)).getcolors(16384)
            report["desktop_canvas_colors"] = len(colors or [])
            assert report["desktop_canvas_colors"] > 128, "Desktop map canvas is blank"
            for width, height in [(390, 844), (360, 740)]:
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(900)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
                page.screenshot(path=str(args.output / f"mobile-{width}.png"), timeout=120000, animations="disabled")
                colors = Image.open(io.BytesIO(page.locator("#map canvas").screenshot())).convert("RGB").resize((128, 128)).getcolors(16384)
                report[f"mobile_{width}_canvas_colors"] = len(colors or [])
                assert report[f"mobile_{width}_canvas_colors"] > 128, "Mobile map canvas is blank"
            report["mobile_overflow"] = False
            report["javascript_errors"] = errors
            assert not errors
            report["passed"] = True
        finally:
            cleanup = []
            for identifier in reversed(created):
                try:
                    csrf = page.evaluate("state.csrf")
                    response = page.request.delete(args.url.rstrip("/") + f"/api/v1/scenes/{identifier}", headers={"X-CSRF-Token": csrf})
                    cleanup.append(response.status in (200, 204, 404))
                except Exception:
                    cleanup.append(False)
            report["cleanup"] = {"created": len(created), "removed": sum(cleanup)}
            report["passed"] = report["passed"] and all(cleanup)
            browser.close()
            (args.output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    assert report["passed"], "Browser checks or test-scene cleanup failed"
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
