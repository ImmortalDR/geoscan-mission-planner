"""Standalone diagnostics and geographic export; neither issues a certificate."""
from copy import deepcopy
from html import escape
import json
from pathlib import Path


def export_geojson(plan: dict) -> dict:
    """Return WGS84 3D route features without modifying metric source coordinates."""
    from pyproj import Transformer
    transformer = Transformer.from_crs(plan['crs']['metric_epsg'], 4326, always_xy=True)
    features = []
    for sortie in plan['sorties']:
        coordinates = []
        for p in sortie['waypoints']:
            lon, lat = transformer.transform(p['x'], p['y'])
            coordinates.append([lon, lat, p.get('z_m', p['agl_m'])])
        if len(coordinates) < 2:
            continue
        features.append(dict(type='Feature', geometry=dict(type='LineString', coordinates=coordinates),
            properties=dict(sortie_id=sortie['id'], uav_id=sortie['uav_id'],
                            times_s=[p['t_s'] for p in sortie['waypoints']],
                            phases=[p['phase'] for p in sortie['waypoints']],
                            time_origin=plan['time_origin'], source_crs=deepcopy(plan['crs']),
                            altitude_assumptions=plan.get('assumptions', []))))
    return dict(type='FeatureCollection', features=features)


def render_html(plan: dict) -> str:
    """Render offline interactive route, time, conflict and incumbent diagnostics."""
    data = json.dumps(plan, allow_nan=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    metrics = ''.join(f'<dt>{escape(str(k))}</dt><dd>{escape(str(v))}</dd>' for k,v in plan['metrics'].items())
    assumptions = ''.join(f'<li>{escape(str(v))}</li>' for v in plan.get('assumptions', []))
    document = '''<!doctype html><html lang="en"><meta charset="utf-8"><title>H2 diagnostics</title>
<style>body{font:15px system-ui;margin:2rem auto;padding:1rem;max-width:1200px;color:#172738}svg{width:100%;background:#f5f7fa;margin:8px 0}#map{height:480px}#timeline{min-height:100px}#history{height:180px}dt{display:inline;font-weight:bold}dd{display:inline;margin-right:18px}input{width:75%}.bad{color:#a21b1b}button{margin:4px}li{margin:4px}</style>
<h1>H2 diagnostic plan</h1><p id="identity"></p><p>Planner diagnostics; independent H3 validation is required. No flight safety certificate.</p>
<dl>METRICS</dl><ul>ASSUMPTIONS</ul><p><label>Mission time <input id="time" type="range" min="0" step="0.1" value="0"></label> <output id="clock"></output></p>
<p>Solid lines: survey; dashed lines: transit/other phases. Colored dots: active UAVs. Red crosses: unresolved conflicts.</p>
<svg id="map" viewBox="0 0 1000 480" role="img" aria-label="Routes and aircraft positions"></svg>
<h2>Aircraft schedule</h2><svg id="timeline" role="img" aria-label="Sorties per aircraft"></svg>
<h2>Incumbent history</h2><p>Objective value versus elapsed solver time. Partial solutions are shown separately by their missing-task count in point tooltips.</p><svg id="history" viewBox="0 0 1000 180" role="img" aria-label="Optimizer history"></svg>
<h2>Unresolved conflicts</h2><div id="conflicts"></div><h2>Unassigned tasks / check violations</h2><pre id="issues"></pre>
<script type="application/json" id="plan-data">PLAN_DATA</script>
<script>
const p=JSON.parse(document.getElementById('plan-data').textContent),ns='http://www.w3.org/2000/svg';
const colors=['#0072b2','#d55e00','#009e73','#cc79a7','#b88600','#56b4e9'];
const uids=[...new Set(p.sorties.map(s=>s.uav_id))],color=id=>colors[uids.indexOf(id)%colors.length];
const points=p.sorties.flatMap(s=>s.waypoints), xs=points.map(q=>q.x),ys=points.map(q=>q.y);
const minX=Math.min(...xs,0),maxX=Math.max(...xs,1); // actual bounds below avoid distant origin
const x0=xs.length?Math.min(...xs):0,y0=ys.length?Math.min(...ys):0;
const scale=Math.min(940/Math.max(1,(xs.length?Math.max(...xs):1)-x0),420/Math.max(1,(ys.length?Math.max(...ys):1)-y0));
const xy=q=>[30+(q.x-x0)*scale,450-(q.y-y0)*scale];
const end=Math.max(1,...points.map(q=>q.t_s)),map=document.getElementById('map');
function el(tag,attrs={},text=null,parent=map){const n=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);if(text!==null)n.textContent=text;parent.appendChild(n);return n;}
function pos(s,t){const a=s.waypoints;if(!a.length||t<a[0].t_s||t>a[a.length-1].t_s)return null;for(let i=1;i<a.length;i++){if(t<=a[i].t_s){let f=(t-a[i-1].t_s)/(a[i].t_s-a[i-1].t_s||1);return {x:a[i-1].x+f*(a[i].x-a[i-1].x),y:a[i-1].y+f*(a[i].y-a[i-1].y)};}}return a[a.length-1];}
for(const s of p.sorties){for(let i=1;i<s.waypoints.length;i++){const a=s.waypoints[i-1],b=s.waypoints[i],x=xy(a),y=xy(b);const n=el('line',{x1:x[0],y1:x[1],x2:y[0],y2:y[1],stroke:color(s.uav_id),'stroke-width':2,'stroke-dasharray':b.phase==='survey'?'none':'5 4'});el('title',{},s.id+' '+b.phase,n);}}
const markers=p.sorties.map(s=>({s,n:el('circle',{r:6,fill:color(s.uav_id),stroke:'white','stroke-width':2})}));
const timeline=document.getElementById('timeline');timeline.setAttribute('viewBox',`0 0 1000 ${Math.max(70,40+uids.length*32)}`);
for(let i=0;i<uids.length;i++)el('text',{x:5,y:30+i*32},uids[i],timeline);
for(const s of p.sorties){const y=15+uids.indexOf(s.uav_id)*32,n=el('rect',{x:150+820*s.start_s/end,y,width:Math.max(1,820*(s.end_s-s.start_s)/end),height:18,fill:color(s.uav_id)},null,timeline);el('title',{},`${s.id}: ${s.start_s.toFixed(1)}–${s.end_s.toFixed(1)} s; ${s.task_ids.length} tasks`,n);}
const cursor=el('line',{x1:150,x2:150,y1:0,y2:40+uids.length*32,stroke:'#111'},null,timeline);
const slider=document.getElementById('time');slider.max=end;
function draw(){const t=+slider.value;document.getElementById('clock').textContent=t.toFixed(1)+' s';for(const {s,n} of markers){const q=pos(s,t);n.style.display=q?'':'none';if(q){const z=xy(q);n.setAttribute('cx',z[0]);n.setAttribute('cy',z[1]);}}cursor.setAttribute('x1',150+820*t/end);cursor.setAttribute('x2',150+820*t/end);}
slider.addEventListener('input',draw);draw();
const conflicts=p.deconfliction?.remaining||[];
for(const c of conflicts){const s=p.sorties.find(s=>s.id===c.sortie_a),q=s?pos(s,c.start_s):null;if(q){const z=xy(q);el('text',{x:z[0],y:z[1],fill:'red','font-size':24},'×');}const b=document.createElement('button');b.textContent=`${c.sortie_a} / ${c.sortie_b} at ${c.start_s.toFixed(1)} s`;b.onclick=()=>{slider.value=c.start_s;draw();};document.getElementById('conflicts').appendChild(b);}
if(!conflicts.length)document.getElementById('conflicts').textContent='No unresolved conflicts reported.';
const hist=p.solver_log||[],h=document.getElementById('history'),ht=Math.max(1,...hist.map(x=>x.elapsed_s)),hv=Math.max(1,...hist.map(x=>x.objective_value));
const hp=hist.map(x=>[40+930*x.elapsed_s/ht,150-130*x.objective_value/hv]);
el('polyline',{points:hp.map(x=>x.join(',')).join(' '),fill:'none',stroke:'#0072b2','stroke-width':2},null,h);
for(let i=0;i<hist.length;i++){const n=el('circle',{cx:hp[i][0],cy:hp[i][1],r:4,fill:hist[i].unassigned_count?'#d55e00':'#0072b2'},null,h);el('title',{},JSON.stringify(hist[i]),n);}
el('text',{x:40,y:175},`Elapsed search: 0–${ht.toFixed(2)} s; objective: 0–${hv.toFixed(1)}`,h);
document.getElementById('identity').textContent=`${p.scene_id} — ${p.objective} — ${p.status}`;
document.getElementById('issues').textContent=JSON.stringify({unassigned:p.unassigned,violations:p.checks?.violations},null,2);
</script></html>'''.replace('METRICS',metrics).replace('ASSUMPTIONS',assumptions).replace('PLAN_DATA',data)
    return document


def write_html(plan: dict, path: str | Path) -> Path:
    """Write an offline diagnostics report."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html(plan), encoding='utf-8')
    return target
