"""Exercise document state, real calculation and terrain prompts on an H3 server.

GMP_ACCESS_CODE is required; this leaves a named regression copy in My scenarios.
"""
import argparse
import io
import json
import os
import time
import zipfile
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--username', default='planner')
    parser.add_argument('--ignore-https-errors', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'url': args.url, 'checks': [], 'runs': []}
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=['--no-sandbox', '--disable-dev-shm-usage', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, ignore_https_errors=args.ignore_https_errors)
        page.set_default_timeout(60000)
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('dialog', lambda dialog: dialog.accept())
        def check(label):
            report['checks'].append(label)
            print(label, flush=True)
        def idle():
            page.wait_for_function('!state.busy && !state.sceneLoading')
        def open_template(scenario="S00_smoke_rgb"):
            page.locator('#catalog-open').click()
            page.locator('#open-templates').click()
            old = page.evaluate('state.scene?.id')
            page.locator(f'[data-template-id="{scenario}"]').click()
            page.wait_for_function('data => state.scene?.scenario_id === data.scenario && state.scene.id !== data.old && !state.sceneLoading && !state.busy', arg={"old": old, "scenario": scenario})
        def save_as(name):
            page.locator('#save-as-scene').click()
            page.locator('#save-as-name').fill(name)
            page.locator('#save-as-form button[type=submit]').click()
            page.wait_for_function('name => state.scene.saved && state.scene.name === name && !state.dirty && !state.busy', arg=name)
        def run():
            before = page.evaluate('state.scene')
            page.locator('[data-sidebar=run]').click()
            assert page.locator('#run-live').is_enabled()
            start = time.monotonic()
            page.locator('#run-live').click()
            page.wait_for_function('state.plan && TERMINAL.has(state.plan.status.toUpperCase())', timeout=240000)
            result = page.evaluate('({status:state.plan.status, metrics:state.plan.metrics, hash:state.plan.plan?.provenance?.routing?.solution_sha256, error:state.plan.error})')
            result['elapsed_s'] = round(time.monotonic() - start, 2)
            report['runs'].append(result)
            assert result['status'] == 'SAFE', result
            assert before == page.evaluate('state.scene')
            response = page.request.get(args.url.rstrip('/') + '/api/v1/scenes/' + before['id'])
            assert response.ok and response.json() == before
            assert not page.evaluate('state.dirty')
            assert page.locator('#result-content .objective-value').is_visible()
            return result
        def upload(payload, name='scenario.zip'):
            old = page.evaluate('state.scene.id')
            page.locator('#catalog-open').click()
            page.locator('#import-open').click()
            page.locator('#import-files').set_input_files({'name': name, 'mimeType': 'application/zip', 'buffer': payload})
            assert not page.locator('#import-base').is_checked()
            page.locator('#import-form button[type=submit]').click()
            page.wait_for_function('old => state.scene.id !== old && !state.busy', arg=old)
        def edit_speed():
            page.locator('[data-sidebar=fleet]').click()
            page.locator('.fleet-item').first.evaluate('e => e.open = true')
            field = page.get_by_label('Скорость, км/ч').first
            value = float(field.input_value())
            field.fill(str(value - 1))
            field.press('Tab')
            page.wait_for_function('state.dirty')
        try:
            page.goto(args.url, wait_until='domcontentloaded')
            page.wait_for_function('!document.getElementById("login-form").querySelector("button").disabled')
            if page.locator('#username').is_visible():
                page.locator('#username').fill(args.username)
            page.locator('#access-code').fill(os.environ['GMP_ACCESS_CODE'])
            page.locator('#login-form button[type=submit]').click()
            page.wait_for_function('state.csrf && state.scenarios.length >= 18 && !state.busy')
            assert page.locator('.project-bar #catalog-open').count() == 1
            assert page.locator('.app-header #catalog-open, .app-header #import-open, #scenario-select, #import-dialog').count() == 0
            open_template()
            assert page.evaluate('state.scene.ready_to_run && state.scene.template_readonly && state.scene.terrain.available')
            assert not page.locator('#terrain-dialog').is_visible()
            assert page.locator('#save-scene').is_disabled()
            assert page.locator('#save-as-scene').is_enabled()
            check('one opener in second row; pristine template runnable; loaded terrain silent')
            original = page.evaluate('state.scene')
            archive_response = page.request.get(args.url.rstrip('/') + f'/api/v1/scenes/{original["id"]}/download')
            assert archive_response.ok
            archive = archive_response.body()
            first = run()
            check('template calculates SAFE directly; scenario unchanged by calculation')
            page.locator('[data-result=export]').click()
            exports = {}
            for link in page.locator('.export-link:not(.disabled)').all():
                href = link.get_attribute('href')
                response = page.request.get(args.url.rstrip('/') + href)
                exports[href.rsplit('=', 1)[-1]] = response.status
                assert response.ok
            assert {'certificate', 'geojson', 'kml', 'mission', 'pdf', 'docx'} <= exports.keys()
            report['exports'] = exports
            check('result exports available separately')
            open_template()
            assert page.evaluate('state.plan === null')
            assert page.locator('#result-dock').is_hidden()
            check('reopening template clears results')
            open_template('S18_MSU_geo401_alotofZones')
            page.locator('[data-sidebar=run]').click()
            before = page.evaluate('state.draft')
            page.locator('#objective').select_option('total_flight')
            assert page.evaluate('state.draft') == before and not page.evaluate('state.dirty')
            check('metric selection does not modify scenario')
            open_template()
            edit_speed()
            assert page.locator('#save-scene').is_disabled()
            assert page.locator('#run-live').is_disabled()
            assert page.locator('#save-as-scene').is_enabled()
            page.locator('#undo-edit').click()
            assert not page.evaluate('state.dirty')
            assert page.locator('#run-live').is_enabled()
            check('template edit requires Save As; undo restores runnable original')
            edit_speed()
            save_as('Проверка открытия и сохранения ' + uuid4().hex[:6])
            saved_id = page.evaluate('state.scene.id')
            report['saved_scene_id'] = saved_id
            assert not page.evaluate('state.scene.template_readonly')
            assert page.locator('#run-live').is_enabled()
            edit_speed()
            assert page.locator('#save-scene').is_enabled()
            assert page.locator('#run-live').is_disabled()
            page.locator('#save-scene').click()
            page.wait_for_function('old => state.scene.id !== old && state.scene.saved && !state.dirty && !state.busy', arg=saved_id)
            saved_id = page.evaluate('state.scene.id')
            report['saved_scene_id'] = saved_id
            check('own scenario supports Save after editing')
            run()
            page.locator('#catalog-open').click()
            page.locator('#open-saved').click()
            assert 'заверш' not in page.locator(f'[data-scene-id="{saved_id}"]').inner_text().lower()
            page.locator(f'[data-scene-id="{saved_id}"]').click()
            idle()
            assert page.evaluate('state.scene.id') == saved_id
            assert page.evaluate('state.plan === null')
            assert not page.locator('#terrain-dialog').is_visible()
            check('reopening saved scenario restores input only, without results or terrain notification')
            upload(archive)
            assert page.evaluate('state.scene.ready_to_run && !state.scene.template_readonly')
            assert page.evaluate('state.scene.terrain.available')
            assert not page.locator('#terrain-dialog').is_visible()
            uploaded = run()
            assert uploaded['hash'] == first['hash']
            assert uploaded['metrics'] == first['metrics']
            check('same template imported as ZIP gives identical SAFE solution and metrics')
            page.locator('[data-result=summary]').click()
            page.screenshot(path=str(args.output / 'desktop.png'), animations='disabled')
            for width, height in [(390, 844), (360, 740)]:
                page.set_viewport_size({'width': width, 'height': height})
                page.wait_for_timeout(500)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert page.locator('#catalog-open').is_visible()
                page.screenshot(path=str(args.output / f'mobile-{width}.png'), animations='disabled')
            check('desktop and mobile layout, no horizontal overflow')
            page.set_viewport_size({'width': 1440, 'height': 1000})
            no_dem = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(archive)) as source, zipfile.ZipFile(no_dem, 'w') as target:
                for item in source.infolist():
                    if Path(item.filename).name != 'dem.tif':
                        target.writestr(item, source.read(item))
            upload(no_dem.getvalue())
            assert page.locator('#terrain-dialog').is_visible()
            assert page.locator('#terrain-dialog-close').inner_text() == 'Позже'
            assert page.locator('#terrain-flat').is_enabled()
            assert page.locator('#terrain-download').is_enabled()
            assert page.locator('#terrain-show-map').is_hidden()
            page.locator('#terrain-dialog-close').click()
            assert page.evaluate('state.plan === null && !state.scene.terrain.available')
            check('missing terrain asks with Later / Copernicus / flat; Later starts nothing')
            # Reopen that input to check the flat choice through the same notice.
            page.evaluate('showTerrainNotice(state.scene)')
            old = page.evaluate('state.scene.id')
            page.locator('#terrain-flat').click()
            page.wait_for_function('old => state.scene.id !== old && !state.busy', arg=old)
            assert page.evaluate('state.scene.terrain.available && state.plan === null')
            assert not page.locator('#terrain-dialog').is_visible()
            assert page.locator('#run-live').is_disabled()
            page.locator('#save-scene').click()
            page.wait_for_function('state.scene.saved && !state.busy')
            assert page.locator('#run-live').is_enabled()
            check('flat choice prepares terrain only; explicit Save and Run remain separate')
            # A present file with incomplete coverage must not trigger the opening notice either.
            page.evaluate('showTerrainNotice({...state.scene, terrain:{...state.scene.terrain, covers_scene:false}})')
            assert not page.locator('#terrain-dialog').is_visible()
            check('present terrain never opens notice, even if coverage validation fails')
            assert not errors, errors
            report['passed'] = True
        except Exception as exc:
            report['failure'] = str(exc)
            report['state'] = page.evaluate('({scene:state.scene?.name,busy:state.busy,dirty:state.dirty,plan:state.plan?.status,toasts:document.getElementById("toast-container")?.innerText})')
            page.screenshot(path=str(args.output / 'failure.png'))
            raise
        finally:
            report['javascript_errors'] = errors
            (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            browser.close()
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
