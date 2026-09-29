"""Development-server smoke: save/save-as, undo/redo, H1 logs and cancellation.

Requires Playwright + Chromium, GMP_ACCESS_CODE, optional GMP_SMOKE_URL.
Only scenarios created by this script are deleted at the end.
"""
import json
import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

base_url = os.environ.get('GMP_SMOKE_URL', 'https://127.0.0.1:8788/')
artifacts = Path(os.environ.get('GMP_SMOKE_ARTIFACTS', '/tmp/geoscan-scenario-smoke'))
artifacts.mkdir(parents=True, exist_ok=True)
name = f'Browser smoke {time.time_ns()}'

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
    context = browser.new_context(ignore_https_errors=True, viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    errors, created = [], set()
    page.on('pageerror', lambda error: errors.append(str(error)))

    def capture(response):
        if '/api/v1/scenes' in response.url and response.request.method == 'POST' and response.status == 200:
            result = response.json()
            if 'id' in result:
                created.add(result['id'])

    page.on('response', capture)

    def save_as(title):
        page.locator('#save-as-scene').click()
        page.locator('#save-as-name').fill(title)
        page.locator('#save-as-form button[type=submit]').click()
        page.wait_for_function('(name) => !state.busy && state.scene?.saved && state.scene.name === name', arg=title)
        page.wait_for_function('!document.getElementById("save-as-dialog").open')

    def edit_field(label, value):
        field = page.get_by_label(label, exact=True)
        field.fill(value)
        field.press('Tab')

    def undo():
        page.keyboard.press('Control+z')

    def redo():
        page.keyboard.press('Control+y')

    try:
        page.goto(base_url, wait_until='domcontentloaded')
        page.locator('#access-code').fill(os.environ['GMP_ACCESS_CODE'])
        page.locator('#login-form button[type=submit]').click()
        page.wait_for_function('state.csrf && state.scenarios.length >= 18')
        page.locator('#catalog-open').click()
        page.locator('[data-template-folder="simple"]').click()
        assert page.locator('#catalog-list .scenario-item').count() == page.evaluate('state.scenarios.filter(s => !s.real_elevation).length')
        page.locator('#catalog-list .scenario-item').filter(has_text='S00').click()
        page.wait_for_function("state.mapReady && state.scene?.scenario_id === 'S00_smoke_rgb'")
        page.evaluate("() => { window.loadingProbe = withScenarioLoading(() => new Promise(resolve => { window.releaseLoadingProbe = resolve; })); }")
        page.wait_for_function("state.sceneLoading")
        assert page.locator("#map-loading").is_visible()
        assert page.locator("#map-area").get_attribute("aria-busy") == "true"
        assert page.locator("#map").evaluate("node => node.inert")
        assert page.locator("#scenario-select").is_disabled()
        page.evaluate("window.releaseLoadingProbe()")
        page.wait_for_function("!state.sceneLoading")
        assert page.locator("#map-loading").is_hidden()
        assert not page.locator("#map").evaluate("node => node.inert")
        assert page.locator('#save-scene').is_disabled()
        assert page.locator('#run-live').is_disabled()
        assert 'Вначале сохраните сценарий' in page.locator('#run-live').inner_text()
        previous_plan = page.evaluate('state.plan')
        page.evaluate("startPlan('h1')")
        assert page.evaluate('state.plan') == previous_plan
        assert not page.evaluate('state.scene.saved')
        assert page.locator('#rename-scene').is_disabled()
        assert page.locator('#delete-scene').is_disabled()
        assert page.locator('#sidebar-content').get_by_role('button', name='Удалить сценарий').count() == 0
        previous_wind = page.evaluate('state.draft.mission.wind.speed_ms || 0')
        edit_field('Ветер, м/с', str(previous_wind + 1))
        undo()
        assert page.evaluate('state.draft.mission.wind.speed_ms || 0') == previous_wind
        redo()
        assert page.evaluate('state.draft.mission.wind.speed_ms') == previous_wind + 1
        undo()
        print('Template name protection and mission undo/redo passed', flush=True)

        fleet_before_sensor = page.evaluate('state.draft.fleet')
        assert not any('thermal' in uav['payload_classes'] for uav in fleet_before_sensor['uavs'])
        area_id = page.evaluate('state.draft.layers.survey_areas.features[0].properties.id')
        assert page.locator('.survey-sensor-row select').count() == page.evaluate('state.draft.layers.survey_areas.features.length')
        page.get_by_label('Датчик: ' + area_id, exact=True).select_option('thermal')
        assert page.evaluate('state.draft.fleet') == fleet_before_sensor
        undo()
        assert page.evaluate("state.draft.layers.survey_areas.features[0].properties.survey_type") == 'rgb'
        redo()
        assert page.evaluate("state.draft.layers.survey_areas.features[0].properties.survey_type") == 'thermal'
        undo()
        # Draw commits and delete use the same event handlers as interactive drawing.
        count = page.evaluate('state.draft.layers.survey_areas.features.length')
        page.evaluate("""() => {
          const f=structuredClone(state.draw.getAll().features.find(f=>f.properties.layer_key==='survey_areas'));
          delete f.id; f.properties.id='DRAW_SMOKE'; state.tool='survey_areas';
          const id=state.draw.add(f)[0]; state.map.fire('draw.create',{features:[state.draw.get(id)]});
        }""")
        assert page.evaluate('state.draft.layers.survey_areas.features.length') == count + 1
        undo()
        assert page.evaluate('state.draft.layers.survey_areas.features.length') == count
        redo()
        assert page.evaluate('state.draft.layers.survey_areas.features.length') == count + 1
        page.evaluate("selectFeature(state.draw.getAll().features.filter(f=>f.properties.layer_key==='survey_areas').at(-1).id)")
        page.keyboard.press('Delete')
        assert page.evaluate('state.draft.layers.survey_areas.features.length') == count
        undo()
        assert page.evaluate('state.draft.layers.survey_areas.features.length') == count + 1
        redo()
        assert page.evaluate('state.draft.layers.survey_areas.features.length') == count
        # Moving a polygon is undone with its exact original coordinates.
        geometry = page.evaluate('state.draft.layers.survey_areas.features[0].geometry')
        page.evaluate("""() => {
          const f=state.draw.getAll().features.find(f=>f.properties.layer_key==='survey_areas');
          const move=x=>typeof x[0]==='number'?[x[0]+.00001,x[1]]:x.map(move);
          f.geometry.coordinates=move(f.geometry.coordinates); state.draw.add(f);
          state.map.fire('draw.update',{features:[f],action:'move'});
        }""")
        assert page.evaluate('state.draft.layers.survey_areas.features[0].geometry') != geometry
        undo()
        assert page.evaluate('state.draft.layers.survey_areas.features[0].geometry') == geometry
        print('Sensor, geometry and deletion undo/redo passed', flush=True)

        page.locator('[data-sidebar=fleet]').click()
        fleet_count = page.evaluate('state.draft.fleet.uavs.length')
        source_id = page.evaluate('state.draft.fleet.uavs[0].id')
        source = page.evaluate('state.draft.fleet.uavs[0]')
        page.get_by_role('button', name='Копировать БВС ' + source_id, exact=True).click()
        page.get_by_role('button', name='Копировать БВС ' + source_id, exact=True).click()
        copies = page.evaluate('state.draft.fleet.uavs')
        assert {source_id + '_copy1', source_id + '_copy2'} <= {uav['id'] for uav in copies}
        for uav in copies:
            if uav['id'].startswith(source_id + '_copy'):
                assert {**uav, 'id': source_id} == source
        assert page.evaluate('state.draft.fleet.uavs[0].payload_classes !== state.draft.fleet.uavs[1].payload_classes')
        page.get_by_role('button', name='Удалить БВС ' + source_id + '_copy2', exact=True).click()
        undo()
        assert source_id + '_copy2' in page.evaluate('state.draft.fleet.uavs.map(u=>u.id)')
        redo()
        assert source_id + '_copy2' not in page.evaluate('state.draft.fleet.uavs.map(u=>u.id)')
        page.get_by_role('button', name='Удалить БВС ' + source_id + '_copy1', exact=True).click()
        assert page.evaluate('state.draft.fleet.uavs[0]') == source
        print('Fleet copies, unique names and deletion undo/redo passed', flush=True)
        start_select = page.get_by_label('Старт', exact=True).first
        landing_select = page.get_by_label('Посадка', exact=True).first
        assert 'Любая площадка взлёта' in start_select.locator('option').all_text_contents()
        assert 'Любая площадка посадки' in landing_select.locator('option').all_text_contents()
        start_select.select_option('')
        landing_select.select_option('')
        assert page.evaluate('state.draft.fleet.uavs[0].start_site === null && state.draft.fleet.uavs[0].landing_site === null')
        print('Unpinned launch and landing choices passed', flush=True)
        page.get_by_role('button', name='Добавить', exact=True).click()
        assert page.evaluate('state.draft.fleet.uavs.length') == fleet_count + 1
        undo()
        assert page.evaluate('state.draft.fleet.uavs.length') == fleet_count
        redo()
        assert page.evaluate('state.draft.fleet.uavs.length') == fleet_count + 1
        undo()
        save_as(name)
        first_id = page.evaluate('state.scene.id')
        library = page.evaluate('state.scene.library_id')
        assert not page.evaluate('state.scene.template_readonly')
        assert page.evaluate('state.scene.last_run') is None
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('(id)=>state.mapReady && state.scene?.id===id', arg=first_id)
        assert page.evaluate('state.scene.saved')
        assert page.locator("#delete-scene").is_enabled()
        assert page.locator('#run-live').is_enabled()
        assert page.locator('#run-live-label').inner_text() == 'Запустить расчёт'
        print('Save As and reload passed', flush=True)

        page.locator('[data-sidebar=run]').click()
        page.locator('#run-live').click()
        page.wait_for_function("state.plan && TERMINAL.has(state.plan.status.toUpperCase())", timeout=180000)
        run_id = page.evaluate('state.plan.id')
        code = page.evaluate('state.plan.run_code')
        assert page.evaluate('state.plan.mode') == 'live'
        assert page.evaluate('state.plan.status') == 'SAFE'
        assert page.evaluate("stageDone('h1') && stageDone('h2') && stageDone('h3')")
        assert code in page.locator('#result-heading').inner_text()
        page.locator('[data-result=h1]').click()
        assert page.locator('.h1-task-row').count() > 0
        page.locator('.h1-task-row').first.click()
        assert page.locator('.h1-task-row.selected').count() == 1
        assert page.evaluate('state.scene.last_run.run_code') == code
        bundle = context.request.get(base_url.rstrip('/') + f'/api/v1/plans/{run_id}/log/h1_h2.bundle.json').json()
        assert bundle == page.evaluate('state.plan.plan.h1_bundle')
        assert bundle["fleet"][0]["start_site"] is None and bundle["fleet"][0]["landing_site"] is None
        assert page.evaluate("sorties().every(s => Boolean(s.start_site) && Boolean(s.landing_site))")
        page.locator('[data-result=summary]').click()
        assert code in page.locator('#result-content').inner_text()
        assert page.locator('a[download]').filter(has_text='h1_h2.bundle.json').count() == 1
        page.locator('[data-result=timeline]').click()
        assert page.locator('.gantt-row').count() > 0
        assert page.evaluate("state.map.getSource('routes')._data.features.length") > 0
        page.locator('[data-result=h3]').click()
        assert 'Сертификат: выдан' in page.locator('#result-content').inner_text()
        page.locator('[data-result=export]').click()
        assert page.locator('.export-link:not(.disabled)').count() == 6
        mission = page.locator('.export-link').filter(has_text='Полный результат')
        response = context.request.get(base_url.rstrip('/') + mission.get_attribute('href'))
        assert response.ok
        print('Full H1-H2-H3 run, routes, validation and export passed', flush=True)

        page.locator('[data-sidebar=fleet]').click()
        previous_speed = page.evaluate('state.draft.fleet.uavs[0].ground_speed_kmh')
        edit_field('Скорость, км/ч', str(previous_speed + 1))
        assert page.locator('#run-live').is_disabled()
        assert 'Вначале сохраните сценарий' in page.locator('#run-live').inner_text()
        previous_plan = page.evaluate('state.plan')
        previous_history = page.evaluate('state.history.length')
        page.evaluate("startPlan('h1')")
        assert page.evaluate('state.plan') == previous_plan
        assert page.evaluate('state.history.length') == previous_history
        assert page.evaluate('state.dirty')
        assert page.evaluate('state.scene.id') == first_id
        page.locator('#save-scene').click()
        page.wait_for_function('(id)=>!state.busy && state.scene.saved && state.scene.id!==id', arg=first_id)
        assert page.evaluate('state.myScenarios.filter(s=>s.library_id===state.scene.library_id).length') == 1
        assert not page.evaluate('state.scene.last_run.matches_input')
        assert page.locator('#run-live').is_enabled()
        undo()
        assert page.evaluate('state.draft.fleet.uavs[0].ground_speed_kmh') == previous_speed
        assert page.evaluate('state.dirty')
        redo()
        assert not page.evaluate('state.dirty')
        save_as(name + ' second')
        assert page.evaluate('state.scene.library_id') != library
        assert page.evaluate('state.scene.last_run') is None
        assert page.evaluate('state.myScenarios.filter(s=>s.name.startsWith("Browser smoke")).length') >= 2
        print('Save updates one file, undo after save, Save As preserves source passed', flush=True)

        page.locator('[data-sidebar=run]').click()
        page.locator('#run-live').click()
        # A tiny H1 scene may finish before the browser can deliver Stop.
        # Forced termination itself is checked with a slow worker in API tests.
        page.evaluate('document.getElementById("stop-plan").click()')
        page.wait_for_function("['CANCELLED', 'COMPLETED'].includes(state.plan?.status)", timeout=10000)
        manifest = context.request.get(base_url.rstrip('/') + f"/api/v1/plans/{page.evaluate('state.plan.id')}/log/run.json").json()
        assert manifest['status'] == page.evaluate('state.plan.status')
        page.locator('#catalog-open').click()
        page.locator('#new-blank').click()
        page.wait_for_function('state.scene && !state.scene.scenario_id')
        assert page.evaluate('state.draft.fleet.uavs.length') == 0
        assert page.locator('#run-live').is_disabled()
        assert 'Вначале сохраните сценарий' in page.locator('#run-live').inner_text()
        save_as(name + ' blank')
        page.wait_for_function('state.scene.saved && !state.busy')
        page.set_viewport_size({'width': 390, 'height': 844})
        page.locator('#mobile-menu').click()
        page.locator('[data-sidebar=run]').click()
        assert page.locator('#stop-plan').is_visible()
        assert page.locator('#run-live').is_visible()
        page.once("dialog", lambda dialog: dialog.accept())
        page.evaluate("document.getElementById(\"delete-scene\").click()")
        page.wait_for_function("!state.scene")
        assert page.locator("#delete-scene").is_disabled()
        assert not errors, errors
        print(json.dumps({'save_save_as': 'passed', 'undo_redo': 'passed', 'run_logs': 'passed', 'cancel': 'passed', 'browser_errors': errors}))
    except Exception:
        import traceback
        traceback.print_exc()
        print('Browser errors:', errors, flush=True)
        try:
            page.screenshot(path=str(artifacts / 'scenario-error.png'), timeout=5000)
        except Exception:
            pass
        raise
    finally:
        page.evaluate("""async ids => {
          if (state.plan && !TERMINAL.has(state.plan.status.toUpperCase())) await api(`/plans/${state.plan.id}/cancel`,{method:'POST'});
          for (const id of ids) { try { await api(`/scenes/${id}`,{method:'DELETE'}); } catch {} }
        }""", list(created))
        browser.close()
