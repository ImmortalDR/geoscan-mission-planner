"""Check the local viewer imagery without deploying or calling the planner."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import threading

from PIL import Image, ImageStat
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def check(output: Path) -> dict:
    spec = importlib.util.spec_from_file_location("viewer_server", ROOT / "viewer/server.py")
    server_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server_module)

    class QuietHandler(server_module.Handler):
        def log_message(self, *_args):
            pass

    manifest = json.loads((ROOT / "viewer/static/imagery/manifest.json").read_text())
    assert manifest["schema"] == "geoscan.viewer.imagery.v1"
    output.mkdir(parents=True, exist_ok=True)
    server = server_module.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    errors, report = [], {"checks": [], "viewports": []}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin)
            page.wait_for_selector("#map.imagery-visible image")
            assert manifest["attribution"] in page.locator("#map-credit").inner_text()
            assert "28.05.2025" in page.locator("#map-credit").inner_text()
            assert "10 м/пикс" in page.locator("#map-credit").inner_text()
            assert page.locator("#map").evaluate("el => el.firstElementChild.id") == "map-imagery"
            assert page.evaluate("""() => {
                const el=document.querySelector('#map image'), p=mapView.projection;
                const entry=imageryManifest.images.find(i=>i.url===el.getAttribute('href'));
                const [x,y]=p.xy([entry.bounds_xy[0],entry.bounds_xy[3]]);
                return Math.abs(Number(el.getAttribute('x'))-x)<1e-8 &&
                    Math.abs(Number(el.getAttribute('y'))-y)<1e-8 &&
                    Math.abs(Number(el.getAttribute('width'))-(entry.bounds_xy[2]-entry.bounds_xy[0])*p.scale)<1e-8 &&
                    Math.abs(Number(el.getAttribute('height'))-(entry.bounds_xy[3]-entry.bounds_xy[1])*p.scale)<1e-8 &&
                    entry.metric_epsg===p.epsg;
            }""")
            points = page.locator("#map polyline:not(.route-casing)").evaluate_all(
                "elements => elements.map(el=>el.getAttribute('points'))"
            )
            assert points
            page.locator("#basemap-scheme").click()
            assert page.locator("#map image").count() == 0
            assert page.locator("#map polyline:not(.route-casing)").evaluate_all(
                "elements => elements.map(el=>el.getAttribute('points'))"
            ) == points
            page.reload()
            page.wait_for_selector("#map polyline:not(.route-casing)", state="attached")
            assert page.locator("#basemap-scheme").get_attribute("aria-pressed") == "true"
            assert page.locator("#map image").count() == 0
            page.locator("#basemap-image").click()
            page.wait_for_selector("#map.imagery-visible image")
            report["checks"].extend(["metric_alignment", "image_below_tasks", "attribution", "toggle_preserves_geometry", "mode_persistence"])

            initial = page.locator("#map").get_attribute("viewBox")
            page.locator("#zoom-in").click()
            zoomed = page.locator("#map").get_attribute("viewBox")
            assert float(zoomed.split()[2]) < float(initial.split()[2])
            bounds = page.locator("#map").bounding_box()
            x, y = bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + 45, y + 25, steps=5)
            page.mouse.up()
            assert page.locator("#map").get_attribute("viewBox") != zoomed
            page.locator("#zoom-reset").click()
            assert page.locator("#map").get_attribute("viewBox") == initial
            report["checks"].append("pan_zoom")

            scenarios = page.locator("#scenario option").evaluate_all("els=>els.map(el=>el.value)")
            for scenario in scenarios:
                page.select_option("#scenario", scenario)
                page.wait_for_function("name => JSON.parse(document.querySelector('#raw').textContent).scene_id===name", arg=scenario.removesuffix(".bundle.json"))
                page.wait_for_selector("#map.imagery-visible image")
                assert page.evaluate("""() => {
                    const bundle=JSON.parse(document.querySelector('#raw').textContent);
                    const lines=[...document.querySelectorAll('#map polyline:not(.route-casing)')];
                    const expected=bundle.tasks.flatMap(task=>task.transects.map(tr=>tr.coords.map(p=>mapView.projection.xy(p).join(',')).join(' ')));
                    return document.querySelectorAll('#map polygon[data-task-id]').length===bundle.tasks.length &&
                        document.querySelectorAll('#map circle').length===bundle.sites.length &&
                        lines.length===expected.length && lines.every((line,i)=>line.getAttribute('points')===expected[i]);
                }""")
            report["scenario_count"] = len(scenarios)
            report["checks"].append("all_scenario_imagery")
            page.select_option("#scenario", scenarios[1] if len(scenarios) > 1 else scenarios[0])
            page.wait_for_selector("#map.imagery-visible image")
            for width, height in [(1440, 1000), (390, 844), (360, 740)]:
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(100)
                overflow = page.evaluate("""() => [...document.querySelectorAll('body *')].filter(el=>{
                    const r=el.getBoundingClientRect();return r.width && (r.left<-.5||r.right>innerWidth+.5);
                }).slice(0,12).map(el=>({tag:el.tagName,id:el.id,class:el.className.baseVal??el.className,width:el.getBoundingClientRect().width}))""")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"horizontal overflow at {width}: {overflow}"
                assert page.evaluate("""() => {
                    const a=document.querySelector('.basemap-switch').getBoundingClientRect();
                    const b=document.querySelector('.map-controls').getBoundingClientRect();
                    return a.right<=b.left;
                }"""), f"overlapping controls at {width}"
                assert page.locator("#map-credit").evaluate("el=>el.scrollWidth<=el.clientWidth")
                screenshot = output / f"map-{width}.png"
                page.locator(".map-wrap").screenshot(path=str(screenshot))
                with Image.open(screenshot) as image:
                    variance = ImageStat.Stat(image.convert("RGB")).var
                    assert min(variance) > 100
                    report["viewports"].append({"width": width, "height": height, "channel_variance": variance})
            report["checks"].append("desktop_mobile_layout")

            failed = context.new_page()
            failed.route("**/imagery/*.jpg", lambda route: route.abort())
            failed.goto(origin)
            failed.wait_for_function("document.querySelector('#map-credit').textContent==='Снимок недоступен'")
            assert failed.locator("#map image").count() == 0
            assert failed.locator("#map polyline:not(.route-casing)").count() > 0
            assert "imagery-visible" not in (failed.locator("#map").get_attribute("class") or "")
            failed.close()
            report["checks"].append("failed_image_fallback")

            mismatched = context.new_page()
            incompatible = {**manifest, "images": [{**item, "metric_epsg": 3857} for item in manifest["images"]]}
            mismatched.route("**/imagery/manifest.json", lambda route: route.fulfill(json=incompatible))
            mismatched.goto(origin)
            mismatched.wait_for_function("document.querySelector('#map-credit').textContent==='Снимок недоступен для этой сцены'")
            assert mismatched.locator("#map image").count() == 0
            assert mismatched.locator("#map polyline:not(.route-casing)").count() > 0
            mismatched.close()
            report["checks"].append("mismatched_crs_fallback")

            delayed = context.new_page()
            pending = []
            delayed.route("**/imagery/manifest.json", lambda route: pending.append(route))
            delayed.goto(origin, wait_until="domcontentloaded")
            delayed.wait_for_selector("#map polyline:not(.route-casing)", state="attached")
            delayed.select_option("#scenario", scenarios[-1])
            delayed.wait_for_function("name => JSON.parse(document.querySelector('#raw').textContent).scene_id===name", arg=scenarios[-1].removesuffix(".bundle.json"))
            assert pending
            pending[0].fulfill(json=manifest)
            delayed.wait_for_selector("#map.imagery-visible image")
            assert delayed.locator("#scenario").input_value() == scenarios[-1]
            assert json.loads(delayed.locator("#raw").text_content())["scene_id"] == scenarios[-1].removesuffix(".bundle.json")
            delayed.close()
            report["checks"].append("delayed_manifest_scene_race")
            assert not errors, errors
            report["javascript_errors"] = errors
            report["status"] = "passed"
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/viewer-imagery-check")
    args = parser.parse_args()
    print(json.dumps(check(args.output), ensure_ascii=False, indent=2))
