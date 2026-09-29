"""Isolated frontend test with real local Sentinel crops and mocked read-only API.

No server or deployment is needed. Application requests are fulfilled directly
from local files by Playwright; OpenStreetMap requests are aborted, never fetched.
This checks the frontend, not authentication or planner integration.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web", type=Path, default=Path(__file__).parents[1] / "h3")
    parser.add_argument("--dataset", type=Path, default=Path("/root/h3/data"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/h3/imagery-browser"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    web = args.web.resolve()
    origin = "https://h3-ui.test"
    layers = ["survey_areas", "allowed_airspace", "no_fly_zones", "temporal_airspace", "landing_sites", "obstacles"]
    scenes = {}
    plans = {}
    for folder in sorted((args.dataset / "scenarios").glob("S*")):
        read = lambda name: json.loads((folder / "input" / name).read_text())
        scene = {"id": folder.name, "scenario_id": folder.name, "name": folder.name, "version": 1,
                 "parent_id": None, "expires_at": "2026-12-01T00:00:00Z",
                 "layers": {name: read(name + ".geojson") for name in layers},
                 **{name: read(name + ".json") for name in ["mission", "fleet", "payload_catalog", "metadata"]},
                 "validation": {"valid": True, "errors": [], "warnings": [], "terrain_available": True}}
        scenes[folder.name] = scene
        reference = folder / "expected/makespan/result.json"
        if reference.exists():
            result = json.loads(reference.read_text())
            plans[folder.name] = {"id": folder.name, "scene_id": folder.name, "mode": "fixture", "status": result["status"],
                                  "plan": result, "metrics": result.get("metrics", {}), "validation": {"passed": True}, "recommendations": []}
    failures = {"manifest": False, "image": False}
    errors = []
    blocked_tiles = []
    report = {"test_scope": "offline frontend with real local image files; mocked read-only API"}

    def app_route(route):
        parsed = urlparse(route.request.url)
        path = parsed.path
        if path.startswith("/api/"):
            if route.request.method != "GET":
                raise AssertionError("Imagery test must not write API state")
            if path == "/api/v1/auth/status":
                payload = {"authenticated": True, "csrf_token": "offline-test", "configured": True}
            elif path == "/api/v1/scenarios":
                payload = {"scenarios": [{"id": key, "name": key} for key in scenes]}
            elif path == "/api/v1/scenes":
                payload = {"scenes": list(scenes.values())}
            elif path == "/api/v1/uav-models":
                payload = {"models": [], "defaults": []}
            elif path.startswith("/api/v1/scenes/"):
                payload = scenes[path.rsplit("/", 1)[-1]]
            elif path == "/api/v1/plans":
                identifier = parse_qs(parsed.query).get("scene_id", [""])[0]
                payload = {"plans": [plans[identifier]] if identifier in plans else []}
            elif path.startswith("/api/v1/plans/"):
                payload = plans[path.rsplit("/", 1)[-1]]
            else:
                raise AssertionError(f"Unexpected API request: {path}")
            route.fulfill(json=payload)
            return
        if path == "/static/imagery/manifest.json" and failures["manifest"]:
            route.fulfill(status=503, body="Simulated manifest failure")
            return
        if path.startswith("/static/imagery/") and path.endswith(".jpg") and failures["image"]:
            route.fulfill(status=503, body="Simulated image failure")
            return
        local = web / ("index.html" if path == "/" else path.removeprefix("/static/"))
        if not local.resolve().is_relative_to(web) or not local.is_file():
            route.fulfill(status=404, body="Not found")
            return
        route.fulfill(path=str(local), content_type=mimetypes.guess_type(local.name)[0] or "application/octet-stream")

    def wait_imagery(page, image_id):
        page.wait_for_function("id => basemap.mode === 'imagery' && basemap.active?.id === id && state.map.isSourceLoaded('imagery-' + id)", arg=image_id, timeout=45000)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.route(origin + "/**", app_route)
        context.route("https://tile.openstreetmap.org/**", lambda route: (blocked_tiles.append(route.request.url), route.abort()))
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(origin, wait_until="domcontentloaded")
            wait_imagery(page, "msu")
            report["default_imagery"] = True
            before = page.evaluate("JSON.stringify(state.draw.getAll())")
            page.locator("[data-basemap=map]").click()
            assert page.evaluate("basemap.mode") == "map"
            assert page.evaluate("JSON.stringify(state.draw.getAll())") == before
            assert page.evaluate("localStorage.getItem('geoscan.h3.basemap')") == "map"
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("state.scene && state.mapReady && basemap.manifestStatus === 'ready'")
            assert page.evaluate("basemap.mode") == "map"
            report["preference_persists"] = True
            page.locator("[data-basemap=imagery]").click()
            wait_imagery(page, "msu")
            for scenario, image_id in [("S02_peredelkino", "peredelkino"), ("S03_orekhovo_domodedovskaya", "orekhovo")]:
                page.locator("#project-select").select_option(scenario)
                wait_imagery(page, image_id)
            report["all_three_crops_render"] = True
            page.locator("#project-select").select_option("S07_unreachable_landing")
            page.wait_for_function("basemap.mode === 'map' && basemap.message.includes('не покрывает')")
            report["outside_coverage_fallback"] = True
            page.locator("#project-select").select_option("S01_msu_100km2")
            wait_imagery(page, "msu")
            page.evaluate("openPlan('S01_msu_100km2')")
            page.wait_for_timeout(1200)
            order = page.evaluate("state.map.getStyle().layers.map(layer => layer.id)")
            assert order.index("osm") < order.index("imagery-msu") < order.index("h3-polygon-fill.cold")
            assert order.index("imagery-msu") < order.index("routes")
            assert "28.05.2025" in page.locator("#imagery-info").inner_text()
            assert "проверки препятствий" in page.locator("#imagery-info").inner_text()
            report["layer_order_and_attribution"] = True
            page.screenshot(path=str(args.output / "desktop.png"), animations="disabled", timeout=120000)
            page.set_viewport_size({"width": 360, "height": 740})
            page.wait_for_timeout(1200)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            toolbar = page.locator("#map-toolbar").bounding_box()
            controls = page.locator(".map-context").bounding_box()
            assert controls["y"] >= toolbar["y"] + toolbar["height"]
            page.screenshot(path=str(args.output / "mobile.png"), animations="disabled", timeout=120000)
            report["mobile_no_overflow_or_toolbar_overlap"] = True
            failures["image"] = True
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("basemap.mode === 'map' && basemap.message.includes('Не удалось загрузить снимок')", timeout=45000)
            assert page.evaluate("state.draw.getAll().features.length") > 0
            report["image_error_fallback_preserves_editor"] = True
            failures["manifest"] = True
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("basemap.manifestStatus === 'error' && basemap.mode === 'map'")
            assert page.locator("#basemap-message").inner_text()
            report["manifest_error_fallback"] = True
            report["javascript_errors"] = errors
            report["osm_requests_aborted_locally"] = len(blocked_tiles)
            assert not errors
        finally:
            report["javascript_errors"] = errors
            browser.close()
            (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
