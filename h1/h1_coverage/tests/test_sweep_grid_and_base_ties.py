"""Regression: rotation roundoff and sensor-aware base attraction at cost ties."""
from copy import deepcopy
from dataclasses import replace
import math

import pytest
from shapely.geometry import Point, box

from h1_coverage.config import CoverageConfig
from h1_coverage.coverage.candidates import SweepCandidate, endpoint_base_proximity, rank_candidates
from h1_coverage.coverage.complete import complete_parallel_sweep, physical_coverage
from h1_coverage.coverage.engine import build_coverage, weighted_start_bases
from h1_coverage.coverage.grid import centered_rows, fixed_spacing_rows, stable_ceil_ratio
from h1_coverage.coverage.sweep import generate_transects
from h1_coverage.feasibility import eligible_uavs_for_payload
from h1_coverage.geo import CrsPipeline
from h1_coverage.models import Mission, PayloadProfile, Scene, Site, SurveyJob, Transect, Uav, Wind


@pytest.mark.parametrize('angle',[0.,90.,180.,270.])
@pytest.mark.parametrize('noise',[-1e-8,0.,1e-8])
def test_integer_width_uses_stable_interior_rows_despite_rotation_roundoff(angle,noise):
    area=box(405254.0896585401,6173510.350460332,405574.0896585401+noise,6173830.350460332+noise)
    profile=PayloadProfile(id='p',type='lidar',nominal_agl_m=80,swath_spacing_m=80)
    sweep=generate_transects(area,80,angle,'job')
    complete=complete_parallel_sweep(area,80,angle,'job',area.buffer(1))
    assert len(sweep)==4  # stable interior phase; pitch remains exactly 80 m
    assert len(complete)==4  # fallback uses centred physical strips
    for lines in (sweep,complete):
        assert area.difference(physical_coverage(lines,profile)).area<1e-3
    if angle==0:
        assert sorted(t.start[1]-area.bounds[1] for t in sweep)==pytest.approx([40,120,200,280],abs=1e-7)
        assert sorted(t.start[1]-area.bounds[1] for t in complete)==pytest.approx([40,120,200,280],abs=1e-7)


@pytest.mark.parametrize('width,spacing',[(350.,80.),(319.,80.),(1000.,73.5),(20.,80.)])
def test_noninteger_width_preserves_original_fixed_step_grid(width,spacing):
    area=box(0,0,500,width)
    lines=generate_transects(area,spacing,0,'job')
    if width<spacing:
        expected=[width/2]
    else:
        count=math.ceil(width/spacing)+1
        first=(width-(count-1)*spacing)/2
        expected=[first+i*spacing for i in range(count) if 0<=first+i*spacing<=width]
    assert sorted(t.start[1] for t in lines)==pytest.approx(expected,abs=1e-9)
    if width==350:
        assert expected==[55,135,215,295]


def test_fixed_grid_only_snaps_numerical_integer_noise():
    assert stable_ceil_ratio(320+1e-8,80)==4
    assert stable_ceil_ratio(320+1e-5,80)==5
    assert fixed_spacing_rows(0,350,80)==(6,80,-25)
    assert fixed_spacing_rows(0,20,80)==(1,80,10)


def test_real_extra_width_is_not_rounded_away():
    assert centered_rows(0,320.00001,80)[0]==5
    count,pitch,first=centered_rows(0,319,80)
    assert count==4 and pitch<=80 and first==pitch/2
    assert centered_rows(0,20,80)==(1,20,10)


def aircraft(uid,start=None,payload='geophysics',max_wind=10):
    return Uav(id=uid,model='geoscan_401',uav_class='multirotor',ground_speed_ms=12,
               operational_endurance_min=40,max_wind_ms=max_wind,payload_classes=[payload],
               start_site=start,turnaround_buffer_m=0)


def square_scene():
    return Scene(id='square',crs=CrsPipeline.for_point(37.5,55.7),
                 jobs=[SurveyJob('job','geophysics','p',box(0,0,320,320))],
                 payloads={'p':PayloadProfile(id='p',type='geophysics',nominal_agl_m=80,line_spacing_m=80)},
                 fleet=[aircraft('u')],sites=[Site('base',Point(-20,160))],mission=Mission())


