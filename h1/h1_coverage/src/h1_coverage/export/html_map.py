"""Simple HTML map for demo (W-02 + W-06 coverage badge)."""
from __future__ import annotations

from pathlib import Path

from shapely.geometry import LineString

from ..coverage.engine import CoverageResult
from ..models import Scene


def export_html_map(scene: Scene, coverage: CoverageResult, path: str | Path) -> Path:
    geoms = []
    for job in scene.jobs:
        geoms.append(job.effective_geom or job.geom)
    for z in scene.no_fly_zones:
        geoms.append(z.geom)
    for t in coverage.tasks.values():
        for tr in t.transects:
            geoms.append(LineString(tr.coords))
    if not geoms:
        bounds = (0, 0, 100, 100)
    else:
        b = geoms[0].bounds
        for g in geoms[1:]:
            bb = g.bounds
            b = (min(b[0], bb[0]), min(b[1], bb[1]), max(b[2], bb[2]), max(b[3], bb[3]))
        bounds = b
    minx, miny, maxx, maxy = bounds
    pad = max((maxx - minx), (maxy - miny), 1.0) * 0.05
    minx, miny, maxx, maxy = minx - pad, miny - pad, maxx + pad, maxy + pad
    w, h = maxx - minx, maxy - miny
    ox, oy = minx, miny

    def tx(x: float, y: float) -> tuple[float, float]:
        return (x - ox, y - oy)

    pct = coverage.coverage_percent
    ok = pct + 1e-6 >= 99.9
    badge_bg = "#2d6a4f" if ok else "#9b2226"
    badge_label = f"{pct:.2f}% covered"

    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>H1 {scene.id}</title>",
        "<style>body{font-family:system-ui,sans-serif;margin:1rem;background:#0f1419;color:#e7ecf1}",
        "svg{background:#1a2332;border-radius:8px;width:100%;height:auto}",
        ".meta{margin:0.5rem 0 1rem;opacity:0.9;display:flex;gap:1rem;align-items:center;flex-wrap:wrap}",
        f".badge{{background:{badge_bg};color:#fff;padding:0.35rem 0.75rem;border-radius:6px;"
        "font-weight:700;letter-spacing:0.02em}}</style></head><body>",
        f"<h1>H1 coverage — {scene.id}</h1>",
        f"<p class='meta'><span class='badge' title='Gate B'>{badge_label}</span>"
        f"<span>tasks={len(coverage.tasks)}</span></p>",
        f"<svg viewBox='0 0 {w:.1f} {h:.1f}' xmlns='http://www.w3.org/2000/svg'>",
    ]

    for job in scene.jobs:
        g = job.effective_geom or job.geom
        if g.is_empty:
            continue
        if g.geom_type == "Polygon":
            pts = " ".join(f"{tx(x,y)[0]:.2f},{h - tx(x,y)[1]:.2f}" for x, y in g.exterior.coords)
            fill = "#2d6a4f66" if ok else "#6c584c66"
            parts.append(
                f"<polygon points='{pts}' fill='{fill}' stroke='#95d5b2' stroke-width='{max(w,h)/400}'/>"
            )
            for hole in g.interiors:
                pts = " ".join(f"{tx(x,y)[0]:.2f},{h - tx(x,y)[1]:.2f}" for x, y in hole.coords)
                parts.append(
                    f"<polygon points='{pts}' fill='#0f1419' stroke='#95d5b2' stroke-width='{max(w,h)/500}'/>"
                )
        elif g.geom_type == "MultiPolygon":
            for poly in g.geoms:
                pts = " ".join(f"{tx(x,y)[0]:.2f},{h - tx(x,y)[1]:.2f}" for x, y in poly.exterior.coords)
                parts.append(
                    f"<polygon points='{pts}' fill='#2d6a4f66' stroke='#95d5b2' stroke-width='{max(w,h)/400}'/>"
                )

    for z in scene.no_fly_zones:
        g = z.geom
        if g.geom_type == "Polygon":
            pts = " ".join(f"{tx(x,y)[0]:.2f},{h - tx(x,y)[1]:.2f}" for x, y in g.exterior.coords)
            parts.append(
                f"<polygon points='{pts}' fill='#9b222666' stroke='#e63946' stroke-width='{max(w,h)/350}'/>"
            )

    for task in coverage.tasks.values():
        color = "#4cc9f0" if task.fixed_wing_safe else "#f4a261"
        for tr in task.transects:
            pts = " ".join(f"{tx(x,y)[0]:.2f},{h - tx(x,y)[1]:.2f}" for x, y in tr.coords)
            parts.append(
                f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='{max(w,h)/250}'/>"
            )
            if not task.fixed_wing_safe:
                for pt in (tr.start, tr.end):
                    cx, cy = tx(pt[0], pt[1])
                    r = max(w, h) * 0.01
                    parts.append(
                        f"<circle cx='{cx:.2f}' cy='{h - cy:.2f}' r='{r:.2f}' "
                        f"fill='none' stroke='#f4a261' stroke-dasharray='4 3'/>"
                    )

    for s in scene.sites:
        cx, cy = tx(s.point.x, s.point.y)
        parts.append(
            f"<circle cx='{cx:.2f}' cy='{h - cy:.2f}' r='{max(w,h)/80:.2f}' fill='#ffd166' stroke='#fff'/>"
        )

    parts.append("</svg></body></html>")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")
    return path
