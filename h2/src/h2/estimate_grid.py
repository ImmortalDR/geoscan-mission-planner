"""Shared terrain-weighted grid for bounded-cost fixed-plan estimates.

The graph is an estimate, never an H3 safety certificate. Terrain resolution is
independent of graph resolution; only the spatial search is coarsened.
"""
from dataclasses import dataclass
import math

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from shapely import covers, intersects, linestrings, points, prepare


@dataclass(frozen=True)
class EstimateConfig:
    grid_step_m: float = 100.
    transit_factor: float = 1.25
    transit_height_margin_m: float = 30.
    horizontal_margin_m: float = 10.
    return_margin_s: float = 60.
    max_grid_nodes: int = 100000

    def validate(self):
        for key in ('grid_step_m', 'transit_factor', 'transit_height_margin_m',
                    'horizontal_margin_m', 'return_margin_s'):
            value = getattr(self, key)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f'invalid {key}')
        if self.grid_step_m <= 0 or self.transit_factor < 1 or self.max_grid_nodes < 4:
            raise ValueError('invalid estimate grid limits')


class Elevations:
    """Vectorized equivalent of TransitContext.elevation, including NoData."""
    def __init__(self, context):
        self.context = context

    def __call__(self, xy, strict=True):
        xy = np.asarray(xy, dtype=float)
        if not np.isfinite(xy).all():
            raise ValueError('nonfinite coordinates')
        shape = xy.shape[:-1]
        p = xy.reshape(-1, 2)
        c = self.context
        if not c.has_terrain:
            return np.zeros(shape)
        if c._raster:
            x, y = p.T
            if c._to_raster:
                x, y = c._to_raster.transform(x, y)
            tr = c._inverse_transform
            col = np.floor(tr.a*x + tr.b*y + tr.c).astype(np.int64)
            row = np.floor(tr.d*x + tr.e*y + tr.f).astype(np.int64)
            inside = (row >= 0) & (col >= 0) & (row < c._values.shape[0]) & (col < c._values.shape[1])
            value = np.full(len(p), np.nan)
            rr, cc = row[inside], col[inside]
            value[inside] = np.where(c._mask[rr, cc], np.nan, c._values[rr, cc])
        else:
            grid = np.asarray(c._values)
            uv = (p - c._origin)/c._cell
            inside = (uv[:,0] >= 0) & (uv[:,1] >= 0) & (uv[:,0] <= grid.shape[1]-1) & (uv[:,1] <= grid.shape[0]-1)
            value = np.full(len(p), np.nan)
            q = uv[inside]
            ix = np.minimum(q[:,0].astype(int), grid.shape[1]-2)
            iy = np.minimum(q[:,1].astype(int), grid.shape[0]-2)
            fx, fy = q[:,0]-ix, q[:,1]-iy
            value[inside] = ((1-fy)*(grid[iy,ix]*(1-fx)+grid[iy,ix+1]*fx)
                             + fy*(grid[iy+1,ix]*(1-fx)+grid[iy+1,ix+1]*fx))
        if strict and not np.isfinite(value).all():
            raise ValueError('path outside DEM or intersecting NoData')
        return value.reshape(shape)


def motion(coords, heights, nominal_s, settings, elevation):
    """Full relative motion at H2's terrain sampling resolution: x,y,z,t,AGL.

    Heights are specified at the input polyline vertices. For a survey all are
    identical; time calculation then matches Scheduler.move, including turns.
    """
    coords = np.asarray(coords, dtype=float)
    heights = np.broadcast_to(np.asarray(heights, dtype=float), (len(coords),))
    lengths = np.linalg.norm(np.diff(coords, axis=0), axis=1)
    total = float(lengths.sum())
    if total < 1e-9:
        z = elevation(coords) + heights
        dt=np.maximum(nominal_s/max(1,len(coords)-1),np.abs(np.diff(z))/settings.vertical_speed_ms)
        return np.column_stack((coords,z,np.r_[0.,np.cumsum(dt)],heights))
    step = min(settings.sample_step_m, elevation.context.sample_step_m)
    xy, agl, fractions = [coords[0]], [heights[0]], [0.]
    elapsed = 0.
    for i, distance in enumerate(lengths):
        n = max(1, math.ceil(distance/step)) if elevation.context.has_terrain else 1
        f = np.arange(1,n+1)/n
        xy.extend(coords[i] + f[:,None]*(coords[i+1]-coords[i]))
        agl.extend(heights[i] + f*(heights[i+1]-heights[i]))
        fractions.extend((elapsed + distance*f)/total)
        elapsed += distance
    xy, agl = np.asarray(xy), np.asarray(agl)
    z = elevation(xy) + agl
    nominal_s = max(nominal_s, abs(z[-1]-z[0])/settings.vertical_speed_ms)
    dt = np.maximum(nominal_s*np.diff(fractions), np.abs(np.diff(z))/settings.vertical_speed_ms)
    return np.column_stack((xy, z, np.r_[0.,np.cumsum(dt)], agl))


