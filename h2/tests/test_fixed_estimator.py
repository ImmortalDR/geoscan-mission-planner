import json
from copy import deepcopy

import numpy as np
import pytest
pytest.importorskip('scipy')

from h2.fixed_estimator import FixedPlanEstimator
from h2.estimate_grid import EstimateConfig, Elevations, motion
from h2.scheduler import Settings, Scheduler
from h2.scenarios import make_bundle
from h2.transit import TransitContext
from test_h2_route_variants import variant_bundle


def fixed(bundle):
    return {'sorties':[dict(uav_id=bundle['fleet'][0]['id'],start_site_id='base',landing_site_id='base',
                           task_ids=[t['id'] for t in bundle['tasks']])]}


def test_detailed_survey_matches_existing_scheduler_with_hill():
    b=variant_bundle()
    scene=dict(schema_version='h2.scene.v1',crs=b['crs'],terrain=dict(origin=[499000,5999000],cell_size_m=500,
        values=[[0,0,0,0,0],[0,30,90,30,0],[0,80,170,80,0],[0,20,70,20,0],[0,0,0,0,0]]))
    s=Settings(sample_step_m=20)
    e=FixedPlanEstimator(b,scene,s)
    route=Scheduler(b,s,scene).route('u0',('t0',),'base','base')
    actual=e.task('u0','t0',route['task_variants'][0],route['task_reversed'][0])['points']
    p=[p for p in route['waypoints'] if p['phase']=='survey']
    expected=np.array([[v['x'],v['y'],v['z_m'],v['t_s']-p[0]['t_s'],v['agl_m']] for v in p])
    np.testing.assert_allclose(actual,expected,rtol=1e-10,atol=1e-7)
    assert e.task('u0','t0')['required_start_s']>actual[-1,3]


def test_fixed_plan_materializes_and_preserves_inputs():
    b=variant_bundle();before=deepcopy(b);plan=fixed(b);plan['sorties'][0].update(task_variants=['alternate'],task_reversed=[True])
    e=FixedPlanEstimator(b,settings=Settings())
    estimate=e.evaluate(plan)
    assert estimate['status']=='ESTIMATED_FEASIBLE',estimate
    result=e.materialize(plan)
    assert result['checks']['passed'],result['checks']
    assert result['estimate_overruns']==[]
    assert result['sorties'][0]['task_variants']==['alternate']
    assert result['metrics']['makespan_s']==pytest.approx(estimate['makespan_s'])
    assert b==before
    json.dumps(estimate,allow_nan=False)


def test_profile_grouping_uses_parameters_not_only_model_name():
    b=make_bundle(1,2);b['fleet'][1]=dict(b['fleet'][0],id='u1')
    e=FixedPlanEstimator(b)
    assert e.by_uav['u0'] is e.by_uav['u1']
    b['fleet'][1]['ground_speed_ms']+=1
    e=FixedPlanEstimator(b)
    assert e.by_uav['u0'] is not e.by_uav['u1']


@pytest.mark.parametrize('change,code',[
    (lambda p:p['sorties'][0].update(task_ids=[]),'missing_task'),
    (lambda p:p['sorties'][0].update(task_ids=['t0','t0']),'duplicate_task'),
    (lambda p:p['sorties'][0].update(start_s=-1),'invalid_departure'),
    (lambda p:p['sorties'][0].update(task_reversed=['yes']),'task_variant_fields'),
    (lambda p:p['sorties'][0].update(task_variants=['invented']),'invalid_task_variant'),
])
def test_rejects_bad_fixed_plans(change,code):
    b=make_bundle(1,1);e=FixedPlanEstimator(b);p=fixed(b);change(p);r=e.evaluate(p)
    assert r['status']=='ESTIMATE_REJECTED'
    assert code in {v['code'] for v in r['violations']}
    json.dumps(r,allow_nan=False)


def test_obstacle_graph_and_all_its_segments_avoid_obstacle():
    b=make_bundle(1,1)
    scene=dict(schema_version='h2.scene.v1',crs=b['crs'],allowed={'type':'Polygon','coordinates':[
        [[499500,5999500],[501000,5999500],[501000,6000500],[499500,6000500],[499500,5999500]]]},
        forbidden=[{'type':'Polygon','coordinates':[[[500030,5999950],[500070,5999950],[500070,6000050],[500030,6000050],[500030,5999950]]]}])
    e=FixedPlanEstimator(b,scene,config=EstimateConfig(grid_step_m=50))
    task=e.task('u0','t0');leg=e.leg('u0',(500000.,6000000.,60.),task['entry'])
    assert len(leg['coords'])>3
    assert TransitContext(scene,b['crs']['metric_epsg']).geometry_clear(leg['coords'])
    result=e.materialize(fixed(b));assert result['checks']['passed'],result['checks']


