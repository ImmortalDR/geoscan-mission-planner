"""Browser regression for a custom-named single KML layer upload."""

import argparse
import json
from pathlib import Path
import shlex
import xml.etree.ElementTree as ET

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("artifacts/h3/upload-browser"))
    args = parser.parse_args()
    values = dict(line.split("=", 1) for line in args.env_file.read_text().splitlines()
                  if "=" in line and not line.lstrip().startswith("#"))
    code = shlex.split(values["GMP_ACCESS_CODE"])[0]
    args.output.mkdir(parents=True, exist_ok=True)
    created = []
    errors = []
    report = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 1280, "height": 850})
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(args.url, wait_until="domcontentloaded")
            page.locator("#access-code").fill(code)
            page.locator("#login-form button[type=submit]").click()
            page.wait_for_function("state.csrf && state.scenarios.length >= 18")
            page.locator("#catalog-open").click()
            page.locator("#open-templates").click()
            page.locator('[data-template-folder="simple"]').click()
            page.locator(".scenario-item").filter(has_text="S00").click()
            page.wait_for_function("state.scene?.scenario_id === 'S00_smoke_rgb'")
            created.append(page.evaluate("state.scene.id"))
            source = page.evaluate("state.scene.layers.survey_areas.features[0]")
            root = ET.Element("kml", {"xmlns": "http://www.opengis.net/kml/2.2"})
            placemark = ET.SubElement(ET.SubElement(root, "Document"), "Placemark")
            ET.SubElement(placemark, "name").text = source["properties"]["id"]
            properties = ET.SubElement(placemark, "ExtendedData")
            for name, value in source["properties"].items():
                ET.SubElement(ET.SubElement(properties, "Data", {"name": name}), "value").text = str(value)
            boundary = ET.SubElement(ET.SubElement(placemark, "Polygon"), "outerBoundaryIs")
            ET.SubElement(ET.SubElement(boundary, "LinearRing"), "coordinates").text = " ".join(
                f"{point[0]},{point[1]},0" for point in source["geometry"]["coordinates"][0])
            page.locator("#catalog-open").click()
            page.locator("#import-open").click()
            page.locator("#import-files").set_input_files({"name": "my-survey-boundary.kml", "mimeType": "application/vnd.google-earth.kml+xml", "buffer": ET.tostring(root)})
            page.locator("#import-layer").select_option("survey_areas")
            assert page.locator("#import-layer-field").is_visible()
            assert "survey_areas.kml" in page.locator("#import-selection").inner_text()
            page.locator("#import-base").uncheck()
            page.locator("#import-form button[type=submit]").click()
            assert "исходного сценария" in page.locator("#import-error").inner_text()
            assert page.evaluate("state.scene.id") == created[0]
            report["missing_base_rejected"] = True
            page.locator("#import-base").check()
            page.locator("#import-form button[type=submit]").click()
            page.wait_for_function("id => state.scene.id !== id", arg=created[0], timeout=120000)
            created.append(page.evaluate("state.scene.id"))
            assert page.evaluate("state.scene.parent_id") == created[0]
            assert page.evaluate("state.scene.layers.survey_areas.features[0].geometry") == source["geometry"]
            assert page.evaluate("state.scene.validation.valid")
            report["custom_kml_new_version"] = True
            report["geometry_preserved"] = True
            page.locator("#catalog-open").click()
            page.locator("#import-open").click()
            page.locator("#import-files").set_input_files({"name": "moscow-surface.tiff", "mimeType": "image/tiff", "buffer": b"mapping-only"})
            assert page.evaluate("singleImport().filename") == "dem.tif"
            assert page.locator("#import-layer-field").is_hidden()
            report["geotiff_normalized"] = True
            page.locator("#import-files").set_input_files([
                {"name": "a.geojson", "mimeType": "application/json", "buffer": b"{}"},
                {"name": "b.kml", "mimeType": "application/xml", "buffer": b"<kml/>"},
            ])
            assert page.evaluate("singleImport()") is None
            report["multiple_filenames_preserved"] = True
            page.locator("#import-files").set_input_files({"name": "bundle.zip", "mimeType": "application/zip", "buffer": b"mapping-only"})
            assert page.evaluate("singleImport()") is None
            report["zip_filename_preserved"] = True
            report["javascript_errors"] = errors
            assert not errors
        finally:
            for identifier in reversed(created):
                page.request.delete(args.url.rstrip("/") + f"/api/v1/scenes/{identifier}", headers={"X-CSRF-Token": page.evaluate("state.csrf")})
            browser.close()
            (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