def test_base_weights_count_pinned_and_free_sensor_compatible_aircraft_once():
    s=square_scene();s.mission.wind=Wind(speed_ms=3)
    s.fleet=[aircraft('free'),aircraft('a','A'),aircraft('b','B'),aircraft('rgb',payload='rgb'),
             aircraft('wind','A',max_wind=1)]
    s.sites=[Site('A',Point(0,0)),Site('B',Point(100,0)),Site('future',Point(0,10),candidate=True),
             Site('landing',Point(0,20),role='landing'),Site('start',Point(0,30),role='start')]
    eligible=eligible_uavs_for_payload(s,'geophysics')
    assert {sid:w for sid,_,w in weighted_start_bases(s,eligible)}=={'A':2,'B':2,'start':1}
    assert weighted_start_bases(s,[])==[]


def test_tied_actual_sweeps_prefer_endpoints_near_compatible_base():
    s=square_scene();cfg=CoverageConfig(angle_step_deg=90,coverage_pass_percent=100)
    left=build_coverage(s,cfg)
    assert left.per_job[0]['sweep_angle_deg']==0 and left.coverage_percent==pytest.approx(100)
    s.sites[0].point=Point(160,-20)
    below=build_coverage(s,cfg)
    assert below.per_job[0]['sweep_angle_deg']==90 and below.coverage_percent==pytest.approx(100)
    chosen=next(c for c in below.candidates['job'] if c['selected'])
    assert chosen['base_proximity_score']==max(c['base_proximity_score'] for c in below.candidates['job'])
    assert below.candidates==build_coverage(deepcopy(s),cfg).candidates
    # A real wind advantage takes priority even with a base below the zone.
    s.mission.wind=Wind(speed_ms=3,direction_deg_from=270)
    assert build_coverage(s,cfg).per_job[0]['sweep_angle_deg']==0


def test_proximity_uses_every_endpoint_and_base_weight_and_is_finite_at_zero():
    tr=Transect([(0,0),(10,0)],10,'job')
    bases=[('a',(0,0),2),('b',(0,10),3)]
    expected=2/1+2/101+3/101+3/201
    assert endpoint_base_proximity([tr],bases)==pytest.approx(expected)
    assert math.isfinite(endpoint_base_proximity([tr],bases))


def test_only_high_precision_cost_ties_are_reordered_and_groups_do_not_chain():
    a=SweepCandidate(0,[],1000,0,0,0,0,1000,'a',1)
    b=replace(a,angle_deg=90,score=1000+5e-7,label='b',base_proximity_score=2)
    assert rank_candidates([a,b])[0] is b
    c=replace(b,score=1000+2e-6,base_proximity_score=1e9,label='c')
    assert rank_candidates([c,a])[0] is a
    # a~b and b~d cannot make d tie with a through a comparison chain.
    d=replace(b,score=1000+1.2e-6,base_proximity_score=1e10,label='d')
    assert rank_candidates([d,b,a])==[b,a,d]


def test_base_attraction_cannot_reorder_different_transect_counts():
    tr=Transect([(0,0),(10,0)],10,'job')
    a=SweepCandidate(0,[tr]*4,1000,0,3,0,0,1000,'a',1)
    b=replace(a,angle_deg=90,transects=[tr]*5,score=1000+5e-7,
              label='b',base_proximity_score=1e9)
    assert rank_candidates([b,a])==[a,b]
    # The guard applies to exact ties too, in either count direction.
    assert rank_candidates([replace(b,score=1000),a])[0] is a
    assert rank_candidates([replace(b,transects=[tr]*3),a])[0] is a
    # Equal counts still allow the secondary metric to decide.
    same_count=replace(b,transects=[tr]*4)
    assert rank_candidates([a,same_count])==[same_count,a]


def test_count_guard_preserves_cross_count_order_in_multi_angle_search():
    tr=Transect([(0,0),(10,0)],10,'job')
    a=SweepCandidate(0,[tr]*4,1000,0,3,0,0,1000,'a',1)
    b=replace(a,angle_deg=30,transects=[tr]*5,label='b',base_proximity_score=10)
    c=replace(a,angle_deg=60,label='c',base_proximity_score=100)
    d=replace(a,angle_deg=90,label='d',base_proximity_score=1000)
    assert rank_candidates([d,c,b,a])==[a,b,d,c]


def test_equal_base_scores_preserve_primary_cost_order():
    a=SweepCandidate(90,[],1000,0,0,0,0,1000,'a',0)
    b=replace(a,angle_deg=0,score=1000+5e-7,label='b')
    assert rank_candidates([b,a])==[a,b]
