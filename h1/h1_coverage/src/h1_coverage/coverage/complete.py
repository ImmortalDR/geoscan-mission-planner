"""Physical strip coverage and boundary-complete parallel survey passes."""
import math

from shapely.geometry import LineString, Polygon, box

from ..geo import rotate_geom, unrotate_geom, union_all
from ..models import Transect
from .grid import centered_rows


def physical_coverage(transects, payload):
    half_width=(payload.footprint_across_m or payload.swath_spacing_m)/2
    half_forward=(payload.footprint_along_m or 0.)/2 if payload.type not in ('lidar','geophysics') else 0.
    shapes=[]
    for tr in transects:
        for a,b in zip(tr.coords,tr.coords[1:]):
            length=math.dist(a,b)
            if length<=1e-9:continue
            dx,dy=(b[0]-a[0])/length,(b[1]-a[1])/length
            start=(a[0]-dx*half_forward,a[1]-dy*half_forward)
            end=(b[0]+dx*half_forward,b[1]+dy*half_forward)
            shapes.append(Polygon([(start[0]-dy*half_width,start[1]+dx*half_width),
                                   (end[0]-dy*half_width,end[1]+dx*half_width),
                                   (end[0]+dy*half_width,end[1]-dx*half_width),
                                   (start[0]+dy*half_width,start[1]-dx*half_width)]))
    return union_all(shapes)


def complete_parallel_sweep(area, spacing, angle, job_id, flight_space):
    """Cover each band's projection, not just its center-line intersection.

    Survey-area boundaries are not no-fly boundaries. Paths may extend beyond
    the survey polygon only inside flight_space (allowed airspace minus all
    exclusions). Clipped paths are remeasured by the caller; incomplete flight
    space is never reported as complete coverage.
    """
    if area.is_empty or spacing<=0:return []
    origin=(area.centroid.x,area.centroid.y)
    rotated=rotate_geom(area,angle,origin)
    lo,bot,hi,top=rotated.bounds
    count,step,_=centered_rows(bot,top,spacing)
    output=[]
    for i in range(count):
        lower=bot+i*step;upper=bot+(i+1)*step;y=(lower+upper)/2
        strip=rotated.intersection(box(lo-1.,lower-1e-7,hi+1.,upper+1e-7))
        if strip.is_empty:continue
        # Retain separated components; no long survey joins across holes/NFZ.
        parts=list(strip.geoms) if hasattr(strip,'geoms') else [strip]
        row=[]
        for part in parts:
            if part.is_empty or part.area<=1e-8:continue
            left,_,right,_=part.bounds
            line=unrotate_geom(LineString([(left-1e-5,y),(right+1e-5,y)]),angle,origin)
            clipped=line.intersection(flight_space)
            pieces=list(clipped.geoms) if hasattr(clipped,'geoms') else [clipped]
            for piece in pieces:
                if not isinstance(piece,LineString) or piece.length<=1e-6:continue
                coords=[(float(x),float(y)) for x,y in piece.coords]
                # Geometry intersections may reverse line orientation.
                projection=rotate_geom(piece,angle,origin)
                if projection.coords[0][0]>projection.coords[-1][0]:coords.reverse()
                row.append(Transect(coords=coords,length_m=piece.length,job_id=job_id))
        row.sort(key=lambda t:rotate_geom(LineString(t.coords),angle,origin).bounds[0],reverse=bool(i%2))
        for tr in row:
            if i%2:tr=Transect(coords=list(reversed(tr.coords)),length_m=tr.length_m,job_id=job_id)
            output.append(tr)
    return output
