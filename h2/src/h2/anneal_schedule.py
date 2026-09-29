"""Fast, resource-constrained splitting of ordered visits into serviced sorties."""
from dataclasses import dataclass
from functools import lru_cache
import math


@dataclass(frozen=True)
class Fragment:
    start: str
    land: str
    visits: tuple
    duration: float
    required: float
    windows: tuple
    temporal: tuple


@dataclass(frozen=True)
class Label:
    missing: int = 0
    flight: float = 0.
    finish: float = 0.
    path: tuple = ()
    omitted: tuple = ()


class SequenceEvaluator:
    """Dynamic programming over (next task, current base), with Pareto labels.

    Labels trade flight time against completion time to respect time windows.
    A bounded frontier limits difficult multi-base instances. Skipped tasks stay
    explicit and dominate the objective penalty; they are never called complete.
    """
    def __init__(self, estimator, label_limit=8):
        self.e = estimator
        self.uids = tuple(sorted(estimator.fleet))
        self.label_limit = label_limit
        self.calls = 0
        self.options = {}
        for uid in self.uids:
            for tid in sorted(estimator.tasks):
                self.options[uid, tid] = tuple((tid, v, r) for t, v, r in estimator.by_uav[uid]['tasks']
                    if t == tid and uid in estimator.eligible[tid] and estimator.task(uid, t, v, r)['valid'])
        # A complete plan always ranks ahead of a partial plan, regardless of time.
        self.penalty = 1e6 + sum(u['operational_endurance_min']*60 for u in estimator.fleet.values())*max(1,len(estimator.tasks))

    def site_point(self, uid, sid):
        s=self.e.sites[sid]
        return (s['x'],s['y'],self.e.fleet[uid]['cruise_agl_m'])

    @lru_cache(maxsize=12000)
    def fragments(self, uid, visits, base):
        e=self.e;u=e.fleet[uid];w=e.settings.vertical_speed_ms
        if e.sites[base]['role'] not in ('both','start') or e.bundle['mission']['wind']['speed_ms']>u['max_wind_ms']:
            return ()
        usable=u['operational_endurance_min']*60*(1-u['energy_reserve_fraction'])
        lands=[s for s in sorted(e.sites) if e.sites[s]['role'] in ('both','landing','reserve')
               and (u['landing_site'] is None or s==u['landing_site'])
               and (e.bundle['mission']['allow_different_start_end'] or s==base)]
        elapsed=max(u['takeoff_time_s'],u['cruise_agl_m']/w)
        required=elapsed;current=self.site_point(uid,base);windows=[];temporal=[];out=[]
        for i,visit in enumerate(visits):
            if uid not in e.eligible.get(visit[0],[]):break
            row=e.task(uid,*visit)
            if not row or not row['valid']:break
            leg=e.leg(uid,current,row['entry'])
            required=max(required,elapsed+leg['required_start_s']);elapsed+=leg['time_s']
            required=max(required,elapsed+row['required_start_s'])
            begin=elapsed;elapsed+=row['time_s'];current=row['exit']
            if not math.isfinite(required) or required>usable+1e-6:break
            window=e.settings.task_windows.get(visit[0])
            if window:windows.append((window[0]-begin,window[1]-begin))
            temporal.extend((z['start_s']-elapsed,z['end_s']-begin) for z in row['temporal'])
            for land in lands:
                back=e.leg(uid,current,self.site_point(uid,land))
                duration=elapsed+back['time_s']+max(u['landing_time_s'],u['cruise_agl_m']/w)
                reserve=max(required,elapsed+back['required_start_s'],duration)
                if reserve<=usable+1e-6:
                    out.append(Fragment(base,land,visits[:i+1],duration,reserve,tuple(windows),tuple(temporal)))
        return tuple(out)

    @staticmethod
    def departure(fragment, ready):
        depart=max(ready,max((lo for lo,hi in fragment.windows),default=0.))
        for lo,hi in sorted(fragment.temporal):
            if lo<=depart<=hi:depart=hi+.001
        if any(depart>hi+1e-6 for lo,hi in fragment.windows):return math.inf
        return depart

    def rank(self, label):
        objective=label.finish if self.e.settings.objective=='makespan' else label.flight
        return (label.missing,objective,label.finish,label.flight)

    def add_label(self, cells, key, value):
        labels=cells.setdefault(key,[])
        def dominates(a,b):
            return a.missing<=b.missing and a.flight<=b.flight+1e-8 and a.finish<=b.finish+1e-8
        if any(dominates(a,value) for a in labels):return
        labels[:]=[a for a in labels if not dominates(value,a)]
        labels.append(value);labels.sort(key=self.rank)
        del labels[self.label_limit:]

    @lru_cache(maxsize=3000)
    def sequence(self, uid, visits):
        self.calls+=1
        u=self.e.fleet[uid];n=len(visits)
        starts=[s for s in sorted(self.e.sites) if self.e.sites[s]['role'] in ('both','start')
                and (u['start_site'] is None or u['start_site']==s)]
        if not starts:return Label(missing=n,omitted=tuple(v[0] for v in visits))
        cells={(0,s):[Label()] for s in starts}
        for i in range(n):
            for base in sorted(self.e.sites):
                labels=cells.get((i,base),())
                if not labels:continue
                pieces=self.fragments(uid,visits[i:],base)
                for label in tuple(labels):
                    self.add_label(cells,(i+1,base),Label(label.missing+1,label.flight,label.finish,
                        label.path,label.omitted+(visits[i][0],)))
                    ready=label.finish+u['service_time_s'] if label.path else self.e.window_start_s
                    for piece in pieces:
                        depart=self.departure(piece,ready);end=depart+piece.duration
                        if not math.isfinite(end) or (self.e.horizon is not None and end>self.e.horizon+1e-6):continue
                        self.add_label(cells,(i+len(piece.visits),piece.land),Label(label.missing,
                            label.flight+piece.duration,end,label.path+((piece,depart),),label.omitted))
        return min((label for (i,base),labels in cells.items() if i==n for label in labels),key=self.rank)

    def evaluate(self, state):
        labels=tuple(self.sequence(uid,seq) for uid,seq in zip(self.uids,state))
        absent=len(self.e.tasks)-sum(len(seq) for seq in state)
        missing=absent+sum(label.missing for label in labels)
        duration=max((label.finish for label in labels),default=0.)
        flight=sum(label.flight for label in labels)
        objective=duration if self.e.settings.objective=='makespan' else flight
        return dict(labels=labels,missing=missing,objective=objective,
                    energy=missing*self.penalty+objective,makespan_s=duration,total_flight_s=flight)

    def fixed_plan(self, score):
        sorties=[]
        for uid,label in zip(self.uids,score['labels']):
            for piece,depart in label.path:
                sorties.append(dict(uav_id=uid,start_site_id=piece.start,landing_site_id=piece.land,
                    start_s=depart,task_ids=[v[0] for v in piece.visits],task_variants=[v[1] for v in piece.visits],
                    task_reversed=[v[2] for v in piece.visits]))
        return dict(schema_version='h2.fixed_plan.v1',sorties=sorties)
