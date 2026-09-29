"""OR-Tools Routing: oriented mandatory transects and physical UAV reloads."""
from dataclasses import dataclass
import math
import time
import weakref

import numpy as np
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from ortools.util import optional_boolean_pb2

SCALE = 10
INF = 10**9


@dataclass
class ImprovementLimit:
    """Deterministic patience after a feasible incumbent, measured in table reads."""
    patience: int
    best_cost: int | None = None
    last_improvement_read: int = 0

    def observe(self, cost, reads):
        if self.best_cost is None or cost < self.best_cost:
            self.best_cost = cost
            self.last_improvement_read = reads

    def reached(self, reads):
        return self.best_cost is not None and reads-self.last_improvement_read >= self.patience


@dataclass(frozen=True)
class Visit:
    kind: str
    physical: int = -1
    service: int = -1
    vehicle: int = -1
    site: str = ''
    copy: int = 0


class RoutingModel:
    def __init__(self, graph, settings, progress=None, copy_multiplier=1, diagnostic=False):
        self.g,self.settings,self.progress=graph,settings,progress
        self.diagnostic=diagnostic
        self.uids=list(graph.fleet)
        self.nodes=[Visit('survey',service=i) for i in range(2*len(graph.tasks))]
        self.start_choices={};self.finish_choices={};self.reloads={}
        self.cap=[math.floor(graph.fleet[uid]['operational_endurance_min']*60*(1-graph.fleet[uid]['energy_reserve_fraction'])*SCALE) for uid in self.uids]
        self.copies={}
        for v,uid in enumerate(self.uids):
            profile=graph.by_uav[uid];policy=graph.policy[uid]
            work=sum(row.get('time_s',0.) for row in profile['services'][::2])
            compatible=max(1,sum(uid in x for x in graph.bundle['feasibility']['eligible_uav_ids_by_task'].values()))
            copies=min(compatible,max(2,math.ceil(work/max(self.cap[v]/SCALE*.7,1))+2)*copy_multiplier)
            self.copies[uid]=copies
            for kind,choices in (('start_base',policy['start']),('finish_base',policy['finish']),('refuel',policy['refuel'])):
                for sid in choices:
                    for number in range(copies if kind=='refuel' else 1):
                        i=len(self.nodes)
                        self.nodes.append(Visit(kind,graph.bases[sid],vehicle=v,site=sid,copy=number))
                        target=self.reloads if kind=='refuel' else self.start_choices if kind=='start_base' else self.finish_choices
                        target.setdefault(v,[]).append(i)
        self.starts=[];self.ends=[]
        for v in range(len(self.uids)):
            self.starts.append(len(self.nodes));self.nodes.append(Visit('start',vehicle=v))
            self.ends.append(len(self.nodes));self.nodes.append(Visit('end',vehicle=v))
        self.n=len(self.nodes)
        self._tables()
        self.manager=pywrapcp.RoutingIndexManager(self.n,len(self.uids),self.starts,self.ends)
        self.routing=pywrapcp.RoutingModel(self.manager)
        self.solver=self.routing.solver();self.solver.ReSeed(settings.seed)
        self._constraints()

    def _tables(self):
        n=self.n;g=self.g
        self.flight=[];self.calendar=[];self.need=[];self.energy=[];self.valid=[]
        self.departure_turn=[];self.arrival_turn=[]
        for v,uid in enumerate(self.uids):
            p=g.by_uav[uid];policy=g.policy[uid]
            entry=np.zeros(n,dtype=int);exit_=entry.copy();service=np.zeros(n);qservice=np.zeros(n)
            start_heading=np.zeros(n);end_heading=np.zeros(n);survey_node=np.zeros(n,dtype=bool)
            valid_node=np.ones(n,dtype=bool)
            for i,node in enumerate(self.nodes):
                if node.kind=='survey':
                    row=p['services'][node.service]
                    entry[i],exit_[i]=row['entry'],row['exit']
                    valid_node[i]=row['valid'] and uid in g.bundle['feasibility']['eligible_uav_ids_by_task'][row['task_id']]
                    service[i]=row.get('time_s',0.);qservice[i]=row.get('required_s',math.inf)
                    from .routing_graph import heading
                    coords=g.tasks[row['task_id']]['geom_coords']
                    if row['reverse']:coords=coords[::-1]
                    start_heading[i]=heading(coords);end_heading[i]=heading(coords,True);survey_node[i]=True
                elif node.physical>=0:entry[i]=exit_[i]=node.physical
                if node.vehicle not in (-1,v):valid_node[i]=False
            transfer=p['dist'][exit_[:,None],entry[None,:]]
            from .routing_graph import angle
            departure=p['turn_seconds']*angle(end_heading[:,None],p['first_heading'][exit_[:,None],entry[None,:]])*survey_node[:,None]
            arrival=p['turn_seconds']*angle(p['last_heading'][exit_[:,None],entry[None,:]],start_heading[None,:])*survey_node[None,:]
            same=exit_[:,None]==entry[None,:]
            departure[same]=0.
            combined=p['turn_seconds']*angle(end_heading[:,None],start_heading[None,:])*survey_node[:,None]*survey_node[None,:]
            arrival[same]=combined[same]
            self.departure_turn.append(departure);self.arrival_turn.append(arrival)
            flight=service[:,None]+transfer
            need=np.maximum(qservice[:,None],service[:,None]+p['q'][exit_[:,None],entry[None,:]])
            allowed=valid_node[:,None]&valid_node[None,:]&np.isfinite(flight)&np.isfinite(need)
            calendar=flight.copy()
            for i,node in enumerate(self.nodes):
                if node.kind in ('start_base','refuel'):
                    flight[i]+=p['takeoff'];need[i]+=p['takeoff'];calendar[i]+=p['takeoff']
                    if node.kind=='refuel':calendar[i]+=g.fleet[uid]['service_time_s']
                if node.kind in ('refuel','finish_base'):
                    flight[:,i]+=p['landing'];calendar[:,i]+=p['landing']
                    need[:,i]=np.maximum(need[:,i],flight[:,i])
            for i,node in enumerate(self.nodes):
                if node.kind=='start':
                    allowed[i,:]=False
                    if node.vehicle==v:
                        for j in self.start_choices.get(v,[]):allowed[i,j]=True;flight[i,j]=calendar[i,j]=need[i,j]=0.
                        j=self.ends[v];allowed[i,j]=True;flight[i,j]=calendar[i,j]=need[i,j]=0.
                elif node.kind=='finish_base':
                    allowed[i,:]=False
                    if node.vehicle==v:
                        j=self.ends[v];allowed[i,j]=True;flight[i,j]=calendar[i,j]=need[i,j]=0.
                elif node.kind=='end':allowed[i,:]=False
                else:
                    for j,dest in enumerate(self.nodes):
                        if dest.kind in ('start','start_base','end') or i==j:
                            allowed[i,j]=False
                        if node.kind=='survey' and dest.kind=='survey' and node.service//2==dest.service//2:
                            allowed[i,j]=False
                        if node.kind in ('start_base','refuel') and dest.kind in ('refuel','finish_base'):
                            # No service-free ferries between real bases. An
                            # unused UAV uses its administrative start->end arc.
                            allowed[i,j]=False
            # An arc cannot start even from full charge if its escape threshold
            # exceeds usable capacity. No finite "penalty" permits such an arc.
            allowed &= need*SCALE<=self.cap[v]+1e-6
            def ticks(a):return np.ceil(np.nan_to_num(a,nan=INF/SCALE,posinf=INF/SCALE)*SCALE-1e-7).clip(0,INF).astype(np.int64)
            f=ticks(flight);c=ticks(calendar);q=ticks(need)
            f[~allowed]=c[~allowed]=q[~allowed]=INF
            e=f.copy()
            for i,node in enumerate(self.nodes):
                if node.kind=='refuel':e[i]-=self.cap[v]
            self.flight.append(f);self.calendar.append(c);self.need.append(q);self.energy.append(e);self.valid.append(allowed)

    def _constraints(self):
        r,m,solver=self.routing,self.manager,self.solver
        indices=m.GetNumberOfIndices();node_of=[m.IndexToNode(i) for i in range(indices)]
        horizon=math.floor(self.g.horizon*SCALE)
        # Lexicographic tie-break: with the same mission completion time, avoid
        # needless flying by aircraft that finish before the critical route.
        self.span_weight=horizon*len(self.uids)+1
        time_callbacks=[];energy_callbacks=[]
        self.callbacks=[]
        self.table_reads=[0]
        reads=self.table_reads
        for v in range(len(self.uids)):
            def callback(a,b,table=self.calendar[v]):
                reads[0]+=1
                return int(table[node_of[a],node_of[b]])
            def energy_callback(a,b,table=self.energy[v]):
                reads[0]+=1
                return int(table[node_of[a],node_of[b]])
            def cost(a,b,table=self.flight[v]):
                reads[0]+=1
                return int(table[node_of[a],node_of[b]])
            self.callbacks.extend((callback,energy_callback,cost))
            time_callbacks.append(r.RegisterTransitCallback(callback))
            energy_callbacks.append(r.RegisterTransitCallback(energy_callback))
            r.SetArcCostEvaluatorOfVehicle(r.RegisterTransitCallback(cost),v)
        r.AddDimensionWithVehicleTransits(time_callbacks,horizon,horizon,True,'Time')
        r.AddDimensionWithVehicleTransitAndCapacity(energy_callbacks,max(self.cap),self.cap,True,'Fuel')
        self.time=r.GetDimensionOrDie('Time');self.fuel=r.GetDimensionOrDie('Fuel')
        if self.settings.objective=='makespan':self.time.SetGlobalSpanCostCoefficient(self.span_weight)
        for v in range(len(self.uids)):
            self.time.SlackVar(r.Start(v)).SetValue(0)
            self.fuel.SlackVar(r.Start(v)).SetValue(0)
            r.AddVariableMinimizedByFinalizer(self.time.CumulVar(r.End(v)))
        # A constant home dimension ties all actual bases for legacy same-base
        # inputs, without constraining explicit new refuel policies.
        zero=r.RegisterTransitCallback(lambda a,b:0)
        r.AddDimension(zero,0,len(self.g.bases),False,'Home')
        home=r.GetDimensionOrDie('Home');base_number={sid:i+1 for i,sid in enumerate(self.g.bases)}
        for i,node in enumerate(self.nodes):
            if node.kind in ('start','end'):continue
            index=m.NodeToIndex(i)
            if node.kind=='survey':
                permitted=[v for v,uid in enumerate(self.uids) if self.g.by_uav[uid]['services'][node.service]['valid']
                           and uid in self.g.bundle['feasibility']['eligible_uav_ids_by_task'][self.g.tids[node.service//2]]]
                if permitted:r.SetAllowedVehiclesForIndex(permitted,index)
                else:r.ActiveVar(index).SetValue(0)
                self.time.SlackVar(index).SetValue(0)
                self.fuel.SlackVar(index).SetValue(0)
                tid=self.g.tids[node.service//2]
                window=self.settings.task_windows.get(tid)
                if window:
                    lo=max(0,math.ceil(window[0]*SCALE));hi=min(horizon,math.floor(window[1]*SCALE))
                    if lo>hi:r.ActiveVar(index).SetValue(0)
                    else:self.time.CumulVar(index).SetRange(lo,hi)
                # All survey occupancy must avoid intersecting temporal zones.
                from shapely.geometry import LineString,shape
                for zone in (self.g.scene or {}).get('temporal',[]):
                    if LineString(self.g.tasks[tid]['geom_coords']).intersects(shape(zone['geometry'])):
                        duration=max((self.g.by_uav[self.uids[v]]['services'][node.service].get('time_s',0.) for v in permitted),default=0.)
                        self.time.CumulVar(index).RemoveInterval(max(0,math.floor((zone['start_s']-duration)*SCALE)),math.ceil(zone['end_s']*SCALE))
            else:
                r.AddDisjunction([index],0)
                r.SetAllowedVehiclesForIndex([node.vehicle],index)
                if node.kind=='refuel':
                    # Full recharge: E_next=E_i-B+flight+(B-E_i)=flight.
                    solver.Add(self.fuel.SlackVar(index)+self.fuel.CumulVar(index)==self.cap[node.vehicle])
                else:self.fuel.SlackVar(index).SetValue(0)
                if node.kind=='finish_base':self.time.SlackVar(index).SetValue(0)
                if node.kind=='start_base':
                    ready=max(self.g.window_start,getattr(self.g,'ready',{}).get(self.uids[node.vehicle],0.))
                    self.time.SlackVar(index).SetMin(min(horizon,math.ceil(ready*SCALE)))
                    if ready>self.g.horizon:r.ActiveVar(index).SetValue(0)
                uid=self.uids[node.vehicle]
                if self.g.policy[uid]['same_base']:
                    solver.Add(solver.IsEqualCstVar(home.CumulVar(index),base_number[node.site])>=r.ActiveVar(index))
            # Q and permission are table constraints conditional on physical UAV
            # and the chosen successor. Inactive visits get the neutral row.
            nextvar=r.NextVar(index);vehicle=r.VehicleVar(index)
            rows=[0]*indices
            for v in range(len(self.uids)):
                rows.extend(min(int(self.need[v][i,j]),self.cap[v]+1) for j in node_of)
            need=solver.Element(rows,(vehicle+1)*indices+nextvar)
            capacities=solver.Element([0]+self.cap,vehicle+1)
            consumed=0 if node.kind=='refuel' else self.fuel.CumulVar(index)
            solver.Add(consumed+need<=capacities)
            # Remove arcs unavailable for every compatible vehicle.
            permitted_next=[j for j in range(indices) if j!=index and not r.IsStart(j)
                            and any(table[i,node_of[j]] for table in self.valid)]
            nextvar.SetValues(sorted(set(permitted_next+[index])))
        for task in range(len(self.g.tasks)):
            max_cost=horizon*len(self.uids)
            if self.settings.objective=='makespan':max_cost+=horizon*self.span_weight
            r.AddDisjunction([m.NodeToIndex(2*task),m.NodeToIndex(2*task+1)],
                             (max_cost+1) if self.diagnostic else -1,1)
        for v in range(len(self.uids)):
            r.NextVar(r.Start(v)).SetValues([m.NodeToIndex(i) for i in self.start_choices.get(v,[])]+[r.End(v)])

    def greedy_routes(self):
        """Deterministic geographic seed; all decisions use the prepared tables."""
        count=len(self.g.tasks);remaining=set(range(count));routes=[[] for _ in self.uids]
        # Assign hard-to-reach work first. Sensor compatibility alone is not
        # enough: a short-endurance copter may be unable to do a distant line.
        scarcity=[]
        for task in range(count):
            available=0
            for v in range(len(self.uids)):
                origins=self.start_choices.get(v,[])+self.reloads.get(v,[])
                destinations=self.finish_choices.get(v,[])+self.reloads.get(v,[])
                # Duplicate recharge copies have identical physical costs.
                origins=list({self.nodes[i].site:i for i in origins}.values())
                destinations=list({self.nodes[i].site:i for i in destinations}.values())
                available+=any(self.valid[v][a,j] and self.valid[v][j,z]
                    and self.flight[v][a,j]+self.need[v][j,z]<=self.cap[v]
                    for a in origins for z in destinations for j in (2*task,2*task+1))
            scarcity.append(available)
        clock=[0]*len(self.uids);used=[0]*len(self.uids);position=[None]*len(self.uids)
        finish_cost=[0]*len(self.uids);flight_total=[0]*len(self.uids)
        while remaining:
            choices=[]
            for v,uid in enumerate(self.uids):
                options=[(position[v],[],clock[v],used[v],flight_total[v])] if position[v] is not None else []
                if position[v] is None:
                    ready=max(self.g.window_start,getattr(self.g,'ready',{}).get(uid,0.))
                    options.extend((base,[base],math.ceil(ready*SCALE),0,0) for base in self.start_choices.get(v,[]))
                else:
                    last=position[v]
                    offered_sites=set()
                    for base in self.reloads.get(v,[]):
                        if base in routes[v] or not self.valid[v][last,base]:continue
                        if self.nodes[base].site in offered_sites:continue
                        offered_sites.add(self.nodes[base].site)
                        if self.g.policy[uid]['same_base'] and self.nodes[base].site!=self.nodes[routes[v][0]].site:continue
                        if used[v]+self.need[v][last,base]>self.cap[v]:continue
                        options.append((base,[base],clock[v]+int(self.calendar[v][last,base]),0,flight_total[v]+int(self.flight[v][last,base])))
                for last,addition,now,fuel,flown in options:
                    for task in sorted(remaining):
                        for target in (2*task,2*task+1):
                            if not self.valid[v][last,target] or fuel+self.need[v][last,target]>self.cap[v]:continue
                            arrival=now+int(self.calendar[v][last,target]);spent=fuel+int(self.flight[v][last,target])
                            endings=[]
                            for end in self.finish_choices.get(v,[]):
                                if self.g.policy[uid]['same_base'] and self.nodes[end].site!=self.nodes[(routes[v]+addition)[0]].site:continue
                                if self.valid[v][target,end] and spent+self.need[v][target,end]<=self.cap[v]:
                                    endings.append((int(self.flight[v][target,end]),end))
                            if not endings:continue
                            tail,end=min(endings);completion=arrival+tail
                            if completion>self.g.horizon*SCALE:continue
                            window=self.settings.task_windows.get(self.g.tids[task])
                            if window and arrival>window[1]*SCALE:continue
                            total=flown+int(self.flight[v][last,target])+tail
                            score=(max(completion,max((x for i,x in enumerate(finish_cost) if i!=v),default=0))
                                   if self.settings.objective=='makespan' else total-flight_total[v])
                            # Start geographically, using a stable secondary score.
                            choices.append((scarcity[task],score,total,v,task,target,addition,arrival,spent,flown+int(self.flight[v][last,target]),completion))
            if not choices:break
            _,_,_,v,task,target,addition,arrival,spent,flown,completion=min(choices,key=lambda x:x[:6])
            routes[v].extend(addition+[target]);position[v]=target;clock[v]=arrival;used[v]=spent
            flight_total[v]=flown;finish_cost[v]=completion;remaining.remove(task)
        for v,uid in enumerate(self.uids):
            if not routes[v]:continue
            last=routes[v][-1]
            endings=[(int(self.flight[v][last,end]),end) for end in self.finish_choices.get(v,[])
                     if self.valid[v][last,end] and used[v]+self.need[v][last,end]<=self.cap[v]
                     and (not self.g.policy[uid]['same_base'] or self.nodes[end].site==self.nodes[routes[v][0]].site)]
            if not endings:return None,[]
            routes[v].append(min(endings)[1])
        return routes,sorted(remaining)

    def solve(self):
        start=time.perf_counter();r,m=self.routing,self.manager
        seed,missing=self.greedy_routes();seed_s=time.perf_counter()-start
        copy_limited=[]
        if seed is not None and missing:
            for v,route in enumerate(seed):
                for sid in self.g.policy[self.uids[v]]['refuel']:
                    if sum(self.nodes[i].kind=='refuel' and self.nodes[i].site==sid for i in route)>=self.copies[self.uids[v]]:
                        copy_limited.append(dict(uav_id=self.uids[v],site_id=sid))
        params=pywrapcp.DefaultRoutingSearchParameters()
        params.first_solution_strategy=routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
        params.local_search_metaheuristic=routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        params.use_cp_sat=optional_boolean_pb2.BOOL_FALSE
        params.sat_parameters.num_search_workers=1;params.sat_parameters.random_seed=self.settings.seed
        depth=self.settings.search_depth or 'standard'
        base={'quick':1200,'standard':5000,'deep':15000}[depth]
        limit=max(100,min(100000,int(base*self.settings.time_budget_s/{'quick':15,'standard':60,'deep':180}[depth])))
        if len(self.g.tasks)<=8:limit=min(limit,600)
        # Deterministic work limit; wall clock is emergency protection only.
        monitor=self.solver.BranchesLimit(limit)
        r.AddSearchMonitor(monitor)
        read_limit=max(20000,int({'quick':100000,'standard':400000,'deep':1500000}[depth]
            *self.settings.time_budget_s/{'quick':15,'standard':60,'deep':180}[depth]))
        reads=self.table_reads
        patience=min(read_limit,max(20000,int(400000*self.settings.time_budget_s/
                     {'quick':15,'standard':60,'deep':180}[depth])))
        if len(self.g.tasks)<=8:patience=min(patience,20000)
        plateau=None  # Arm after all initial assignments have been prepared.
        work_monitor=self.solver.CustomLimit(lambda:reads[0]>=read_limit or
                                             (plateau is not None and plateau.reached(reads[0])))
        r.AddSearchMonitor(work_monitor)
        params.time_limit.seconds=max(30,math.ceil(self.settings.time_budget_s*2))
        keep={'quick':2,'standard':4,'deep':7}[depth]
        candidates={};seen=0
        nodes=self.nodes;reloads=self.reloads
        def canonical(routes):
            # Recharge copies are interchangeable. Do not run expensive final
            # checks repeatedly for the same physical route with different IDs.
            result=[]
            for v,route in enumerate(routes):
                used={};normal=[]
                for i in route:
                    node=nodes[i]
                    if node.kind=='refuel':
                        rank=used.get(node.site,0);used[node.site]=rank+1
                        i=[j for j in reloads[v] if nodes[j].site==node.site][rank]
                    normal.append(i)
                result.append(tuple(normal))
            return tuple(result)
        callback_r=weakref.proxy(r);vehicle_count=len(self.uids);capturing=True
        def capture():
            nonlocal seen
            if not capturing:return
            seen+=1
            routes=[]
            for v in range(vehicle_count):
                index=callback_r.Start(v);route=[]
                while not callback_r.IsEnd(index):
                    index=callback_r.NextVar(index).Value()
                    if not callback_r.IsEnd(index):route.append(m.IndexToNode(index))
                routes.append(route)
            signature=canonical(routes)
            value=callback_r.CostVar().Value()
            if plateau is not None:plateau.observe(value,reads[0])
            if signature not in candidates or value<candidates[signature]:candidates[signature]=value
            if len(candidates)>keep:
                del candidates[max(candidates,key=lambda key:(candidates[key],key))]
        r.AddAtSolutionCallback(capture)
        r.CloseModelWithParameters(params)
        fallback=r.ReadAssignmentFromRoutes(seed,True) if seed is not None and (not missing or self.diagnostic) else None
        if fallback:candidates[canonical(seed)]=fallback.ObjectiveValue()
        initial=None;selected_seed='greedy_fallback'
        from .routing_seed import sweep_routes
        seed_candidates=1
        for split in (False,True):
            for reverse in (False,True):
                proposed,omitted=sweep_routes(self,reverse,split)
                if proposed is None or (omitted and not self.diagnostic):continue
                reads[0]=0
                assignment=r.ReadAssignmentFromRoutes(proposed,True)
                seed_candidates+=1
                if assignment:
                    candidates[canonical(proposed)]=assignment.ObjectiveValue()
                    if initial is None or assignment.ObjectiveValue()<initial.ObjectiveValue():
                        initial=assignment
                        selected_seed=('contiguous_strips' if split else 'whole_sweeps')+('_reverse' if reverse else '')
        if initial is None:initial=fallback
        initial_value=None
        if initial:
            if self.settings.objective=='makespan':
                initial_value=max(initial.Value(self.time.CumulVar(r.End(v))) for v in range(vehicle_count))/SCALE
            else:
                initial_value=0.
                for v in range(vehicle_count):
                    index=r.Start(v)
                    while not r.IsEnd(index):
                        nxt=initial.Value(r.NextVar(index))
                        initial_value+=int(self.flight[v][m.IndexToNode(index),m.IndexToNode(nxt)])/SCALE
                        index=nxt
        seed_s=time.perf_counter()-start
        if self.progress:self.progress(dict(stage='routing_search',initial_objective_s=initial_value,nodes=self.n,branch_limit=limit,seed_valid=initial is not None,selected_seed=selected_seed))
        solve_started=time.perf_counter()
        reads[0]=0
        plateau=ImprovementLimit(patience,min(candidates.values()) if candidates else None)
        solution=(r.SolveFromAssignmentWithParameters(initial,params) if initial else r.SolveWithParameters(params))
        solve_s=time.perf_counter()-solve_started
        search_reads=reads[0]
        stagnant=plateau.reached(search_reads)
        if solution:
            route_list=[]
            for v in range(len(self.uids)):
                index=r.Start(v);route=[]
                while not r.IsEnd(index):
                    index=solution.Value(r.NextVar(index))
                    if not r.IsEnd(index):route.append(m.IndexToNode(index))
                route_list.append(route)
            candidates[canonical(route_list)]=solution.ObjectiveValue()
        capturing=False
        from .routing_blocks import refine_blocks
        block_reports=[]
        block_limit={'quick':80,'standard':320,'deep':1000}[depth]
        starts=sorted(candidates,key=lambda key:(candidates[key],key))[:1]
        for key in starts:
            polished,report=refine_blocks(self,[list(route) for route in key],block_limit,self.progress)
            block_reports.append(report)
            for value,routes in polished:
                candidates[canonical(routes)]=value
        ordered=sorted(candidates,key=lambda key:(candidates[key],key))[:keep]
        return [list(map(list,key)) for key in ordered],dict(seed_s=seed_s,initial_objective_s=initial_value,
            search_s=time.perf_counter()-start-seed_s,solutions_seen=seen,branches=self.solver.Branches(),
            failures=self.solver.Failures(),branch_limit=limit,status=int(r.status()),logical_nodes=self.n,
            reload_copies=self.copies,greedy_missing=missing,finalists=len(ordered),
            reload_copy_limit_reached=copy_limited,depth=depth,
            seed_candidates=seed_candidates,selected_seed=selected_seed,block_search=block_reports,
            table_reads=search_reads,table_read_limit=read_limit,
            stagnation_read_limit=patience,reads_since_improvement=search_reads-plateau.last_improvement_read,
            stagnation_limit_reached=stagnant,
            emergency_time_limit_s=params.time_limit.seconds,
            emergency_time_limit_reached=solve_s>=params.time_limit.seconds-.01,
            stop_policy='deterministic no-improvement/branch/table-read limits; emergency time limit separately',seed=self.settings.seed,diagnostic=self.diagnostic)
