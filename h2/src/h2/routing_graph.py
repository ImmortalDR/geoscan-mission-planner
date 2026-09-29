"""Prepared physical transect fence/KNN graph with detailed directed motion.

No terrain/shortest-path work is performed by the Routing callbacks. Return
thresholds use a constructive escape: continue to the next verified endpoint,
then follow the precomputed return path. This covers segment interiors too.
"""
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict
import math
import time

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from pyproj import Transformer

from .contract import fingerprint, mission_clock, load_bundle
from .estimate_grid import Elevations, motion
from .scheduler import Scheduler
from .site_policy import sites_for
from .transit import TransitContext, NoPath
from .routing_altitude import transit_envelope

def heading(coords, end=False):
    diffs=np.diff(np.asarray(coords)[:,:2],axis=0)
    diffs=diffs[np.linalg.norm(diffs,axis=1)>1e-8]
    if not len(diffs):return 0.
    d=diffs[-1] if end else diffs[0]
    return float(np.arctan2(d[1],d[0]))

def angle(a,b):
    return abs(np.arctan2(np.sin(a-b),np.cos(a-b)))

def internal_turn(coords):
    diffs=np.diff(np.asarray(coords)[:,:2],axis=0)
    diffs=diffs[np.linalg.norm(diffs,axis=1)>1e-8]
    headings=np.arctan2(diffs[:,1],diffs[:,0])
    return float(angle(headings[1:],headings[:-1]).sum())


def transit_motion(context, settings, elevation, uav, start, end, kind, turn_rate,
                   direct_only=False):
    """One physical edge, shared by graph preparation and final shortcuts."""
    radius = uav['turnaround_buffer_m'] if uav['uav_class']=='fixed_wing' else 0.
    ha = uav['cruise_agl_m'] if start['kind']=='base' else start['agl_m']
    hb = uav['cruise_agl_m'] if end['kind']=='base' else end['agl_m']
    if direct_only:
        coords = [start['xy'], end['xy']]
        if not context.geometry_clear(coords, radius+10.):
            raise NoPath('Direct shortcut intersects buffered airspace boundary')
    else:
        coords = context.path(start['xy'],end['xy'],radius+10.)
    if len(coords)==1 or (direct_only and tuple(coords[0])==tuple(coords[-1])):
        if abs(ha-hb)>1e-9 and uav['uav_class']=='fixed_wing':
            raise NoPath('Fixed-wing altitude change without horizontal movement')
        coords = [coords[0],coords[0]]
    coords = np.asarray(coords,dtype=float)
    length = np.linalg.norm(np.diff(coords,axis=0),axis=1)
    total = float(length.sum())
    frac = np.r_[0.,np.cumsum(length)]/max(total,1e-9)
    if total>0 and not np.any(np.isclose(frac,.5)):
        j=int(np.searchsorted(frac,.5));f=(.5-frac[j-1])/(frac[j]-frac[j-1])
        coords=np.insert(coords,j,coords[j-1]+f*(coords[j]-coords[j-1]),axis=0)
        frac=np.insert(frac,j,.5)
    heights=ha+(hb-ha)*frac
    if kind not in ('fence','along_transect'):
        heights+=30.*(1.-abs(2.*frac-1.))
    if total<1e-9:heights=np.array([ha,hb])
    nominal=total/uav['ground_speed_ms']+turn_rate*internal_turn(coords)
    trajectory=motion(coords,heights,nominal,settings,elevation)
    return transit_envelope(trajectory,nominal,settings.vertical_speed_ms)


