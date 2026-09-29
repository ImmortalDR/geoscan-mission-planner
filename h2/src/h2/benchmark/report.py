"""Dependency-free HTML report: spatial routes and service-window schedule."""
from html import escape
from pathlib import Path

from .vrptw import Instance, Result

COLORS = ('#0072b2', '#d55e00', '#009e73', '#cc79a7', '#e69f00', '#56b4e9')


def write_html(instance: Instance, result: Result, path: str | Path) -> Path:
    cs = {c.id: c for c in instance.customers}
    xmin, xmax = min(c.x for c in cs.values()), max(c.x for c in cs.values())
    ymin, ymax = min(c.y for c in cs.values()), max(c.y for c in cs.values())
    factor = min(680/max(xmax-xmin, 1), 440/max(ymax-ymin, 1))

    def xy(c):
        return 30+(c.x-xmin)*factor, 470-(c.y-ymin)*factor

    spatial = ['<svg viewBox="0 0 740 500" role="img" aria-label="Routes">']
    rows = []
    horizon = max(cs[0].due, 1)
    for route in result.routes:
        color = COLORS[route.vehicle_id % len(COLORS)]
        points = ' '.join(f'{xy(cs[v.customer_id])[0]:.2f},{xy(cs[v.customer_id])[1]:.2f}'
                          for v in route.visits if v.customer_id in cs)
        spatial.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"><title>Vehicle {route.vehicle_id}</title></polyline>')
        for v in route.visits[1:-1]:
            c = cs.get(v.customer_id)
            if c is None:
                continue
            # Gray bars show allowed service-start windows; colored bars show service.
            left, width = 100*c.ready/horizon, 100*(c.due-c.ready)/horizon
            at, duration = 100*v.service_start/horizon, 100*c.service/horizon
            rows.append(f'<tr><td>{route.vehicle_id}</td><td>{c.id}</td><td>{v.service_start:.3f}</td>'
                        f'<td>{c.ready:g}–{c.due:g}</td><td><div class="track">'
                        f'<span class="window" style="left:{left:.4f}%;width:{width:.4f}%"></span>'
                        f'<span class="service" style="background:{color};left:{at:.4f}%;width:{max(duration,.2):.4f}%"></span>'
                        '</div></td></tr>')
    for c in cs.values():
        x, y = xy(c)
        spatial.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{5 if c.id == 0 else 2.5}" fill="black"><title>Customer {c.id}; demand {c.demand}; window [{c.ready}, {c.due}]</title></circle>')
    spatial.append('</svg>')
    metrics = ''.join(f'<dt>{escape(str(k))}</dt><dd>{escape(str(v))}</dd>' for k,v in result.metrics.items())
    violations = ''.join(f'<li>{escape(e)}</li>' for e in result.violations)
    document = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>VRPTW {escape(instance.name)}</title>
<style>body{{font:15px system-ui;max-width:1100px;margin:2rem auto;padding:1rem;color:#182530}}svg{{width:100%;max-height:550px;background:#f5f7fa}}table{{border-collapse:collapse;width:100%}}td,th{{padding:5px;border-bottom:1px solid #ddd;text-align:left}}td:last-child{{width:55%}}.track{{position:relative;height:16px;background:#f7f7f7}}.window,.service{{position:absolute;top:2px;height:12px}}.window{{background:#ddd}}.service{{height:6px;top:5px}}dt{{font-weight:bold}}dd{{margin-bottom:8px;overflow-wrap:anywhere}}</style>
<h1>VRPTW {escape(instance.name)}</h1><p>Standard abstract routing benchmark. No UAV safety claim or published BKS comparison.</p>
<dl>{metrics}</dl><ul>{violations}</ul><h2>Routes</h2>{''.join(spatial)}
<h2>Service-start windows</h2><p>Gray: allowed service-start interval. Color: service duration. Time axis: 0–{horizon:g}. Hover over map points for customer details.</p>
<table><thead><tr><th>Vehicle</th><th>Customer</th><th>Start</th><th>Window</th><th>Schedule</th></tr></thead><tbody>{''.join(rows)}</tbody></table></html>'''
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding='utf-8')
    return target
