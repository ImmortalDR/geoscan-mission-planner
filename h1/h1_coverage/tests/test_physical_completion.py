from shapely.geometry import Polygon,box,LineString
import pytest

from h1_coverage.coverage.complete import complete_parallel_sweep,physical_coverage
from h1_coverage.models import PayloadProfile


@pytest.mark.parametrize('angle',[0.,15.,75.,165.])
def test_lidar_complete_strip_covers_oblique_boundary_and_preserves_flight_space(angle):
    area=Polygon([(0,0),(1000,130),(680,760),(25,390)])
    allowed=area.envelope.buffer(200.)
    profile=PayloadProfile(id='lidar',type='lidar',nominal_agl_m=120.,swath_spacing_m=80.)
    lines=complete_parallel_sweep(area,80.,angle,'job',allowed)
    assert lines and area.difference(physical_coverage(lines,profile)).area<1e-3
    assert all(allowed.covers(LineString(t.coords)) for t in lines)
    assert lines==complete_parallel_sweep(area,80.,angle,'job',allowed)


def test_completion_does_not_cross_forbidden_airspace_or_fake_coverage():
    area=box(0,0,1000,1000);forbidden=box(300,0,700,1000)
    allowed=area.buffer(200).difference(forbidden)
    profile=PayloadProfile(id='lidar',type='lidar',nominal_agl_m=120.,swath_spacing_m=80.)
    lines=complete_parallel_sweep(area,80.,0.,'job',allowed)
    assert all(LineString(t.coords).intersection(forbidden.buffer(-1e-6)).is_empty for t in lines)
    assert area.difference(physical_coverage(lines,profile)).area>100000