class RoutingGraph:
    def __init__(self, bundle, settings, scene=None, progress=None, knn=4, long_links=2,
                 corner_spacing_m=300.):
        began = time.perf_counter()
        self.bundle = load_bundle(bundle)
        self.settings = settings
        self.scene = deepcopy(scene)
        self.fleet = {u['id']:u for u in bundle['fleet']}
        self.tasks = {t['id']:t for t in bundle['tasks']}
        self.tids = list(self.tasks)
        self.sites = {s['id']:s for s in bundle['sites'] if not s.get('candidate',False)}
        self.policy = {uid:sites_for(bundle,u) for uid,u in self.fleet.items()}
        self.origin,self.horizon = mission_clock(bundle)
        self.window_start = float((scene or {}).get('operating_window',{}).get('start_s',0.))
        self.horizon = min(self.horizon or 7*86400., float((scene or {}).get('operating_window',{}).get('end_s',math.inf)))
        self.return_margin = 60.
        # Use physical motion times without arbitrary transit padding.
        self.transit_factor = 1.0
        self.vertices = []
        self.ends = {}
        self.bases = {}
        self.edges = {}
        self.profiles = {}
        self.by_uav = {}
        self.timings = {}
        self.knn,self.long_links = knn,long_links
        self.corner_spacing_m = float(corner_spacing_m)
        if not math.isfinite(self.corner_spacing_m) or self.corner_spacing_m <= 0:
            raise ValueError('corner_spacing_m must be finite and positive')
        self._topology()
        scheduler = Scheduler(bundle,settings,scene)
        # Temporal airspace is handled by survey windows and final ground-delay
        # repair. Treating it as permanently forbidden made valid scenes empty.
        transit_context = TransitContext(scene,bundle['crs']['metric_epsg'])
        groups = defaultdict(list)
        for uid,u in self.fleet.items():
            groups[fingerprint({k:v for k,v in u.items() if k not in ('id','start_site','landing_site','refuel_sites')})].append(uid)
        for profile_id,uids in groups.items():
            if progress: progress(dict(stage='graph_profile',model=self.fleet[uids[0]]['model'],profile=len(self.profiles)+1,profiles=len(groups)))
            t0 = time.perf_counter()
            u = self.fleet[uids[0]]
            elevation = Elevations(scheduler.context)
            n = len(self.vertices)
            records = {}
            weights = {}
            radius = u['turnaround_buffer_m'] if u['uav_class']=='fixed_wing' else 0.
            # Same half-turn timing envelope as Scheduler.task_seconds. A full
            # circle on every short fence link double-charged the nominal turn.
            turn_rate = max(50.,radius)/u['ground_speed_ms'] if radius else settings.multirotor_turn_s/math.pi
            for (a,b),kind in sorted(self.edges.items()):
                va,vb = self.vertices[a],self.vertices[b]
                try:
                    trajectory=transit_motion(transit_context,settings,elevation,u,va,vb,kind,turn_rate)
                    trajectory[:,3]*=self.transit_factor
                    duration=float(trajectory[-1,3])
                    records[a,b]=trajectory
                    weights[a,b]=max(1e-6,duration)
                except (NoPath,ValueError):
                    continue
            edges_s = time.perf_counter()-t0
            # Sparse KNN is expanded deterministically when structural reachability
            # is missing. Component bridges are physical paths, never dummy arcs.
            expanded=0
            for attempt in range(2):
                matrix=csr_matrix(([v for v in weights.values()],
                    ([a for a,b in weights],[b for a,b in weights])),shape=(n,n))
                dist,pred=dijkstra(matrix,directed=True,return_predecessors=True)
                base_indices=list(self.bases.values())
                missing=[i for i in range(n) if base_indices and not np.isfinite(dist[i,base_indices]).any()]
                if not missing or attempt: break
                # Add a bridge to a reachable corner/base. Motion uses the same
                # model; do not silently mark an unavailable path as feasible.
                good=[i for i in self.corners if i not in set(missing)]
                for a in sorted(set(missing)&set(self.corners)):
                    for b in sorted(good,key=lambda b:(math.dist(self.vertices[a]['xy'],self.vertices[b]['xy']),b))[:4]:
                        for x,y in ((a,b),(b,a)):
                            if (x,y) in weights: continue
                            vx,vy=self.vertices[x],self.vertices[y]
                            hx=u['cruise_agl_m'] if vx['kind']=='base' else vx['agl_m']
                            hy=u['cruise_agl_m'] if vy['kind']=='base' else vy['agl_m']
                            try:
                                coords=transit_context.path(vx['xy'],vy['xy'],radius+10.)
                                if len(coords)<2: coords=coords*2
                                length=sum(math.dist(p,q) for p,q in zip(coords,coords[1:]))
                                nominal=length/u['ground_speed_ms']+turn_rate*internal_turn(coords)
                                trajectory=motion(coords,np.linspace(hx,hy,len(coords)),nominal,settings,elevation)
                                trajectory=transit_envelope(trajectory,nominal,settings.vertical_speed_ms)
                                trajectory[:,3]*=self.transit_factor
                                records[x,y]=trajectory;weights[x,y]=max(1e-6,float(trajectory[-1,3]))
                                self.edges[x,y]='bridge';expanded+=1
                            except (NoPath,ValueError): pass
            takeoff=max(u['takeoff_time_s'],u['cruise_agl_m']/settings.vertical_speed_ms)
            landing=max(u['landing_time_s'],u['cruise_agl_m']/settings.vertical_speed_ms)
            emergency=[self.bases[sid] for sid in self.policy[uids[0]]['emergency']]
            from .routing_paths import prepare_paths
            source_headings=[]
            for vertex in self.vertices:
                if vertex['kind']=='base':source_headings.append(math.nan);continue
                task=self.tasks[vertex['task_id']]
                source_headings.append(heading(task['geom_coords'],True) if vertex['id'].endswith(':1') else heading(task['geom_coords'])+math.pi)
            paths=prepare_paths(self.vertices,records,weights,source_headings,turn_rate*self.transit_factor,
                                emergency,landing,self.return_margin,angle,heading)
            returns=paths['returns']
            service=[]
            for tid in self.tids:
                task=self.tasks[tid]
                allowed=any(uid in bundle['feasibility']['eligible_uav_ids_by_task'][tid] for uid in uids)
                for reverse in (False,True):
                    a,b=self.ends[tid]
                    if reverse:a,b=b,a
                    row=dict(task_id=tid,reverse=reverse,entry=a,exit=b,valid=False)
                    if allowed and scheduler.context.geometry_clear(task['geom_coords']) and (u['uav_class']!='fixed_wing' or task['fixed_wing_safe']):
                        try:
                            coords=task['geom_coords'][::-1] if reverse else task['geom_coords']
                            points=motion(coords,task['agl_m'],scheduler.task_seconds(uids[0],tid),settings,elevation)
                            duration=float(points[-1,3])
                            required=duration+returns[b]+self.return_margin
                            row.update(valid=math.isfinite(required),points=points,time_s=duration,required_s=required)
                        except ValueError: pass
                    service.append(row)
            table=dict(uav=u,records=records,weights=weights,services=service,takeoff=takeoff,landing=landing,elevation=elevation,**paths)
            self.profiles[profile_id]=table
            for uid in uids:self.by_uav[uid]=table
            self.timings[profile_id]=dict(id=profile_id,model=u['model'],uav_ids=uids,physical_edges=len(weights),
                expanded_edges=expanded,valid_orientations=sum(row['valid'] for row in service),
                detailed_edges_s=edges_s,total_s=time.perf_counter()-t0)
        self.preparation_s=time.perf_counter()-began

    def _boundary_portals(self, ordered):
        """Promote existing endpoints by bisecting long boundary intervals.

        Both sides use the same selected transect. Cumulative boundary distance
        accounts for staggered/irregular ends; using the longer side at each
        step bounds the distance on either side. Adjacent lines cannot be split
        further without changing H1 geometry. Ties select the lower index.
        """
        distance = [0.]
        for previous, following in zip(ordered, ordered[1:]):
            distance.append(distance[-1] + max(
                math.dist(self.vertices[a]['xy'], self.vertices[b]['xy'])
                for a, b in zip(previous, following)))
        selected = {0, len(ordered) - 1}
        pending = [(0, len(ordered) - 1)]
        while pending:
            lo, hi = pending.pop()
            if hi - lo <= 1 or distance[hi] - distance[lo] <= self.corner_spacing_m + 1e-8:
                continue
            midpoint = (distance[lo] + distance[hi]) / 2.
            middle = min(range(lo + 1, hi), key=lambda i: (abs(distance[i] - midpoint), i))
            selected.add(middle)
            pending.extend(((lo, middle), (middle, hi)))
        return [endpoint for i in sorted(selected) for endpoint in ordered[i]]

    def _topology(self):
        groups=defaultdict(list)
        for tid,t in self.tasks.items():
            indices=[]
            for side,xy in enumerate((t['entry'],t['exit'])):
                indices.append(len(self.vertices))
                self.vertices.append(dict(id=f'{tid}:{side}',kind='endpoint',xy=tuple(xy),agl_m=t['agl_m'],task_id=tid,parent_task_id=t['parent_task_id']))
            self.ends[tid]=tuple(indices)
            self._pair(*indices,'along_transect')
            groups[t['parent_task_id']].append(tid)
        corners=[]
        for parent,tids in groups.items():
            v=np.asarray(self.tasks[tids[0]]['exit'])-self.tasks[tids[0]]['entry']
            ordered=[]
            for tid in tids:
                a,b=self.ends[tid]
                if np.dot(np.asarray(self.vertices[b]['xy'])-self.vertices[a]['xy'],v)<0:a,b=b,a
                ordered.append((a,b))
            for previous,following in zip(ordered,ordered[1:]):
                self._pair(previous[0],following[0],'fence');self._pair(previous[1],following[1],'fence')
            corners.extend(self._boundary_portals(ordered))
        # H1 splits a single survey zone into endurance-sized parent chunks.
        # Continue its fence across these bookkeeping boundaries: adjacent
        # transects keep their survey AGL, without a new transit climb each time.
        families=defaultdict(list)
        for tid,t in self.tasks.items():
            yaw=math.atan2(t['exit'][1]-t['entry'][1],t['exit'][0]-t['entry'][0])%math.pi
            families[(t['job_id'],round(yaw,6),round(t['agl_m'],6))].append(tid)
        for tids in families.values():
            first=self.tasks[tids[0]];along=np.asarray(first['exit'])-first['entry']
            across=np.array([-along[1],along[0]])
            tids.sort(key=lambda tid:(float((np.asarray(self.tasks[tid]['entry'])+self.tasks[tid]['exit'])@across),tid))
            ordered=[]
            for tid in tids:
                a,b=self.ends[tid]
                if np.dot(np.asarray(self.vertices[b]['xy'])-self.vertices[a]['xy'],along)<0:a,b=b,a
                ordered.append((a,b))
            for previous,following in zip(ordered,ordered[1:]):
                for a,b in zip(previous,following):
                    self._pair(a,b,'fence')
        self.corners=tuple(corners)
        for vertex in self.corners:
            self.vertices[vertex]['portal'] = True
        for a in corners:
            candidates=[b for b in corners if self.vertices[a]['parent_task_id']!=self.vertices[b]['parent_task_id']]
            for b in sorted(candidates,key=lambda b:(math.dist(self.vertices[a]['xy'],self.vertices[b]['xy']),b))[:self.knn]:
                self._pair(a,b,'between_groups')
        # Two distance scales add shortcuts beyond the local neighbourhood.
        # Stable geometric ordering, with no RNG or Python hash dependence.
        # At most long_links*C new undirected edges: never a corner clique.
        for a in corners:
            candidates=sorted((b for b in corners if
                self.vertices[a]['parent_task_id']!=self.vertices[b]['parent_task_id']
                and (a,b) not in self.edges),
                key=lambda b:(math.dist(self.vertices[a]['xy'],self.vertices[b]['xy']),b))
            if not candidates:continue
            selected=[]
            for band in range(self.long_links):
                lo=len(candidates)*band//self.long_links
                hi=len(candidates)*(band+1)//self.long_links
                pool=candidates[lo:hi]
                if not pool:continue
                # Prefer a new direction at the farther end of this scale.
                def rank(b):
                    yaw=math.atan2(self.vertices[b]['xy'][1]-self.vertices[a]['xy'][1],
                                   self.vertices[b]['xy'][0]-self.vertices[a]['xy'][0])
                    separation=min((angle(yaw,h) for h in selected),default=math.pi)
                    return (separation,math.dist(self.vertices[a]['xy'],self.vertices[b]['xy']),-b)
                b=max(pool,key=rank)
                selected.append(math.atan2(self.vertices[b]['xy'][1]-self.vertices[a]['xy'][1],
                                           self.vertices[b]['xy'][0]-self.vertices[a]['xy'][0]))
                self._pair(a,b,'long_link')
        for sid,s in self.sites.items():
            a=len(self.vertices);self.bases[sid]=a
            self.vertices.append(dict(id=sid,kind='base',xy=(s['x'],s['y']),site_id=sid))
            for b in corners:
                self._pair(a,b,'base_link')

    def _pair(self,a,b,kind):
        if a!=b:
            self.edges.setdefault((a,b),kind);self.edges.setdefault((b,a),kind)

    def path_edges(self, uid, a, b):
        p=self.by_uav[uid];pred=p['pred'][a];current=int(p['path_ends'][b]);origin=int(p['path_starts'][a])
        result=[]
        while current!=origin:
            if current<len(p['path_states']):result.append(p['path_states'][current])
            previous=int(pred[current])
            if previous<0:raise ValueError('no physical graph path')
            current=previous
            if len(result)>len(p['path_states']):raise ValueError('cyclic predecessor tree')
        return result[::-1]

    def geojson(self):
        from shapely.geometry import LineString
        transformer=Transformer.from_crs(self.bundle['crs']['metric_epsg'],4326,always_xy=True)
        features=[]
        def add(kind,coords,props):
            features.append(dict(type='Feature',geometry=dict(type=kind,coordinates=coords),properties=props))
        for i,v in enumerate(self.vertices):
            add('Point',list(transformer.transform(*v['xy'])),dict(id=v['id'],node=i,kind=v['kind'],portal=v.get('portal',False)))
        for (a,b),kind in sorted(self.edges.items()):
            if a>b:continue
            valid=[(key,p) for key,p in self.profiles.items() if (a,b) in p['records'] or (b,a) in p['records']]
            if not valid:continue
            for key,p in valid:
                forward=p['records'].get((a,b));backward=p['records'].get((b,a))
                symmetric=(forward is not None and backward is not None and
                           forward.shape==backward.shape and np.allclose(forward[:,:2],backward[::-1,:2]))
                directions=[(a,b)] if symmetric else [edge for edge in ((a,b),(b,a)) if edge in p['records']]
                for x,y in directions:
                    # DEM samples have different altitudes but lie on straight
                    # map segments. Sending all of them made the graph view tens
                    # of MB. Only the display polyline is simplified (1 cm);
                    # timing, resource and H3 keep the complete 3D trajectory.
                    xy=p['records'][x,y][:,:2]
                    if len(xy)>2:xy=LineString(xy).simplify(.01).coords
                    coords=[list(transformer.transform(px,py)) for px,py in xy]
                    label=f"{p['uav']['model']} ({', '.join(self.timings[key]['uav_ids'])})"
                    values={label:dict(forward_s=p['weights'][x,y],backward_s=p['weights'].get((y,x)) if symmetric else None)}
                    add('LineString',coords,dict(id=f'e:{x}:{y}',kind=kind,source=x,target=y,
                        directed=not symmetric,profile_id=key,profile_times=values))
        for tid,t in self.tasks.items():
            # These arrows are the two alternative complete survey operations.
            for reverse in (False,True):
                coords=t['geom_coords'][::-1] if reverse else t['geom_coords']
                add('LineString',[list(transformer.transform(*xy)) for xy in coords],
                    dict(id=f'{tid}:{"-" if reverse else "+"}',kind='survey',task_id=tid,directed=True,direction=-1 if reverse else 1))
        for rank,(uid,u) in enumerate(self.fleet.items()):
            for mode in ('start','finish'):
                allowed=self.policy[uid][mode]
                if not allowed:continue
                xy=np.mean([self.vertices[self.bases[sid]]['xy'] for sid in allowed],axis=0)+np.array([120.*(rank+1),180. if mode=='start' else -180.])
                did=f'D_{mode}:{uid}'
                add('Point',list(transformer.transform(*xy)),dict(id=did,kind='dummy',uav_id=uid,label=did))
                for sid in allowed:
                    points=[xy,self.vertices[self.bases[sid]]['xy']]
                    if mode=='finish':points.reverse()
                    add('LineString',[list(transformer.transform(*point)) for point in points],
                        dict(id=f'{did}:{sid}',kind='dummy_link',directed=True,uav_id=uid,time_s=0,administrative=True))
        return dict(type='FeatureCollection',features=features)

    def report(self):
        return dict(preparation_s=self.preparation_s,vertices=len(self.vertices),transects=len(self.tasks),
                    orientations=2*len(self.tasks),profiles=list(self.timings.values()),knn=self.knn,
                    corners=len(self.corners),base_connections='all_corners',base_transfers=False,
                    corner_spacing_m=self.corner_spacing_m,
                    corner_policy='both ends of boundary transects; recursive physical midpoint; existing H1 endpoints only',
                    long_links_per_corner=self.long_links,
                    long_link_pairs=sum(kind=='long_link' for kind in self.edges.values())//2,
                    long_link_policy='two distance bands; angular diversity; stable geometric ordering',
                    transit_factor=self.transit_factor,return_margin_s=self.return_margin,
                    transit_altitude_policy='upper concave clearance envelope; exact endpoint AGL; immutable survey AGL',
                    return_policy='any active emergency landing site; onward service requires refuel_sites',
                    path_policy='heading-aware shortest paths; temporal transit checked/repaired before H3')