def test_more_margin_never_makes_resource_test_easier():
    b=make_bundle(1,1,endurance_min=3)
    e=FixedPlanEstimator(b,config=EstimateConfig(return_margin_s=60))
    r=e.evaluate(fixed(b));assert r['status']=='ESTIMATE_REJECTED'
    assert any(v['code']=='resource_or_return_reserve' for v in r['violations'])


def test_disconnected_return_is_not_claimed_reachable():
    b=make_bundle(1,1)
    from shapely.geometry import box,mapping,MultiPolygon
    scene=dict(schema_version='h2.scene.v1',crs=b['crs'],allowed=mapping(MultiPolygon([
        box(499800,5999800,500020,6000200),box(500080,5999800,500700,6000200)])))
    e=FixedPlanEstimator(b,scene,config=EstimateConfig(grid_step_m=50))
    r=e.evaluate(fixed(b));assert r['status']=='ESTIMATE_REJECTED'
    assert not e.task('u0','t0')['valid']
    json.dumps(r,allow_nan=False)


def test_dem_vectorization_matches_scalar_and_rejects_nodata():
    b=make_bundle(1,1)
    scene=dict(schema_version='h2.scene.v1',crs=b['crs'],terrain=dict(origin=[499000,5999000],cell_size_m=1000,values=[[0,20],[40,100]]))
    context=TransitContext(scene,32637);v=Elevations(context)
    xy=np.array([[499000,5999000],[499500,5999800],[500000,6000000]])
    np.testing.assert_allclose(v(xy),[context.elevation(*p) for p in xy])
    with pytest.raises(ValueError):v([[0,0]])


def test_grid_limit_is_explicit():
    with pytest.raises(ValueError,match='grid has'):
        FixedPlanEstimator(make_bundle(1,1),config=EstimateConfig(grid_step_m=1,max_grid_nodes=10))


def test_zero_distance_transfer_has_no_fake_flight_and_preserves_height():
    b=make_bundle(1,1);e=FixedPlanEstimator(b)
    endpoint=e.task('u0','t0')['entry']
    leg=e.leg('u0',endpoint,endpoint)
    assert leg['time_s']==0
    assert leg['heights'].tolist()==[endpoint[2],endpoint[2]]
    assert leg['required_start_s']>0
    # A vertical up/down leg must charge both parts, even at identical XY.
    trajectory=motion([[500100,6000000]]*3,[60,90,70],0,e.settings,e.by_uav['u0']['grid'].elevation)
    assert trajectory[-1,3]==pytest.approx(50/e.settings.vertical_speed_ms)
    assert trajectory[-1,4]==70


def test_service_gap_and_task_time_window_are_checked():
    b=make_bundle(2,1)
    e=FixedPlanEstimator(b,settings=Settings(task_windows={'t0':(0,1)}))
    p=fixed(b);p['sorties'][0]['task_ids']=['t0']
    p['sorties'].append(dict(p['sorties'][0],task_ids=['t1'],start_s=0))
    r=e.evaluate(p)
    assert {'service_gap','task_window'}<={v['code'] for v in r['violations']}


def test_dem_bytes_are_in_fingerprint_and_nodata_blocks_motion(tmp_path):
    import rasterio
    from rasterio.transform import from_origin
    path=tmp_path/'dem.tif';b=make_bundle(1,1)
    def write(values):
        with rasterio.open(path,'w',driver='GTiff',height=30,width=30,count=1,dtype='float32',
                           crs='EPSG:32637',transform=from_origin(499000,6002000,100,100),nodata=-9999) as dst:
            dst.write(values.astype('float32'),1)
    values=np.zeros((30,30));write(values)
    scene=dict(schema_version='h2.scene.v1',crs=b['crs'],terrain={'kind':'raster','path':str(path)})
    first=FixedPlanEstimator(b,scene)
    values[0,0]=1;write(values)
    second=FixedPlanEstimator(b,scene)
    assert first.fingerprint!=second.fingerprint
    values[20,11]=-9999;write(values)
    third=FixedPlanEstimator(b,scene)
    assert not third.task('u0','t0')['valid']


