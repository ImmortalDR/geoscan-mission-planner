"""Deterministic S17: mixed fleet, concave zones and a mandatory charging stop."""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
from pyproj import Transformer
from shapely.geometry import Polygon, Point, box, mapping
from shapely.ops import transform

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src/gmp/scenario_templates/S18_MSU_geo401_alotofZones/input'
IDENTIFIER = 'S17_msu_refuel_relay'
TITLE = 'МГУ: дозарядка по пути к другой базе'


def generate(destination: Path):
    inp = destination / 'input'
    inp.mkdir(parents=True, exist_ok=True)
    def read(name): return json.loads((SOURCE / name).read_text())
    def write(name, data): (inp / name).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    converter = Transformer.from_crs('EPSG:32637', 'EPSG:4326', always_xy=True)
    # Avoid placing long transects exactly on raster-cell boundaries.
    origin = (407507.3, 6174007.7)
    def feature(geometry, **properties):
        shifted = transform(lambda x, y: (x+origin[0], y+origin[1]), geometry)
        return dict(type='Feature', properties=properties, geometry=mapping(transform(converter.transform, shifted)))
    def collection(features): return dict(type='FeatureCollection', features=features)
    west = Polygon([(-1700,-650),(-500,-650),(-500,-250),(-950,-250),(-950,250),(-1700,250)])
    east = Polygon([(500,-650),(1700,-650),(1700,250),(950,250),(950,-250),(500,-250)])
    north_west = Polygon([(-1950,950),(-1050,700),(-450,1050),(-450,1600),(-1250,1850),(-1950,1550)])
    north_east = Polygon([(450,800),(1700,800),(1950,1250),(1650,1750),(1000,1750),(1000,1350),(450,1350)])
    zones = [('Job_0',west,'lidar','LIDAR'),('Job_1',east,'lidar','LIDAR'),
             ('Job_2',north_west,'rgb','RGB'),('Job_3',north_east,'rgb','RGB')]
    assert all(geometry.is_valid for _,geometry,_,_ in zones)
    write('survey_areas.geojson', collection([feature(g,id=uid,survey_type=sensor,payload_profile=profile) for uid,g,sensor,profile in zones]))
    write('allowed_airspace.geojson', collection([feature(box(-3400,-2300,3400,2700),id='Airspace_0')]))
    write('landing_sites.geojson', collection([feature(Point(-2350,0),id='Base_0',role='both',candidate=False),
          feature(Point(0,0),id='Base_1',role='both',candidate=False),
          feature(Point(2350,0),id='Base_2',role='both',candidate=False)]))
    for name in ['no_fly_zones.geojson','obstacles.geojson','temporal_airspace.geojson']:
        write(name, collection([]))
    original = read('fleet.json')['uavs']
    rotor = deepcopy(next(u for u in original if u['model']=='geoscan_401'))
    plane = deepcopy(next(u for u in original if u['model']=='geoscan_201'))
    for i,u in enumerate([rotor,plane]):
        u.update(id=f'UAV_{i}',start_site='Base_0',landing_site='Base_2',refuel_sites=['Base_1'])
    rotor['payload_classes']=['lidar']
    plane['payload_classes']=['rgb']
    write('fleet.json',dict(uavs=[rotor,plane]))
    profiles=read('payload_catalog.json')['payload_profiles']
    rgb=deepcopy(next(p for p in profiles if p['type']=='rgb'))
    rgb.update(nominal_agl_m=246.15384615384616,planning_gsd_cm=6.,gsd_cm=7.2)
    lidar=deepcopy(next(p for p in profiles if p['type']=='lidar'))
    write('payload_catalog.json',dict(payload_profiles=[rgb,lidar]))
    mission=read('mission.json')
    mission.update(allow_different_start_end=True)
    mission['mission_window']['end']='2026-06-15T14:30:00+03:00'
    write('mission.json',mission)
    metadata=read('metadata.json')
    metadata.update(scenario_id=IDENTIFIER,description=TITLE,seed=20260918,
                    reference_generation={'angle_step_deg':30},
                    template_design={'theme':'intermediate_charging_with_distinct_endpoints',
                                     'coordinates':'synthetic mission geometry on real Copernicus terrain',
                                     'start':'Base_0','refuel_only':'Base_1','finish':'Base_2'})
    write('metadata.json',metadata)
    shutil.copy2(SOURCE/'dem.tif',inp/'dem.tif')
    spec=dict(id=IDENTIFIER,title=TITLE,objectives=['makespan','total_flight'],
              expected_status='SAFE',role='refuel_relay',fleet_count=2,real_elevation=True,
              purpose_ru='Геоскан 201 снимает RGB, Геоскан 401 — LiDAR. Четыре сложные области, старт на западной базе, дозарядка только на центральной, финиш на восточной.',
              catalog_badge_ru='2 БВС · промежуточная дозарядка',
              checks=dict(minimum_used_uavs=2,minimum_sorties=3,different_start_end=True,required_refuel_site='Base_1'))
    (destination/'scenario.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2)+'\n')
    print(destination)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('destination',nargs='?',type=Path,default=ROOT/'src/gmp/scenario_templates'/IDENTIFIER)
    generate(parser.parse_args().destination)
