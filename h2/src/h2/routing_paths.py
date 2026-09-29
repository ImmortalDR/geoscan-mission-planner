"""Shortest physical paths with incoming-edge heading state and escape bounds."""
import math
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra


def prepare_paths(vertices, records, weights, source_headings, turn_seconds, emergency, landing, margin, angle, heading):
    edges=sorted(weights);edge_index={edge:i for i,edge in enumerate(edges)}
    n=len(vertices);e=len(edges);count=e+2*n
    starts=np.arange(e,e+n);ends=np.arange(e+n,e+2*n)
    outgoing={a:[] for a in range(n)};incoming={a:[] for a in range(n)}
    edge_headings={edge:(heading(records[edge]),heading(records[edge],True)) for edge in edges}
    for i,(a,b) in enumerate(edges):outgoing[a].append(i);incoming[b].append(i)
    aa=[];bb=[];cost=[]
    def add(a,b,t):aa.append(a);bb.append(b);cost.append(max(1e-8,float(t)))
    for a in range(n):
        yaw=source_headings[a];base=math.isnan(yaw)
        add(starts[a],ends[a],0. if base else math.pi*turn_seconds)
        for j in outgoing[a]:
            edge=edges[j];first=edge_headings[edge][0]
            add(starts[a],j,weights[edge]+(0. if base else turn_seconds*angle(yaw,first)))
        for i in incoming[a]:
            last=edge_headings[edges[i]][1]
            add(i,ends[a],0. if base else turn_seconds*angle(last,yaw+math.pi))
            # Real bases are terminals, not fly-through shortcuts between tasks.
            # A subsequent takeoff needs an explicit permitted recharge visit.
            for j in ([] if base else outgoing[a]):
                edge=edges[j];first=edge_headings[edge][0]
                add(i,j,weights[edge]+turn_seconds*angle(last,first))
    graph=csr_matrix((cost,(aa,bb)),shape=(count,count))
    dist,pred=dijkstra(graph,directed=True,indices=starts,return_predecessors=True)
    # Reversed graph from a real emergency landing sink. Dummy starts/ends of
    # Routing do not participate. An edge-state carries its actual arrival yaw.
    sink=count
    reverse=csr_matrix((cost+[landing]*len(emergency),(bb+[sink]*len(emergency),aa+[int(ends[i]) for i in emergency])),shape=(count+1,count+1))
    escape=dijkstra(reverse,directed=True,indices=sink)[:count]
    returns=escape[starts].copy()
    # Reorient from a survey-entry heading to that point's exit heading if
    # aborting before service; this is a conservative, explicit half turn.
    for i in range(n):
        if not math.isnan(source_headings[i]):escape[ends[i]]=returns[i]+math.pi*turn_seconds
    q=np.full((n,n),np.inf);first=np.zeros((n,n));last=np.zeros((n,n))
    first_edge=np.full(count,-1,dtype=np.int32)
    for a in range(n):
        required=np.full(count,np.inf);required[starts[a]]=returns[a]+margin
        first_edge.fill(-1)
        for state in np.argsort(dist[a],kind='stable'):
            previous=int(pred[a,state])
            if previous<0:continue
            required[state]=max(required[previous],dist[a,state]+escape[state]+margin)
            first_edge[state]=state if previous==starts[a] and state<e else first_edge[previous]
        q[a]=required[ends]
        for b in range(n):
            i=int(first_edge[ends[b]]);j=int(pred[a,ends[b]])
            if i>=0:first[a,b]=edge_headings[edges[i]][0]
            if 0<=j<e:last[a,b]=edge_headings[edges[j]][1]
    return dict(dist=dist[:,ends].copy(),pred=pred,q=q,returns=returns,
        first_heading=first,last_heading=last,turn_seconds=turn_seconds,edge_headings=edge_headings,
        path_states=edges,path_starts=starts,path_ends=ends)
