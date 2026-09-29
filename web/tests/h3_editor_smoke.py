"""Browser regression for map/list selection, site permissions and a real SAFE run."""
from pathlib import Path
import json,time,uuid
from playwright.sync_api import sync_playwright
import argparse,os
parser=argparse.ArgumentParser()
parser.add_argument('--url',required=True)
parser.add_argument('--username',default='planner')
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--ignore-https-errors',action='store_true')
args=parser.parse_args()
out=args.output;out.mkdir(parents=True,exist_ok=True)
report={'checks':[]};errors=[]
with sync_playwright() as pw:
 b=pw.chromium.launch(args=['--no-sandbox','--disable-dev-shm-usage','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
 p=b.new_page(viewport={'width':1440,'height':1000},ignore_https_errors=args.ignore_https_errors);p.set_default_timeout(45000)
 p.on('pageerror',lambda e:errors.append(str(e)));p.on('dialog',lambda d:d.accept())
 def done(label): report['checks'].append(label);print(label,flush=True)
 try:
  p.goto(args.url,wait_until='domcontentloaded');p.locator('#username').fill(args.username);p.locator('#access-code').fill(os.environ['GMP_ACCESS_CODE']);p.locator('#login-form button[type=submit]').click()
  p.wait_for_function('state.csrf && state.scene && !state.sceneLoading && !state.busy')
  p.locator('#catalog-open').click();p.locator('#open-templates').click();p.locator('[data-template-id="S00_smoke_rgb"]').click()
  p.wait_for_function('state.scene?.scenario_id==="S00_smoke_rgb" && state.mapReady && !state.sceneLoading && !state.busy')
  p.get_by_role('button',name='Job_0',exact=True).click()
  assert p.locator('.object-entry.active').count()==1
  assert p.evaluate('state.draw.getSelectedIds()[0] === state.selected')
  # Click an actual base marker on the canvas, then check its sidebar row.
  p.evaluate('state.map.jumpTo({center:state.draw.getAll().features.find(f=>f.properties.layer_key==="landing_sites").geometry.coordinates})')
  p.wait_for_timeout(700)
  pt=p.evaluate('''()=>{const f=state.draw.getAll().features.find(f=>f.properties.layer_key==='landing_sites');const q=state.map.project(f.geometry.coordinates);return {x:q.x,y:q.y,id:f.id}}''')
  bounds=p.locator('#map').bounding_box();p.mouse.click(bounds['x']+pt['x'],bounds['y']+pt['y'])
  p.wait_for_function('id=>state.selected===id',arg=pt['id'])
  assert 'Base_0' in p.locator('.object-entry.active').inner_text()
  done('real map click and sidebar click select the matching object')
  p.get_by_role('button',name='Job_0',exact=True).click();p.get_by_role('button',name='Дублировать область',exact=True).click()
  assert p.get_by_role('button',name='Job_1',exact=True).count()==1
  p.get_by_role('button',name='Удалить Job_1',exact=True).click()
  assert p.get_by_role('button',name='Job_1',exact=True).count()==0
  p.locator('#undo-edit').click()
  assert p.get_by_role('button',name='Job_1',exact=True).count()==1
  p.locator('#undo-edit').click()
  assert not p.evaluate('state.dirty')
  done('ordinal duplicate names, per-zone delete and undo work')
  p.locator('[data-sidebar=fleet]').click()
  assert not p.locator('.fleet-item').first.evaluate('e=>e.open')
  p.get_by_label('Свойства UAV_0',exact=True).click()
  assert p.locator('.fleet-properties-title').first.is_visible()
  assert p.locator('.fleet-item .when-open').first.is_visible()
  assert p.locator('.base-table').count()==1
  assert 'По исходным' not in p.locator('#sidebar-content').inner_text()
  p.get_by_label('Дозарядка: Любая',exact=True).uncheck()
  assert p.evaluate('state.draft.fleet.uavs[0].refuel_sites')==[]
  assert p.locator('.fleet-item').first.evaluate('e=>e.open')
  p.get_by_label('Дозарядка: Base_0',exact=True).check()
  site=p.evaluate('state.draft.layers.landing_sites.features[0].properties.id')
  assert p.evaluate('state.draft.fleet.uavs[0].refuel_sites')==[site]
  p.get_by_label('Старт: Любая',exact=True).check()
  assert p.evaluate('state.draft.fleet.uavs[0].start_site') is None
  p.get_by_label('Старт: Base_0',exact=True).check()
  p.get_by_label('Финиш: Base_0',exact=True).check()
  assert p.evaluate('state.draft.fleet.uavs[0].start_site')==site
  assert p.evaluate('state.draft.fleet.uavs[0].landing_site')==site
  p.get_by_label('Название БВС',exact=True).fill('UAV_0');p.get_by_label('Название БВС',exact=True).press('Tab')
  done('table materializes old permissions, preserves expansion, handles any/fixed/none')
  p.screenshot(path=str(out/'fleet-desktop.png'),animations='disabled')
  p.locator('#save-as-scene').click();name='Проверка таблицы баз '+uuid.uuid4().hex[:6];p.locator('#save-as-name').fill(name);p.locator('#save-as-form button[type=submit]').click()
  p.wait_for_function('name=>state.scene.name===name && state.scene.saved && !state.busy',arg=name)
  assert p.evaluate('state.scene.validation.valid'),p.evaluate('state.scene.validation')
  report['scene_id']=p.evaluate('state.scene.id')
  p.locator('[data-sidebar=run]').click();start=time.monotonic();p.locator('#run-live').click()
  p.wait_for_function('state.plan && TERMINAL.has(state.plan.status.toUpperCase())',timeout=180000)
  report['run']=p.evaluate('({status:state.plan.status,metrics:state.plan.metrics,error:state.plan.error})');report['run']['elapsed_s']=round(time.monotonic()-start,2)
  assert report['run']['status']=='SAFE',report['run']
  done('saved table choices and display name pass actual H1/H2/H3: SAFE')
  p.locator('[data-sidebar=fleet]').click();p.get_by_label('Свойства UAV_0',exact=True).click()
  p.wait_for_function('state.selectedUav!==null')
  assert p.evaluate('Array.isArray(state.map.getPaintProperty("routes","line-width"))')
  # Multi-base template covers disabled role/candidate cells and a subset of refuel bases.
  p.locator('#catalog-open').click();p.locator('#open-templates').click();p.locator('[data-template-id="S10_start_end_sites"]').count()
  choice=p.evaluate('state.scenarios.find(s=>s.id.startsWith("S10")).id')
  p.locator(f'[data-template-id="{choice}"]').click();p.wait_for_function('id=>state.scene.scenario_id===id && !state.busy && !state.sceneLoading',arg=choice)
  p.locator('[data-sidebar=fleet]').click();p.get_by_label('Свойства UAV_0',exact=True).click()
  config=p.evaluate('({uav:state.draft.fleet.uavs[0],sites:siteProperties()})')
  report['multi_base_initial']=config
  for column in ['start_site','refuel_sites','landing_site']:
   controls=p.locator('.fleet-item').first.locator(f'input[data-base-column="{column}"]')
   for i in range(controls.count()):
    control=controls.nth(i)
    if control.is_enabled():control.check()
   assert p.evaluate('''()=>{let u=state.draft.fleet.uavs[0];return ['start_site','landing_site'].every(k=>u[k]===null||SceneEditor.eligible(siteProperties(),k).includes(u[k])) && (u.refuel_sites===null||u.refuel_sites.every(id=>SceneEditor.eligible(siteProperties(),'refuel_sites').includes(id)))}''')
  done('multi-base table only allows role-compatible active bases')
  p.set_viewport_size({'width':390,'height':844});p.locator('#mobile-menu').click()
  assert p.locator('#sidebar').is_visible()
  assert p.evaluate('document.documentElement.scrollWidth<=innerWidth')
  p.screenshot(path=str(out/'fleet-mobile.png'),animations='disabled')
  assert not errors,errors
  report['passed']=True
 except Exception as e:
  report['failure']=str(e);report['state']=p.evaluate('({scene:state.scene?.name,errors:document.getElementById("toast-container").innerText})')
  p.screenshot(path=str(out/'failure.png'));raise
 finally:
  report['javascript_errors']=errors;(out/'browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));b.close()
print('All editor browser checks passed',flush=True)
