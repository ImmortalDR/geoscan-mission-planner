"""Prepare once, evaluate explicit flight plans without running an optimizer.

ESTIMATED_FEASIBLE is a resource/schedule estimate, never a SAFE certificate.
The original planner and independent H3 gate remain available for verification.
"""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import hashlib
import math
from pathlib import Path
import time

import numpy as np
from shapely.geometry import LineString, shape

from .contract import fingerprint, load_bundle, mission_clock
from .estimate_grid import EstimateConfig, Elevations, TerrainGrid, motion
from .scheduler import Scheduler, Settings
from .task_routes import route_variants
from .transit import TransitContext


def profile_key(uav):
    return fingerprint({k:v for k,v in uav.items() if k not in ('id','start_site','landing_site')})


class FixedPlanEstimator:
    def __init__(self,bundle,scene=None,settings=None,config=None):
        began=time.perf_counter()
        self.bundle=load_bundle(bundle)
        self.scene=deepcopy(scene)
        self.settings=deepcopy(settings or Settings())
        self.settings.validate()
        self.config=config or EstimateConfig();self.config.validate()
        self.fleet={u['id']:u for u in self.bundle['fleet']}
        self.sites={s['id']:s for s in self.bundle['sites'] if not s.get('candidate',False)}
        self.tasks={t['id']:t for t in self.bundle['tasks']}
        self.eligible=self.bundle['feasibility']['eligible_uav_ids_by_task']
        self.origin,self.horizon=mission_clock(self.bundle)
        operating=(scene or {}).get('operating_window',{})
        self.window_start_s=float(operating.get('start_s',0.))
        if 'end_s' in operating:
            self.horizon=min(self.horizon if self.horizon is not None else math.inf,float(operating['end_s']))
        if not math.isfinite(self.window_start_s) or self.window_start_s<0 or (self.horizon is not None and not math.isfinite(self.horizon)):
            raise ValueError('invalid operating window')
        self.profiles={};self.by_uav={};self.timings={}
        scheduler=Scheduler(self.bundle,self.settings,self.scene)
        grid_scene=deepcopy(scene) if scene else None
        if grid_scene:
            # Permanent reachability must not rely on an occasionally blocked route.
            grid_scene.setdefault('forbidden',[]).extend(z['geometry'] for z in grid_scene.get('temporal',[]))
        grid_context=TransitContext(grid_scene,self.bundle['crs']['metric_epsg'])
        groups={}
        for uid,u in self.fleet.items():groups.setdefault(profile_key(u),[]).append(uid)
        for key,uids in groups.items():
            u=self.fleet[uids[0]];task_rows={};endpoints={}
            stage=time.perf_counter()
            elevation=Elevations(scheduler.context)
            for tid,task in self.tasks.items():
                if not any(uid in self.eligible[tid] for uid in uids):continue
                for variant in route_variants(task):
                    for reverse in (False,True):
                        identity=(tid,variant['id'],reverse)
                        coords=variant['geom_coords'][::-1] if reverse else variant['geom_coords']
                        reason=None
                        if u['uav_class']=='fixed_wing' and not variant['fixed_wing_safe']:
                            reason='fixed_wing_variant'
                        elif not scheduler.context.geometry_clear(coords):reason='task_geometry'
                        if reason:
                            task_rows[identity]={'valid':False,'reason':reason};continue
                        try:
                            trajectory=motion(coords,task['agl_m'],scheduler.task_seconds(uids[0],tid,variant),self.settings,elevation)
                        except ValueError as exc:
                            task_rows[identity]={'valid':False,'reason':str(exc)};continue
                        entry=(*coords[0],task['agl_m']);exit_=(*coords[-1],task['agl_m'])
                        temporal=[z for z in (scene or {}).get('temporal',[])
                                  if LineString(coords).intersects(shape(z['geometry']))]
                        task_rows[identity]={'valid':True,'points':trajectory,'time_s':float(trajectory[-1,3]),
                                             'entry':entry,'exit':exit_,'temporal':temporal}
                        endpoints[entry]=None;endpoints[exit_]=None
            detailed_s=time.perf_counter()-stage
            for site in self.sites.values():endpoints[(site['x'],site['y'],u['cruise_agl_m'])]=None
            stage=time.perf_counter()
            grid=TerrainGrid(grid_context,u,self.settings,self.config,[p[:2] for p in endpoints],
                             [s for s in self.sites.values() if s['role'] in ('both','landing','reserve')])
            grid_s=time.perf_counter()-stage
            stage=time.perf_counter()
            for row in task_rows.values():
                if row['valid']:
                    row['required_start_s']=max(row['time_s'],grid.return_requirements(row['points']))
                    if not math.isfinite(row['required_start_s']):
                        row['valid']=False;row['reason']='no_permanent_return_path'
            reserve_s=time.perf_counter()-stage
            stage=time.perf_counter()
            coordinates=list(endpoints)
            nodes,costs=grid.attach(np.array([p[:2] for p in coordinates]))
            # One verified nearby node per endpoint bounds work independently of pair count.
            best=np.argmin(costs,axis=1)
            attachments=[(nodes[i,j:j+1],costs[i,j:j+1]) for i,j in enumerate(best)]
            grid.prepare_sources(attachments)
            for i,p in enumerate(coordinates):endpoints[p]=i
            n=np.array([int(x[0][0]) for x in attachments]);c=np.array([float(x[1][0]) for x in attachments])
            matrix=np.full((len(n),len(n)),math.inf)
            for i,(node,cost) in enumerate(zip(n,c)):
                if math.isfinite(cost):matrix[i]=cost+grid.distances[grid.source_rows[int(node)],n]+c
            heights=np.array([p[2] for p in coordinates])
            peak=np.maximum(np.maximum(heights[:,None],heights[None,:]),u['cruise_agl_m'])+self.config.transit_height_margin_m
            matrix+=self.config.transit_factor*(2*peak-heights[:,None]-heights[None,:])/self.settings.vertical_speed_ms
            matrix_s=time.perf_counter()-stage
            tables={'uav':u,'tasks':task_rows,'grid':grid,'endpoint_ids':endpoints,'coordinates':coordinates,
                    'attachments':attachments,'times':matrix,'legs':{}}
            self.profiles[key]=tables
            for uid in uids:self.by_uav[uid]=tables
            self.timings[key]={'model':u['model'],'uav_ids':uids,'task_variants':len(task_rows),
                               'valid_variants':sum(x['valid'] for x in task_rows.values()),
                               'grid_nodes':len(grid.xy),'graph_sources':len(grid.source_rows),
                               'endpoint_pairs':int(matrix.size),'detailed_tasks_s':detailed_s,
                               'grid_and_return_field_s':grid_s,'task_return_thresholds_s':reserve_s,
                               'transition_matrix_s':matrix_s}
        terrain_hash = None
        if grid_context._raster:
            with Path(self.scene['terrain']['path']).open('rb') as stream:
                terrain_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
        self.fingerprint=fingerprint({'bundle':self.bundle,'scene':self.scene,'terrain_sha256':terrain_hash,
                                      'settings':asdict(self.settings),'config':asdict(self.config)})
        self.preparation_s=time.perf_counter()-began

    def report(self):
        return {'preparation_s':self.preparation_s,'profiles':list(self.timings.values()),
                'config':asdict(self.config),'input_fingerprint':self.fingerprint}

    def task(self,uid,tid,variant='primary',reverse=False):
        return self.by_uav[uid]['tasks'].get((tid,variant,reverse))

    def leg(self,uid,start,end):
        tables=self.by_uav[uid];key=(tuple(start),tuple(end))
        if key in tables['legs']:return tables['legs'][key]
        a,b=(tables['endpoint_ids'][p] for p in key)
        grid=tables['grid'];u=tables['uav'];cfg=self.config
        if key[0] == key[1]:
            coords=np.array([start[:2],end[:2]],dtype=float)
            heights=np.array([start[2],end[2]],dtype=float)
            points=motion(coords,heights,0.,self.settings,grid.elevation)
            result={'time_s':0.,'required_start_s':grid.return_requirements(points),
                    'coords':coords,'heights':heights}
            tables['legs'][key]=result
            return result
        duration=float(tables['times'][a,b])
        if not math.isfinite(duration):
            result={'time_s':math.inf,'required_start_s':math.inf,'coords':None,'heights':None}
        else:
            _,xy,escape,_=grid.path(tables['attachments'][a],tables['attachments'][b])
            coords=np.vstack((np.array(start[:2]),xy,np.array(end[:2])))
            keep=np.r_[True,np.any(np.diff(coords,axis=0)!=0,axis=1)];coords=coords[keep]
            if len(coords)==1:coords=np.vstack((coords,coords))
            lengths=np.linalg.norm(np.diff(coords,axis=0),axis=1)
            # Allocate climb/descent across the first/last half of the whole route.
            f=np.r_[0.,np.cumsum(lengths)]/max(float(lengths.sum()),1e-9)
            peak=max(start[2],end[2],u['cruise_agl_m'])+cfg.transit_height_margin_m
            heights=np.where(f<=.5,start[2]+2*f*(peak-start[2]),peak+2*(f-.5)*(end[2]-peak))
            if lengths.sum() < 1e-9:
                if u['uav_class']=='fixed_wing':
                    result={'time_s':math.inf,'required_start_s':math.inf,'coords':None,'heights':None}
                    tables['legs'][key]=result
                    return result
                coords=np.repeat(coords[:1],3,axis=0)
                heights=np.array([start[2],peak,end[2]])
            # The midpoint must exist even when the chosen route is one straight edge.
            if lengths.sum()>0 and not np.any(np.isclose(f,.5,atol=1e-12)):
                j=int(np.searchsorted(f,.5));alpha=(.5-f[j-1])/(f[j]-f[j-1])
                coords=np.insert(coords,j,coords[j-1]+alpha*(coords[j]-coords[j-1]),axis=0)
                heights=np.insert(heights,j,peak)
            turn_s=(2*math.pi*u['turnaround_buffer_m']/u['ground_speed_ms']*cfg.transit_factor
                    if u['uav_class']=='fixed_wing' else 0.)
            if u['uav_class']=='fixed_wing' and len(coords)>2:
                v=np.diff(coords,axis=0);norm=np.linalg.norm(v,axis=1)
                good=(norm[:-1]>1e-8)&(norm[1:]>1e-8)
                cos=np.clip(np.sum(v[:-1][good]*v[1:][good],axis=1)/(norm[:-1][good]*norm[1:][good]),-1,1)
                turn_s+=float(np.arccos(cos).sum())*u['turnaround_buffer_m']/u['ground_speed_ms']*cfg.transit_factor
            duration+=turn_s
            # Materialization may distribute the time margin unevenly along the
            # route. Since every remaining segment takes at least distance/v,
            # t(node) <= horizontal_prefix + duration - horizontal_total.
            # Charging all slack at every prefix preserves the return guarantee.
            vertical_escape=max(abs(h-u['cruise_agl_m']) for h in (start[2],end[2],peak))
            extra=cfg.transit_factor*(vertical_escape+2*cfg.transit_height_margin_m)/self.settings.vertical_speed_ms
            tail=float(tables['attachments'][b][1][0])
            prefix=(np.linalg.norm(np.asarray(start[:2])-xy[0])+
                    np.r_[0.,np.cumsum(np.linalg.norm(np.diff(xy,axis=0),axis=1))])/u['ground_speed_ms']
            slack=max(0.,duration-float(lengths.sum())/u['ground_speed_ms'])
            required=max(float(np.max(prefix+slack+escape+extra)),
                         duration+tail+float(escape[-1])+extra)+cfg.return_margin_s
            result={'time_s':duration,'required_start_s':max(duration,required),
                    'coords':coords,'heights':heights}
        tables['legs'][key]=result
        return result

    def evaluate(self,plan,require_complete=True):
        """Evaluate supplied sorties in list order; omitted start_s means earliest ready.

        A complete fixed plan specifies UAV, base(s), task order, variant and
        direction. It is never silently reassigned, repartitioned or deconflicted.
        """
        if not isinstance(plan,dict) or not isinstance(plan.get('sorties'),list):
            raise ValueError('fixed plan requires a sorties list')
        if plan.get('schema_version','h2.fixed_plan.v1')!='h2.fixed_plan.v1':
            raise ValueError('expected h2.fixed_plan.v1')
        if any(not isinstance(s,dict) for s in plan['sorties']):
            raise ValueError('each sortie must be an object')
        began=time.perf_counter();violations=[];result=[];assigned=Counter();ready={};position={}
        def fail(code,**details):violations.append({'code':code,**details})
        for index,sortie in enumerate(plan.get('sorties',[])):
            uid=sortie.get('uav_id')
            if uid not in self.fleet:fail('unknown_uav',sortie=index);continue
            u=self.fleet[uid]
            if self.bundle['mission']['wind']['speed_ms']>u['max_wind_ms']:fail('wind_limit',sortie=index)
            start_id=sortie.get('start_site_id');land_id=sortie.get('landing_site_id')
            if start_id not in self.sites or land_id not in self.sites:
                fail('unknown_site',sortie=index);continue
            start,land=self.sites[start_id],self.sites[land_id]
            if start['role'] not in ('both','start') or land['role'] not in ('both','landing','reserve'):fail('site_role',sortie=index)
            if uid in position and start_id!=position[uid]:fail('site_continuity',sortie=index)
            if uid not in position and u['start_site'] is not None and start_id!=u['start_site']:fail('pinned_start',sortie=index)
            if u['landing_site'] is not None and land_id!=u['landing_site']:fail('pinned_landing',sortie=index)
            if not self.bundle['mission']['allow_different_start_end'] and start_id!=land_id:fail('different_sites',sortie=index)
            tids=sortie.get('task_ids',[]);variants=sortie.get('task_variants',['primary']*len(tids));reversals=sortie.get('task_reversed',[False]*len(tids))
            if len(tids)!=len(variants) or len(tids)!=len(reversals) or any(type(x) is not bool for x in reversals):
                fail('task_variant_fields',sortie=index);continue
            depart=sortie.get('start_s',ready.get(uid,self.window_start_s))
            if isinstance(depart,bool) or not isinstance(depart,(float,int)) or not math.isfinite(depart) or depart<0:
                fail('invalid_departure',sortie=index);continue
            if depart<self.window_start_s-1e-6:fail('daylight_window',sortie=index)
            if depart<ready.get(uid,0.)-1e-6:fail('service_gap',sortie=index)
            elapsed=max(u['takeoff_time_s'],u['cruise_agl_m']/self.settings.vertical_speed_ms)
            required=elapsed;parts=[];current=(start['x'],start['y'],u['cruise_agl_m']);task_times=[];bad=False
            for tid,variant,reverse in zip(tids,variants,reversals):
                assigned[tid]+=1
                if tid not in self.tasks or uid not in self.eligible.get(tid,[]):fail('ineligible_task',task_id=tid,sortie=index);bad=True;continue
                task=self.task(uid,tid,variant,reverse)
                if not task or not task['valid']:
                    fail('invalid_task_variant',task_id=tid,sortie=index,reason=(task or {}).get('reason'));bad=True;continue
                leg=self.leg(uid,current,task['entry']);parts.append(('transit',leg,None))
                required=max(required,elapsed+leg['required_start_s']);elapsed+=leg['time_s']
                required=max(required,elapsed+task['required_start_s'])
                begin=elapsed;elapsed+=task['time_s'];parts.append(('survey',task,tid))
                task_times.append({'task_id':tid,'start_s':depart+begin,'end_s':depart+elapsed})
                for zone in task['temporal']:
                    if depart+begin<=zone['end_s'] and depart+elapsed>=zone['start_s']:
                        fail('temporal_restriction',task_id=tid,sortie=index,zone_id=zone.get('id'))
                w=self.settings.task_windows.get(tid)
                if w and not w[0]<=depart+begin<=w[1]:fail('task_window',task_id=tid,sortie=index)
                current=task['exit']
            leg=self.leg(uid,current,(land['x'],land['y'],u['cruise_agl_m']));parts.append(('transit',leg,None))
            required=max(required,elapsed+leg['required_start_s']);elapsed+=leg['time_s']
            elapsed+=max(u['landing_time_s'],u['cruise_agl_m']/self.settings.vertical_speed_ms)
            usable=u['operational_endurance_min']*60*(1-u['energy_reserve_fraction'])
            required=max(required,elapsed)
            if not math.isfinite(elapsed):fail('no_grid_route',sortie=index)
            if required>usable:fail('resource_or_return_reserve',sortie=index,required_s=_finite(required),usable_s=usable)
            if self.horizon is not None and depart+elapsed>self.horizon:fail('mission_window',sortie=index)
            ready[uid]=depart+elapsed+u['service_time_s'];position[uid]=land_id
            result.append({'uav_id':uid,'start_site_id':start_id,'landing_site_id':land_id,'task_ids':list(tids),
                           'task_variants':list(variants),'task_reversed':list(reversals),'start_s':depart,
                           'end_s':_finite(depart+elapsed),'flight_time_s':_finite(elapsed),'required_start_s':_finite(required),
                           'usable_s':usable,'resource_margin_s':_finite(usable-required),'task_times':_clean(task_times),
                           '_parts':parts,'_bad':bad})
        for tid,count in assigned.items():
            if count!=1:fail('duplicate_task',task_id=tid,count=count)
        if require_complete:
            for tid in self.tasks.keys()-assigned.keys():fail('missing_task',task_id=tid)
        status='ESTIMATED_FEASIBLE' if not violations else 'ESTIMATE_REJECTED'
        public=[{k:v for k,v in row.items() if not k.startswith('_')} for row in result]
        complete_timing=(len(public)==len(plan.get('sorties',[]))
                         and all(x['end_s'] is not None and not r['_bad'] for x,r in zip(public,result)))
        report={'schema_version':'h2.estimate.v1','status':status,'sorties':public,'violations':violations,
                'makespan_s':max((x['end_s'] for x in public),default=0.) if complete_timing else None,
                'total_flight_s':sum(x['flight_time_s'] for x in public) if complete_timing else None,
                'evaluation_s':time.perf_counter()-began,'preparation_s':self.preparation_s,
                'input_fingerprint':self.fingerprint,'requires_h3_validation':True,
                'unchecked':['inter_aircraft_conflicts','continuous_surface_clearance','coverage_and_GSD'],
                'temporal_policy':'transit and return avoid temporal zones permanently; intersecting surveys avoid the entire zone time window at every altitude'}
        return report


    def materialize(self,plan,*,check=True):
        """Construct the selected grid routes for independent H2/H3 validation.

        This does not repair conflicts, change assignments, or declare SAFE.
        Overruns against the estimate are reported explicitly.
        """
        from .checks import check_plan
        from .planner import metrics
        began=time.perf_counter()
        estimate=self.evaluate(plan)
        if len(estimate['sorties'])!=len(plan.get('sorties',[])):
            raise ValueError('cannot materialize malformed fixed plan')
        output=[];overruns=[];ready={}
        for source,estimated in zip(plan['sorties'],estimate['sorties']):
            uid=source['uav_id'];u=self.fleet[uid];grid=self.by_uav[uid]['grid']
            start=self.sites[source['start_site_id']];land=self.sites[source['landing_site_id']]
            depart=source.get('start_s',ready.get(uid,self.window_start_s));clock=depart
            current=(start['x'],start['y'],u['cruise_agl_m'])
            points=[];task_times=[];distance=transit_length=survey_length=0.
            def point(xy,agl,phase,tid=None):
                points.append(dict(x=float(xy[0]),y=float(xy[1]),z_m=float(grid.elevation(np.asarray(xy)))+agl,
                                   agl_m=agl,t_s=clock,phase=phase,task_id=tid))
            def append(trajectory,phase,tid=None):
                nonlocal clock,distance,transit_length
                length=float(np.linalg.norm(np.diff(trajectory[:,:2],axis=0),axis=1).sum())
                distance+=length
                if phase=='transit':transit_length+=length
                for x,y,z,t,h in trajectory:
                    points.append(dict(x=float(x),y=float(y),z_m=float(z),agl_m=float(h),
                                       t_s=clock+float(t),phase=phase,task_id=tid))
                clock+=float(trajectory[-1,3])
            def transfer(target):
                nonlocal current
                leg=self.leg(uid,current,target)
                if not math.isfinite(leg['time_s']):raise ValueError('no grid transfer path')
                coords=leg['coords'];length=float(np.linalg.norm(np.diff(coords,axis=0),axis=1).sum())
                trajectory=motion(coords,leg['heights'],length/u['ground_speed_ms'],self.settings,grid.elevation)
                actual=float(trajectory[-1,3])
                if actual>leg['time_s']+1e-6:
                    overruns.append(dict(uav_id=uid,estimated_s=leg['time_s'],actual_s=actual))
                elif actual>0:
                    trajectory[:,3]*=leg['time_s']/actual
                append(trajectory,'transit');current=target
            point(current[:2],0.,'takeoff')
            clock+=max(u['takeoff_time_s'],u['cruise_agl_m']/self.settings.vertical_speed_ms)
            point(current[:2],u['cruise_agl_m'],'takeoff')
            for tid,variant,reverse in zip(estimated['task_ids'],estimated['task_variants'],estimated['task_reversed']):
                task=self.task(uid,tid,variant,reverse)
                if task is None or not task['valid']:raise ValueError(f'invalid task variant {tid}')
                transfer(task['entry']);begin=clock
                append(task['points'],'survey',tid);current=task['exit']
                survey_length+=self.tasks[tid]['survey_length_m']
                task_times.append(dict(task_id=tid,start_s=begin,end_s=clock))
            transfer((land['x'],land['y'],u['cruise_agl_m']))
            point(current[:2],u['cruise_agl_m'],'landing')
            clock+=max(u['landing_time_s'],u['cruise_agl_m']/self.settings.vertical_speed_ms)
            point(current[:2],0.,'landing')
            index=sum(s['uav_id']==uid for s in output)+1
            usable=u['operational_endurance_min']*60*(1-u['energy_reserve_fraction'])
            output.append(dict(id=f'{uid}_S{index}',index=index,uav_id=uid,start_site_id=start['id'],landing_site_id=land['id'],
                task_ids=estimated['task_ids'],task_variants=estimated['task_variants'],task_reversed=estimated['task_reversed'],
                start_s=depart,end_s=clock,flight_time_s=clock-depart,waypoints=points,task_times=task_times,
                resource_margin_s=usable-(clock-depart),distance_m=distance,transit_length_m=transit_length,survey_length_m=survey_length))
            ready[uid]=clock+u['service_time_s']
        assigned={tid for s in output for tid in s['task_ids']}
        result=dict(schema_version='h2.plan.v1',scene_id=self.bundle['scene_id'],input_sha256=fingerprint(self.bundle),
                    input_schema_version=self.bundle['schema_version'],crs=self.bundle['crs'],time_origin=self.origin.isoformat(),
                    tasks=deepcopy(self.bundle['tasks']),sorties=output,objective=self.settings.objective,
                    unassigned=[dict(task_id=tid,reason='omitted from fixed plan') for tid in self.tasks if tid not in assigned],
                    metrics=metrics(output),solver_log=[],deconfliction={'remaining':[]},requires_h3_validation=True,certificate=None,
                    assumptions=['Fixed plan, terrain grid transit, conservative estimated timing; H3 validation required'],
                    estimate_overruns=overruns)
        result['checks']=(check_plan(self.bundle,result,self.settings,self.scene) if check else
                          {'passed':False,'violations':[],'pending':True})
        result['status']='FEASIBLE' if result['checks']['passed'] and not overruns and estimate['status']=='ESTIMATED_FEASIBLE' else 'UNRESOLVED'
        result['materialization_s']=time.perf_counter()-began
        return result


def _finite(value):
    return float(value) if math.isfinite(value) else None


def _clean(value):
    if isinstance(value,dict):return {k:_clean(v) for k,v in value.items()}
    if isinstance(value,list):return [_clean(v) for v in value]
    if isinstance(value,(float,np.floating)):return _finite(value)
    return value
