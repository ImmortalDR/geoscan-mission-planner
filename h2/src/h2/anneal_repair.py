"""Small ground delays with service propagation and continuous conflict checks."""
from copy import deepcopy

from shapely.geometry import LineString, Point, shape
from .algorithms.conflict_detection import detect_conflicts
from .deconfliction import _shift_times


def shift_departure(sorties, sid, delay, fleet):
    result=list(sorties)
    index=next(i for i,s in enumerate(result) if s['id']==sid)
    uid=result[index]['uav_id'];service=fleet[uid]['service_time_s']
    ordered=sorted((i for i,s in enumerate(result) if s['uav_id']==uid),key=lambda i:(result[i]['start_s'],result[i]['index']))
    active=False;end=None
    for i in ordered:
        if i==index:active=True
        if not active:continue
        delta=delay if i==index else max(0.,end+service-result[i]['start_s'])
        if delta>0:
            result[i]=deepcopy(result[i]);_shift_times(result[i],delta)
        end=result[i]['end_s']
    return result


def within_windows(sorties, horizon, settings):
    for s in sorties:
        if horizon is not None and s['end_s']>horizon+1e-6:return False
        for t in s['task_times']:
            window=settings.task_windows.get(t['task_id'])
            if window and t['start_s']>window[1]+1e-6:return False
    return True


def crossings(sortie, scene):
    """Crossing-segment intervals, matching the conservative full H2 checker.

    Delay only the intersecting portion's sampled segment, not the whole sortie.
    """
    result=[];origin=sortie['start_s']
    for zone in (scene or {}).get('temporal',[]):
        polygon=shape(zone['geometry'])
        for a,b in zip(sortie['waypoints'],sortie['waypoints'][1:]):
            if b['t_s']<=a['t_s']:continue
            aa=(a['x'],a['y']);bb=(b['x'],b['y'])
            geometry=Point(aa) if aa==bb else LineString([aa,bb])
            if geometry.intersects(polygon):
                result.append((a['t_s']-origin,b['t_s']-origin,zone['start_s'],zone['end_s']))
    return tuple(result)


def repair_schedule(sorties, fleet, settings, scene=None, horizon=None, max_rounds=64, max_shift_s=600., max_conflict_rounds=None):
    routes=list(sorties);by_uav={u['id']:u for u in fleet};actions=[]
    conflict_rounds=0
    occupied={s['id']:crossings(s,scene) for s in sorties}
    for _ in range(max_rounds):
        shifted=False
        for s in sorted(routes,key=lambda s:(s['start_s'],s['id'])):
            delta=0.
            for t in s['task_times']:
                window=settings.task_windows.get(t['task_id'])
                if window:delta=max(delta,window[0]-t['start_s'])
            for enter,leave,lo,hi in occupied[s['id']]:
                if s['start_s']+enter<=hi and s['start_s']+leave>=lo:
                    delta=max(delta,hi-(s['start_s']+enter)+.001)
            if delta>0:
                candidate=shift_departure(routes,s['id'],delta,by_uav)
                if not within_windows(candidate,horizon,settings):
                    return routes,detect_conflicts(routes,fleet),actions
                routes=candidate;shifted=True
                actions.append(dict(action='shift_time_window',rung=1,sortie=s['id'],uav=s['uav_id'],delay_s=delta))
                break
        if shifted:continue
        conflicts=detect_conflicts(routes,fleet)
        if not conflicts:return routes,[],actions
        if max_conflict_rounds is not None and conflict_rounds>=max_conflict_rounds:
            return routes,conflicts,actions
        conflict_rounds+=1
        first=conflicts[0];by_id={s['id']:s for s in routes}
        pair=[by_id[first[k]] for k in ('sortie_a','sortie_b')]
        proposals=[]
        for moved,other in (pair,pair[::-1]):
            if moved['uav_id']==other['uav_id']:continue
            last_bad=0.;found=None
            for delta in (1.,2.,4.,8.,12.,16.,24.,32.,48.,64.,96.,128.,192.,256.,384.,512.,768.):
                if delta>max_shift_s:break
                trial=dict(moved,waypoints=[dict(p,t_s=p['t_s']+delta) for p in moved['waypoints']])
                if not detect_conflicts([trial,other],fleet,first_only=True):
                    found=delta;break
                last_bad=delta
            if found is None:continue
            # Refine a witnessed safe delay, always retesting the complete pair.
            for _ in range(7):
                mid=(last_bad+found)/2
                trial=dict(moved,waypoints=[dict(p,t_s=p['t_s']+mid) for p in moved['waypoints']])
                if detect_conflicts([trial,other],fleet,first_only=True):last_bad=mid
                else:found=mid
            delta=found+.001
            candidate=shift_departure(routes,moved['id'],delta,by_uav)
            if not within_windows(candidate,horizon,settings):continue
            finish=max(s['end_s'] for s in candidate)
            delay=sum(a['start_s']-b['start_s'] for a,b in zip(candidate,routes))
            proposals.append((finish if settings.objective=='makespan' else delay,delay,moved['id'],delta,candidate))
        if not proposals:return routes,conflicts,actions
        _,_,sid,delta,routes=min(proposals,key=lambda p:p[:3])
        actions.append(dict(action='shift_conflict',rung=1,sortie=sid,uav=by_id[sid]['uav_id'],delay_s=delta))
    return routes,detect_conflicts(routes,fleet),actions