class TerrainGrid:
    def __init__(self, context, uav, settings, config, endpoints, sites):
        self.context, self.uav, self.settings, self.config = context, uav, settings, config
        self.elevation = Elevations(context)
        radius = uav['turnaround_buffer_m'] if uav['uav_class']=='fixed_wing' else 0.
        clearance = radius + config.horizontal_margin_m
        self.allowed = context.allowed.buffer(-clearance) if context.allowed is not None else None
        self.blocked = context.forbidden.buffer(clearance + 1e-6)
        if self.allowed is not None:
            if self.allowed.is_empty:
                raise ValueError('no grid space after aircraft clearance')
            prepare(self.allowed)
            bounds = self.allowed.bounds
        else:
            p = np.asarray(endpoints)
            bounds = (*p.min(axis=0)-config.grid_step_m*2, *p.max(axis=0)+config.grid_step_m*2)
        prepare(self.blocked)
        step = config.grid_step_m
        lo = np.floor(np.asarray(bounds[:2])/step)*step
        hi = np.ceil(np.asarray(bounds[2:])/step)*step
        self.origin = lo
        self.nx, self.ny = np.rint((hi-lo)/step).astype(int)+1
        count = int(self.nx*self.ny)
        if count > config.max_grid_nodes:
            raise ValueError(f'grid has {count} nodes; increase grid_step_m')
        xx, yy = np.meshgrid(lo[0]+np.arange(self.nx)*step,lo[1]+np.arange(self.ny)*step)
        self.xy = np.column_stack((xx.ravel(),yy.ravel()))
        valid = np.isfinite(self.elevation(self.xy,strict=False))
        geom = points(self.xy)
        if self.allowed is not None: valid &= covers(self.allowed,geom)
        if not self.blocked.is_empty: valid &= ~intersects(self.blocked,geom)
        self.valid = valid
        ids = np.arange(count).reshape(self.ny,self.nx)
        pairs=[]
        for dx,dy in ((1,0),(0,1),(1,1),(-1,1)):
            a=ids[:self.ny-dy, max(0,-dx):self.nx-max(0,dx)].ravel()
            b=ids[dy:, max(0,dx):self.nx-max(0,-dx)].ravel()
            keep=valid[a]&valid[b]
            a,b=a[keep],b[keep]
            clear=self.clear(self.xy[a],self.xy[b]);pairs.append((a[clear],b[clear]))
        a=np.concatenate([p[0] for p in pairs]);b=np.concatenate([p[1] for p in pairs])
        costs=self.edge_costs(self.xy[a],self.xy[b])
        keep=np.isfinite(costs);a,b,costs=a[keep],b[keep],costs[keep]
        self.graph=csr_matrix((np.r_[costs,costs],(np.r_[a,b],np.r_[b,a])),shape=(count,count))
        self.sites=sites
        # Virtual sink accounts for different landing positions and descent times.
        landing=max(uav['landing_time_s'],uav['cruise_agl_m']/settings.vertical_speed_ms)
        sink_a=[];sink_w=[]
        for site in sites:
            n,w=self.attach(np.array([[site['x'],site['y']]]))
            good=np.isfinite(w[0]);sink_a.extend(n[0,good]);sink_w.extend(w[0,good]+landing)
        # Keep the best virtual arc where several bases attach to one node.
        best={}
        for i,w in zip(sink_a,sink_w):best[int(i)]=min(best.get(int(i),math.inf),float(w))
        coo=self.graph.tocoo()
        extended=csr_matrix((np.r_[coo.data,list(best.values())],
                            (np.r_[coo.row,[count]*len(best)],np.r_[coo.col,list(best)])),shape=(count+1,count+1))
        self.return_s,self.return_pred=dijkstra(extended,indices=count,return_predecessors=True)
        self.return_s=self.return_s[:count]
        if uav['uav_class']=='fixed_wing':
            # Account for bends along the selected return tree, plus unknown
            # entry/landing-connector headings. The route itself is not smoothed.
            scale=config.transit_factor*radius/uav['ground_speed_ms']
            penalties=np.zeros(count)
            directions=np.zeros_like(self.xy)
            parent=self.return_pred[:count]
            connected=(parent>=0)&(parent<count)
            directions[connected]=self.xy[parent[connected]]-self.xy[connected]
            for node in np.argsort(self.return_s):
                par=int(parent[node])
                if not math.isfinite(self.return_s[node]):continue
                if par==count:penalties[node]=2*math.pi*scale;continue
                va,vb=directions[node],directions[par]
                norm=float(np.linalg.norm(va)*np.linalg.norm(vb))
                angle=math.acos(float(np.clip(va@vb/norm,-1,1))) if norm else math.pi
                penalties[node]=penalties[par]+angle*scale
            self.return_s+=penalties+math.pi*scale
        self.source_rows={}
        self.distances=None;self.predecessors=None

    def clear(self,a,b):
        lines=linestrings(np.stack((a,b),axis=1))
        valid=np.ones(len(a),dtype=bool)
        if self.allowed is not None:valid &= covers(self.allowed,lines)
        if not self.blocked.is_empty:valid &= ~intersects(self.blocked,lines)
        return valid

    def edge_costs(self,a,b):
        """Add horizontal and vertical time (not their max), then 25% margin."""
        if not len(a):return np.empty(0)
        distance=np.linalg.norm(b-a,axis=1)
        n=max(1,math.ceil(float(distance.max())/min(self.settings.sample_step_m,self.context.sample_step_m))) if self.context.has_terrain else 1
        result=np.empty(len(a))
        for left in range(0,len(a),8192):
            aa,bb=a[left:left+8192],b[left:left+8192]
            f=np.linspace(0,1,n+1)
            z=self.elevation(aa[:,None,:]+(bb-aa)[:,None,:]*f[None,:,None],strict=False)
            vertical=np.abs(np.diff(z,axis=1)).sum(axis=1)
            result[left:left+8192]=self.config.transit_factor*(distance[left:left+8192]/self.uav['ground_speed_ms']+vertical/self.settings.vertical_speed_ms)
        result[~np.isfinite(result)]=math.inf
        return result

    def attach(self,xy):
        xy=np.asarray(xy,dtype=float)
        q=np.floor((xy-self.origin)/self.config.grid_step_m).astype(int)
        ij=q[:,None,:]+np.array([[0,0],[1,0],[0,1],[1,1]])
        inside=(ij[:,:,0]>=0)&(ij[:,:,0]<self.nx)&(ij[:,:,1]>=0)&(ij[:,:,1]<self.ny)
        ids=np.clip(ij[:,:,1],0,self.ny-1)*self.nx+np.clip(ij[:,:,0],0,self.nx-1)
        flat=ids.ravel();a=np.repeat(xy,4,axis=0);b=self.xy[flat]
        valid=inside.ravel()&self.valid[flat]&self.clear(a,b)
        costs=np.full(len(a),math.inf)
        costs[valid]=self.edge_costs(a[valid],b[valid])
        return ids,costs.reshape(-1,4)

    def prepare_sources(self,attachments):
        sources=sorted({int(i) for nodes,costs in attachments for i,c in zip(nodes,costs) if math.isfinite(c)})
        self.source_rows={n:i for i,n in enumerate(sources)}
        # Every endpoint source yields costs to ALL destinations in one search.
        self.distances,self.predecessors=dijkstra(self.graph,indices=sources,return_predecessors=True)

    def return_requirements(self,trajectory):
        """Anchor costs; continuing to the next anchor also covers segment interiors."""
        nodes,cost=self.attach(trajectory[:,:2])
        horizontal=np.min(cost+self.return_s[nodes],axis=1)
        vertical=self.config.transit_factor*(np.abs(trajectory[:,4]-self.uav['cruise_agl_m'])+2*self.config.transit_height_margin_m)/self.settings.vertical_speed_ms
        required=trajectory[:,3]+horizontal+vertical+self.config.return_margin_s
        return float(required.max())

    def path(self,start,end):
        sn,sc=start;tn,tc=end
        best=(math.inf,None,None)
        for a,c in zip(sn,sc):
            if not math.isfinite(c):continue
            row=self.source_rows[int(a)]
            values=c+self.distances[row,tn]+tc
            j=int(np.argmin(values))
            if values[j]<best[0]:best=(float(values[j]),int(a),int(tn[j]))
        duration,a,b=best
        if a is None:return math.inf,np.empty((0,2)),np.empty(0),np.empty(0)
        row=self.source_rows[a];indices=[b]
        while indices[-1]!=a:
            previous=int(self.predecessors[row,indices[-1]])
            if previous<0:raise ValueError('broken grid predecessor')
            indices.append(previous)
        indices.reverse();indices=np.asarray(indices)
        return duration,self.xy[indices],self.return_s[indices],self.distances[row,indices]