def test_h3_detects_conflict_and_accepts_explicit_sequential_schedule(tmp_path):
    from pathlib import Path
    from gmp.api.planner_adapter import build_h1
    from gmp.api.h2_adapter import build_scene_sidecar,export_sorties
    from gmp.safety.h3_gate import recompute_metrics,validate_result
    root=Path(__file__).resolve().parents[2]
    # Make simultaneous flights conflict independently of H1's chosen sweep angle.
    # Use an explicit large separation requirement; do not mutate the fixture.
    import shutil
    inputs=tmp_path/'input'
    shutil.copytree(root/'data/scenarios/S05_multi_uav/input',inputs)
    fleet_path=inputs/'fleet.json'
    fleet=json.loads(fleet_path.read_text())
    for uav in fleet['uavs']:
        uav.update(horizontal_separation_m=5000,vertical_separation_m=500)
    fleet_path.write_text(json.dumps(fleet))
    bundle=build_h1(inputs,root/'data','makespan')['h1_bundle']
    payloads=json.loads((inputs/'payload_catalog.json').read_text())['payload_profiles']
    settings=Settings(sample_step_m=20,survey_speed_factor=min(p['survey_speed_factor'] for p in payloads))
    e=FixedPlanEstimator(bundle,build_scene_sidecar(inputs,bundle),settings)
    plan={'sorties':[
        dict(uav_id='U01',start_site_id='WEST',landing_site_id='WEST',task_ids=['JOB_RGB#T000'],task_variants=['primary'],task_reversed=[False]),
        dict(uav_id='U02',start_site_id='EAST',landing_site_id='EAST',task_ids=['JOB_MS#T000'],task_variants=['alternate'],task_reversed=[True])
    ]}
    def validate():
        detailed=e.materialize(plan)
        result=dict(schema='geoscan.h3.result.v1',scene_id=bundle['scene_id'],objective='makespan',status='SAFE',
                    optimality={'status':'unknown'},sorties=export_sorties(bundle,detailed),metrics={})
        result['metrics']=recompute_metrics(inputs,result)['metrics']
        return detailed,validate_result(inputs,result)
    assert e.evaluate(plan)['status']=='ESTIMATED_FEASIBLE'
    detailed,report=validate()
    assert report['status']=='UNSAFE'
    assert any(v['code']=='AIRCRAFT_CONFLICT' for v in report['violations'])
    plan['sorties'][1]['start_s']=detailed['sorties'][0]['end_s']+1
    detailed,report=validate()
    assert detailed['checks']['passed'],detailed['checks']
    assert report['status']=='SAFE',report['violations']
    assert not detailed['estimate_overruns']


def test_wind_limit_and_temporal_survey_are_rejected():
    from shapely.geometry import box,mapping
    b=make_bundle(1,1)
    b['mission']['wind']['speed_ms']=b['fleet'][0]['max_wind_ms']+1
    scene=dict(schema_version='h2.scene.v1',crs=b['crs'],temporal=[dict(
        id='temporary',geometry=mapping(box(500200,5999990,500250,6000010)),
        start_s=0,end_s=100000,min_alt_m=0,max_alt_m=10000)])
    e=FixedPlanEstimator(b,scene)
    report=e.evaluate(fixed(b))
    assert {'wind_limit','temporal_restriction'}<={v['code'] for v in report['violations']}


def test_return_reserve_covers_slowed_materialized_transfer():
    b=variant_bundle();b['fleet'][0]['cruise_agl_m']=200
    scene=dict(schema_version='h2.scene.v1',crs=b['crs'],terrain=dict(origin=[499000,5999000],cell_size_m=500,
        values=[[0,0,0,0,0],[0,30,90,30,0],[0,80,170,80,0],[0,20,70,20,0],[0,0,0,0,0]]))
    e=FixedPlanEstimator(b,scene);tab=e.by_uav['u0'];u=tab['uav']
    task=e.task('u0','t0');base=(b['sites'][0]['x'],b['sites'][0]['y'],u['cruise_agl_m'])
    for start,end in [(base,task['entry']),(task['exit'],base)]:
        leg=e.leg('u0',start,end)
        distance=float(np.linalg.norm(np.diff(leg['coords'],axis=0),axis=1).sum())
        trajectory=motion(leg['coords'],leg['heights'],distance/u['ground_speed_ms'],e.settings,tab['grid'].elevation)
        assert trajectory[-1,3]<=leg['time_s']
        trajectory[:,3]*=leg['time_s']/trajectory[-1,3]
        assert tab['grid'].return_requirements(trajectory)<=leg['required_start_s']
