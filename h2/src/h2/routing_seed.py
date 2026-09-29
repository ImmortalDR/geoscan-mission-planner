"""Geographic sweep seed with table-only orientation/recharge label search."""
from collections import defaultdict
import math

import numpy as np

from .routing_solver import SCALE


def sequence_route(model, vehicle, tasks):
    """Label = arrival time, consumed resource, flight time, logical route.

    Survey is charged on departure, exactly as in Routing. Labels may recharge
    between lines; full service and physical movement come from the same tables.
    The bounded Pareto frontier affects seed quality only, never feasibility.
    """
    m=model;v=vehicle;uid=m.uids[v];cap=m.cap[v]
    limit=m.g.horizon*SCALE
    ready=max(m.g.window_start,getattr(m.g,'ready',{}).get(uid,0.))
    labels=[(math.ceil(ready*SCALE),0,0,(i,)) for i in m.start_choices.get(v,[])]
    if not tasks:return []
    def arrivals(label,target):
        now,used,flight,path=label;last=path[-1]
        if m.valid[v][last,target] and used+m.need[v][last,target]<=cap:
            yield now+int(m.calendar[v][last,target]),used+int(m.flight[v][last,target]),flight+int(m.flight[v][last,target]),path+(target,)
        offered=set()
        for base in m.reloads.get(v,[]):
            node=m.nodes[base]
            if base in path or node.site in offered:continue
            offered.add(node.site)
            if m.g.policy[uid]['same_base'] and node.site!=m.nodes[path[0]].site:continue
            if (m.valid[v][last,base] and m.valid[v][base,target] and used+m.need[v][last,base]<=cap):
                yield (now+int(m.calendar[v][last,base])+int(m.calendar[v][base,target]),
                       int(m.flight[v][base,target]),flight+int(m.flight[v][last,base])+int(m.flight[v][base,target]),path+(base,target))
    for task in tasks:
        pool=defaultdict(list)
        for label in labels:
            for target in (2*task,2*task+1):
                for item in arrivals(label,target):
                    if item[0]>limit:continue
                    pool[(target,m.nodes[item[3][0]].site)].append(item)
        labels=[]
        for key,items in pool.items():
            front=[]
            # Keep independent time/resource/flight trade-offs. Deterministic
            # truncation stops pathological frontiers during seed construction.
            for item in sorted(items,key=lambda x:(x[0],x[1],x[2],x[3])):
                if any(a[0]<=item[0] and a[1]<=item[1] and a[2]<=item[2] for a in front):continue
                front.append(item)
            if len(front)>24:
                front=sorted(front,key=lambda x:(x[0]+x[1],x[0],x[3]))[:24]
            labels.extend(front)
        if not labels:return None
    results=[]
    for label in labels:
        for end in m.finish_choices.get(v,[]):
            if m.g.policy[uid]['same_base'] and m.nodes[end].site!=m.nodes[label[3][0]].site:continue
            results.extend(x for x in arrivals(label,end) if x[0]<=limit)
    if not results:return None
    key=(lambda x:(x[0],x[2],x[3])) if m.settings.objective=='makespan' else (lambda x:(x[2],x[0],x[3]))
    return list(min(results,key=key)[3])


def parallel_groups(model, reverse=False):
    """Ordered neighbouring transects, including H1 chunks of the same sweep."""
    groups=defaultdict(list)
    for i,tid in enumerate(model.g.tids):
        t=model.g.tasks[tid]
        vector=np.asarray(t['exit'])-t['entry']
        yaw=math.atan2(vector[1],vector[0])%math.pi
        if math.isclose(yaw,math.pi,abs_tol=1e-6):yaw=0.
        key=(t['job_id'],t['payload_profile_id'],round(t['agl_m'],6),round(yaw,6))
        groups[key].append(i)
    result=[]
    for key,ids in sorted(groups.items()):
        across=np.array([-math.sin(key[-1]),math.cos(key[-1])])
        def position(i):
            t=model.g.tasks[model.g.tids[i]]
            return (float((np.asarray(t['entry'])+t['exit'])@across),i)
        result.append(sorted(ids,key=position,reverse=reverse))
    return result


