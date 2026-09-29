#!/usr/bin/env python3
"""Read-only desktop/mobile map pixel probe; does not create jobs or projects."""
import argparse
import io
import json
from pathlib import Path

from PIL import Image, ImageStat
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    values = dict(line.split("=", 1) for line in args.env_file.read_text().splitlines() if line and not line.startswith("#"))
    args.output.mkdir(parents=True, exist_ok=True)
    errors, rows = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until="domcontentloaded")
        page.locator("#access-code").fill(values["GMP_ACCESS_CODE"])
        page.locator("#login-form button[type=submit]").click()
        page.wait_for_function("state.mapReady && state.scene", timeout=60000)
        initial = page.evaluate("state.scene.scenario_id")
        assert initial == "S01_msu_100km2", initial
        identifier = page.evaluate("state.projects.find(p => p.scenario_id === 'S00_smoke_rgb').id")
        page.locator("#project-select").select_option(identifier)
        page.wait_for_function("state.scene.scenario_id === 'S00_smoke_rgb'")
        for width, height in [(1440, 1000), (390, 844), (360, 740)]:
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(1800)
            content = page.locator("canvas.maplibregl-canvas").screenshot()
            bitmap = Image.open(io.BytesIO(content)).convert("RGB")
            variance = ImageStat.Stat(bitmap).var
            colors = len(bitmap.getcolors(bitmap.width * bitmap.height) or [])
            assert min(variance) > 20 and colors > 100, (variance, colors)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert page.evaluate("state.draw.getAll().features.length") >= 4
            page.screenshot(path=str(args.output / f"map-{width}.png"))
            rows.append({"width": width, "height": height, "canvas_pixels": list(bitmap.size),
                         "channel_variance": variance, "distinct_colors": colors, "horizontal_overflow": False})
        browser.close()
    assert not errors, errors
    result = {"passed": True, "fresh_browser_scenario": initial, "javascript_errors": errors, "viewports": rows}
    (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
