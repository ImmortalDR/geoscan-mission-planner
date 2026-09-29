import math
from copy import deepcopy

import pytest
from h1_coverage.coverage.atomic import build_atomic_tasks
from h1_coverage.models import Transect


@pytest.mark.parametrize('count',[1,2,3,4])
def test_snakes_preserve_lines_and_flip_only_connections(count):
    lines=[]
    for i in range(count):
        coords=[(500000+i*100.,6000000.),(500000+i*100.,6000400.+i*50)]
        if i%2: coords.reverse()
        lines.append(Transect(coords=coords,length_m=math.dist(*coords),job_id='job'))
    original=deepcopy(lines)
    task=build_atomic_tasks([lines],job_id='job',payload_class='rgb',payload_profile_id='camera',
        agl_m=80,sweep_angle_deg=0,fixed_wing_safe_flags=[True])[0]
    task.alternate_fixed_wing_safe=True
    variants=task.route_variants
    assert len(variants)==(1 if count==1 else 2)
    assert variants[0]['geom_coords']==[list(p) for p in task.geom_coords]
    if count>1:
        assert variants[1]['geom_coords']==[list(p) for line in lines for p in reversed(line.coords)]
        assert variants[1]['internal_transition_m']==pytest.approx(
            sum(math.dist(a.start,b.end) for a,b in zip(lines,lines[1:])))
        directed=[tuple(map(tuple,v['geom_coords'][::direction])) for v in variants for direction in [1,-1]]
        assert len(set(directed))==4
    assert lines==original