def solo_costs(model):
    if hasattr(model,'_seed_solo'):return model._seed_solo
    m=model;count=len(m.g.tasks);vehicle_count=len(m.uids)
    solo=np.full((vehicle_count,count),np.inf)
    for v in range(vehicle_count):
        origins=list({m.nodes[i].site:i for i in m.start_choices.get(v,[])+m.reloads.get(v,[])}.values())
        ends=list({m.nodes[i].site:i for i in m.finish_choices.get(v,[])+m.reloads.get(v,[])}.values())
        for task in range(count):
            for a in origins:
                for end in ends:
                    for j in (2*task,2*task+1):
                        if (m.valid[v][a,j] and m.valid[v][j,end] and m.flight[v][a,j]+m.need[v][j,end]<=m.cap[v]):
                            solo[v,task]=min(solo[v,task],int(m.flight[v][a,j])+int(m.flight[v][j,end]))
    m._seed_solo=solo
    return solo


def sweep_routes(model, reverse=False, split=False):
    """Assign whole sweeps or contiguous strips before choosing orientations.

    Load estimates only construct a seed. sequence_route and Routing validate
    actual costs, sensor permissions, recharge and return-resource constraints.
    No contiguity constraint is imposed on the subsequent optimizer.
    """
    m=model;vehicle_count=len(m.uids);solo=solo_costs(m)
    available=np.isfinite(solo).sum(axis=0)
    missing=[i for i,n in enumerate(available) if not n]
    work=np.array([[p.get('time_s',0.)*SCALE for p in m.g.by_uav[uid]['services'][::2]] for uid in m.uids])
    owned=[[] for _ in m.uids];load=[0.]*vehicle_count
    groups=parallel_groups(m,reverse)
    # Scarce groups first, but never alternate individual lines to balance load.
    groups.sort(key=lambda ids:(min(available[i] for i in ids),-sum(float(np.min(solo[:,i])) for i in ids if available[i]),tuple(ids)))
    for group in groups:
        ids=[i for i in group if available[i]]
        if not ids:continue
        common=[v for v in range(vehicle_count) if all(math.isfinite(solo[v,i]) for i in ids)]
        if common and not split:
            def score(v):
                extra=sum(work[v,i] for i in ids)+min(solo[v,i]-work[v,i] for i in ids)
                finish=load[v]+extra
                primary=max(finish,max((load[w] for w in range(vehicle_count) if w!=v),default=0.)) if m.settings.objective=='makespan' else extra
                return primary,finish,v
            v=min(common,key=score)
            owned[v].append(ids);load[v]+=sum(work[v,i] for i in ids)
            continue
        participants=[v for v in range(vehicle_count) if any(math.isfinite(solo[v,i]) for i in ids)]
        target=(sum(load[v] for v in participants)+sum(min(work[v,i] for v in participants if math.isfinite(solo[v,i])) for i in ids))/len(participants)
        current=None;retired=set();block=[]
        for i in ids:
            choices=[v for v in participants if math.isfinite(solo[v,i])]
            unused=[v for v in choices if v not in retired and v!=current]
            switch=current not in choices or (unused and load[current]+work[current,i]>target+1e-6)
            if switch:
                if current is not None:
                    owned[current].append(block);retired.add(current)
                # Return to a previous aircraft only when eligibility leaves no
                # unused choice. This fallback keeps explicit sensor exclusions.
                candidates=unused or choices
                current=min(candidates,key=lambda v:(load[v]+work[v,i],solo[v,i],v));block=[]
            block.append(i);load[current]+=work[current,i]
        if block:owned[current].append(block)
    routes=[]
    for v,sequences in enumerate(owned):
        current=np.asarray(m.g.vertices[m.nodes[m.start_choices[v][0]].physical]['xy']) if m.start_choices.get(v) else np.zeros(2)
        ordered=[]
        while sequences:
            def distance(seq):
                t=m.g.tasks[m.g.tids[seq[0]]]
                return min(math.dist(current,t['entry']),math.dist(current,t['exit']))
            chosen=min(sequences,key=lambda seq:(distance(seq),seq));sequences.remove(chosen)
            ordered.extend(chosen);t=m.g.tasks[m.g.tids[chosen[-1]]];current=(np.asarray(t['entry'])+t['exit'])/2
        route=sequence_route(m,v,ordered)
        if route is None:return None,missing
        routes.append(route)
    return routes,missing
