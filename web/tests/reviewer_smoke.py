"""Isolated browser acceptance for named roles, real map and reviewer evidence.

Install playwright and its Chromium in a separate test environment. No live
workspace is modified. Screenshots and assertions are saved without credentials.
"""
import json
import io
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from gmp.api.auth import password_hash


def main():
    output = ROOT / "docs/evidence/browser"
    output.mkdir(parents=True, exist_ok=True)
    skip_webgl = os.environ.get("GMP_BROWSER_SKIP_WEBGL") == "1"
    report = {"viewports": [], "roles": [], "javascript_errors": [],
              "map_check": "skipped explicitly: DOM-only run" if skip_webgl else "required"}
    with tempfile.TemporaryDirectory(prefix="geoscan-browser-") as temporary:
        directory = Path(temporary)
        password, code = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        digest = password_hash(password)
        auth_file = directory / "users.json"
        auth_file.write_text(json.dumps({"users": {r: {"role": r, "password_hash": digest} for r in ("viewer", "planner", "admin")}}))
        auth_file.chmod(0o600)
        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            port = bound.getsockname()[1]
        env = {k:v for k,v in os.environ.items() if not k.startswith("GMP_")}
        env.update(GMP_AUTH_FILE=str(auth_file), GMP_ACCESS_CODE=code, GMP_ALLOW_LOCAL_HTTP="1",
                   GMP_DATA_DIR=str(directory / "state"), GMP_OFFLINE="1",
                   PYTHONPATH=os.pathsep.join(str(ROOT / p) for p in ("src", "h1/h1_coverage/src", "h2/src")))
        url = f"http://127.0.0.1:{port}"
        with (directory / "server.log").open("w") as log:
            server = subprocess.Popen([sys.executable, "-m", "uvicorn", "gmp.api.app:app", "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                for _ in range(120):
                    if server.poll() is not None:
                        raise RuntimeError("Browser test service exited")
                    try:
                        if httpx.get(url + "/health/ready", timeout=1).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.25)
                else:
                    raise TimeoutError("Browser service readiness")
                with sync_playwright() as playwright:
                    options = {"headless": True, "args": ["--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]}
                    if os.environ.get("CHROMIUM_EXECUTABLE"):
                        options["executable_path"] = os.environ["CHROMIUM_EXECUTABLE"]
                    browser = playwright.chromium.launch(**options)
                    for role in ("admin", "planner", "viewer", "legacy"):
                        context = browser.new_context(viewport={"width": 1440, "height": 1000})
                        page = context.new_page()
                        page.on("pageerror", lambda error: report["javascript_errors"].append(str(error)))
                        page.goto(url)
                        page.locator("#username-field").wait_for(state="visible")
                        if role != "legacy":
                            page.locator("#username").fill(role)
                        page.locator("#access-code").fill(code if role == "legacy" else password)
                        page.locator("#login-form button[type=submit]").click()
                        page.wait_for_function("state.csrf && document.getElementById('login-screen').hidden")
                        assert page.evaluate("state.role") == ("admin" if role == "legacy" else role)
                        if skip_webgl:
                            page.evaluate("state.map?.remove(); state.map=null; state.mapReady=false")
                        if role == "admin":
                            page.locator("#catalog-open").click()
                            page.locator("#open-templates").click()
                            page.locator(".scenario-item").filter(has_text="S00").click()
                            page.wait_for_function("state.scene?.scenario_id === 'S00_smoke_rgb'")
                            page.wait_for_timeout(1000)
                            if not skip_webgl:
                                assert page.locator("canvas").count() > 0
                                # Capture the viewport rectangle directly: locator screenshots wait for
                                # layout stability and can stall on continuously rendered WebGL.
                                page.evaluate("state.map?.stop()")
                                bounds = page.locator("#map").bounding_box()
                                assert bounds and bounds["width"] > 0 and bounds["height"] > 0
                                pixels = Image.open(io.BytesIO(page.screenshot(clip=bounds, animations="disabled"))).convert("RGB").resize((64, 64))
                                report["map_colors"] = len(pixels.getcolors(4096))
                                assert report["map_colors"] > 32, "Map is blank"
                            page.screenshot(path=str(output / "workspace-desktop.png"))
                        page.goto(url + "/documentation")
                        page.locator("#overview").wait_for(state="visible")
                        for width, height in ((1440, 1000), (390, 844)):
                            page.set_viewport_size({"width": width, "height": height})
                            for tab in ("overview", "algorithms", "testing"):
                                page.locator(f"[data-view='{tab}']").click()
                                page.locator(f"#{tab}").wait_for(state="visible")
                                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (role, width, tab)
                                if tab == "algorithms":
                                    expected = json.loads((ROOT / "algorithms/catalog.json").read_text())["algorithms"]
                                    assert page.locator("#algorithm-rows tr").count() == len(expected)
                                if role == "admin":
                                    page.screenshot(path=str(output / f"{tab}-{width}.png"), full_page=True)
                            if role == "admin":
                                report["viewports"].append({"width": width, "height": height, "horizontal_overflow": False})
                        response = context.request.get(url + "/api/v1/documentation/file?path=../../etc/passwd")
                        assert response.status == 404
                        report["roles"].append(role)
                        context.close()
                    browser.close()
                assert not report["javascript_errors"], report["javascript_errors"]
                report["passed"] = True
            finally:
                server.terminate()
                try:
                    server.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
                (output / "summary.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
