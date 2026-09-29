"""Deterministic whole-sweep neighbourhoods over prepared Routing tables."""
from collections import Counter
import math
import time

from .routing_seed import parallel_groups, sequence_route
from .routing_solver import SCALE


def task_sequences(model, routes):
    return [[model.nodes[i].service//2 for i in route if model.nodes[i].kind=='survey'] for route in routes]


def refine_blocks(model, routes, evaluation_limit=300, progress=None):
    """Restore local snakes, relocate and exchange owned groups as a unit.

    Every changed aircraft gets a new orientation/recharge label search. Only
    improving resource-feasible assignments survive. Final H2/H3 checks and
    temporal repair remain authoritative, just as for the Routing candidates.
    Geometry, graph costs, reserves and allowed vehicles are unchanged.
    """
    began=time.perf_counter();m=model;count=len(m.uids)
    groups=parallel_groups(m);family={t:g for g,ids in enumerate(groups) for t in ids}
    rank={t:i for ids in groups for i,t in enumerate(ids)}
    current=[list(r) for r in routes];sequences=task_sequences(m,current)
    expected=Counter(t for seq in sequences for t in seq)
    cache={(v,tuple(seq)):current[v] for v,seq in enumerate(sequences)}
    evaluations=0;proposals=0;accepted=[];kept=[]

    def score(plan):
        elapsed=[sum(int(m.calendar[v][a,b]) for a,b in zip(route,route[1:])) for v,route in enumerate(plan)]
        flight=sum(int(m.flight[v][a,b]) for v,route in enumerate(plan) for a,b in zip(route,route[1:]))
        end=max((elapsed[v]+math.ceil(max(m.g.window_start,getattr(m.g,'ready',{}).get(m.uids[v],0.))*SCALE)
                 for v,route in enumerate(plan) if route),default=0)
        return end*m.span_weight+flight if m.settings.objective=='makespan' else flight

    def validate(plan):
        for v,route in enumerate(plan):
            if not route:continue
            if route[0] not in m.start_choices.get(v,[]) or route[-1] not in m.finish_choices.get(v,[]):return None
            used=0;clock=math.ceil(max(m.g.window_start,getattr(m.g,'ready',{}).get(m.uids[v],0.))*SCALE)
            uid=m.uids[v];home=m.nodes[route[0]].site
            for a,b in zip(route,route[1:]):
                if m.nodes[a].kind=='refuel':used=0
                if not m.valid[v][a,b] or used+int(m.need[v][a,b])>m.cap[v]:return None
                used+=int(m.flight[v][a,b]);clock+=int(m.calendar[v][a,b])
                node=m.nodes[b]
                if node.kind=='survey':
                    window=m.settings.task_windows.get(m.g.tids[node.service//2])
                    if window and clock>window[1]*SCALE:return None
                elif m.g.policy[uid]['same_base'] and node.site!=home:return None
            if clock>m.g.horizon*SCALE:return None
        return score(plan)

    initial_value=validate(current)
    if initial_value is None:return [],dict(evaluations=0,accepted=[],reason='invalid_start',elapsed_s=time.perf_counter()-began)
    value=initial_value

    def eligible(v,block):
        uid=m.uids[v];services=m.g.by_uav[uid]['services']
        return all(uid in m.g.bundle['feasibility']['eligible_uav_ids_by_task'][m.g.tids[t]]
                   and (services[2*t]['valid'] or services[2*t+1]['valid']) for t in block)

    def build(v,seq):
        nonlocal evaluations
        key=v,tuple(seq)
        if key not in cache:
            if evaluations>=evaluation_limit:return None
            evaluations+=1;cache[key]=sequence_route(m,v,seq)
        return cache[key]

    def try_change(changes,operation):
        nonlocal proposals,current,sequences,value
        proposals+=1;candidate=list(current);newseq=list(sequences)
        for v,seq in sorted(changes.items()):
            route=build(v,seq)
            if route is None:return False
            candidate[v]=route;newseq[v]=seq
        if Counter(t for seq in newseq for t in seq)!=expected:raise AssertionError('Block move changed task ownership multiplicity')
        if score(candidate)>=value:return False
        actual=validate(candidate)
        if actual is None or actual>=value:return False
        before=value;current=candidate;sequences=newseq;value=actual
        accepted.append(dict(operation=operation,before=before,after=value))
        kept.append((value,[list(r) for r in current]))
        if len(kept)>4:kept.pop(0)
        if progress:progress(dict(stage='routing_blocks',operation=operation,evaluations=evaluations,accepted=len(accepted)))
        return True

    def blocks(v):
        # One owned portion of a parallel family, even if a previous search
        # interleaved its lines with visits elsewhere.
        out={}
        for t in sequences[v]:out.setdefault(family[t],[]).append(t)
        return [(g,sorted(ids,key=rank.get)) for g,ids in out.items()]

    def replace(v,removed,added):
        excluded=set(removed);seq=sequences[v]
        first=next((i for i,t in enumerate(seq) if t in excluded),len(seq))
        return [t for t in seq[:first] if t not in excluded]+list(added)+[t for t in seq[first:] if t not in excluded]

    def insertions(v,block):
        seq=sequences[v]
        boundaries=[0]+[i for i in range(1,len(seq)) if family[seq[i-1]]!=family[seq[i]]]+([len(seq)] if seq else [])
        first=m.g.tasks[m.g.tids[block[0]]]
        def distance(i):
            if i==0:
                choices=m.start_choices.get(v,[])
                return min((math.dist(m.g.vertices[m.nodes[b].physical]['xy'],first['entry']) for b in choices),default=math.inf)
            prior=m.g.tasks[m.g.tids[seq[i-1]]]
            return min(math.dist(a,b) for a in (prior['entry'],prior['exit']) for b in (first['entry'],first['exit']))
        return sorted(set(boundaries),key=lambda i:(distance(i),i))[:3]

    # Alternate the neighbourhoods: a transfer can make a new snake possible,
    # and a swap can free resource for another transfer. Fixed operation counts
    # preserve reproducibility independent of host speed.
    for _ in range(3):
        changed=False
        for v in range(count):
            for _,block in blocks(v):
                for oriented in (block,block[::-1]):
                    changed=try_change({v:replace(v,block,oriented)},'snake_order') or changed
                    if evaluations>=evaluation_limit:break
                if evaluations>=evaluation_limit:break
            if evaluations>=evaluation_limit:break
        if evaluations>=evaluation_limit:break
        # Prioritise the longest aircraft route for makespan improvements.
        owners=sorted(range(count),key=lambda v:(-sum(int(m.calendar[v][a,b]) for a,b in zip(current[v],current[v][1:])),v))
        moved=False
        for v in owners:
            for _,block in blocks(v):
                for w in range(count):
                    if w==v or not eligible(w,block):continue
                    remaining=replace(v,block,[])
                    for oriented in (block,block[::-1]):
                        for at in insertions(w,oriented):
                            if try_change({v:remaining,w:sequences[w][:at]+oriented+sequences[w][at:]},'move_snake'):
                                changed=moved=True;break
                            if evaluations>=evaluation_limit:break
                        if moved or evaluations>=evaluation_limit:break
                    if moved or evaluations>=evaluation_limit:break
                if moved or evaluations>=evaluation_limit:break
            if moved or evaluations>=evaluation_limit:break
        if evaluations>=evaluation_limit:break
        swapped=False
        for v in owners:
            for w in range(v+1,count):
                for _,a in blocks(v):
                    if not eligible(w,a):continue
                    for _,b in blocks(w):
                        if not eligible(v,b):continue
                        for aa in (a,a[::-1]):
                            for bb in (b,b[::-1]):
                                if try_change({v:replace(v,a,bb),w:replace(w,b,aa)},'swap_snakes'):
                                    changed=swapped=True;break
                                if evaluations>=evaluation_limit:break
                            if swapped or evaluations>=evaluation_limit:break
                        if swapped or evaluations>=evaluation_limit:break
                    if swapped or evaluations>=evaluation_limit:break
                if swapped or evaluations>=evaluation_limit:break
            if swapped or evaluations>=evaluation_limit:break
        if not changed or evaluations>=evaluation_limit:break
    return kept,dict(evaluations=evaluations,proposals=proposals,evaluation_limit=evaluation_limit,
                     accepted=accepted,initial_cost=initial_value,final_cost=value,elapsed_s=time.perf_counter()-began)
