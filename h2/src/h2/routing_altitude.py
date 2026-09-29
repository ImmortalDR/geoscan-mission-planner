"""Optional upper altitude envelope for transit, never for survey service."""
import numpy as np


def transit_envelope(points, nominal_s, vertical_speed):
    """Least concave majorant of the already sampled clearance-safe profile.

    It joins terrain peaks without descending into every intervening depression.
    End states are identical, and every interpolated segment lies at or above
    the original trajectory. Time still obeys horizontal and vertical bounds.
    The old trajectory remains a fallback if smoothing does not save time.
    """
    distance=np.r_[0.,np.cumsum(np.linalg.norm(np.diff(points[:,:2],axis=0),axis=1))]
    if distance[-1]<1e-9 or len(points)<3:return points
    # Duplicate XY positions need their original vertical motion envelope.
    if np.any(np.diff(distance)<=1e-9):return points
    z=points[:,2];hull=[]
    for i in range(len(points)):
        while len(hull)>=2:
            a,b=hull[-2:]
            if (z[b]-z[a])*(distance[i]-distance[b])>(z[i]-z[b])*(distance[b]-distance[a])+1e-9:break
            hull.pop()
        hull.append(i)
    roof=np.interp(distance,distance[hull],z[hull])
    # Retain the exact DEM-derived endpoint states, including AGL.
    roof=np.maximum(roof,z)
    duration=max(nominal_s,abs(roof[-1]-roof[0])/vertical_speed)
    dt=np.maximum(duration*np.diff(distance)/distance[-1],np.abs(np.diff(roof))/vertical_speed)
    elapsed=np.r_[0.,np.cumsum(dt)]
    if elapsed[-1]>=points[-1,3]-1e-8:return points
    result=points.copy()
    result[:,4]+=roof-z;result[:,2]=roof;result[:,3]=elapsed
    return result
