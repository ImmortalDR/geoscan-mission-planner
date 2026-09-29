"""KML import/export (OGC KML 2.2 subset used by GIS packages and QGC)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable, Sequence

from shapely.geometry import (
    LineString,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

KML_NS = "http://www.opengis.net/kml/2.2"
_NS = {"k": KML_NS}


class KmlError(ValueError):
    pass


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def _parse_coords(text: str) -> list[tuple[float, float, float]]:
    out: list[tuple[float, float, float]] = []
    for token in (text or "").replace("\n", " ").replace("\t", " ").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        lon, lat = float(parts[0]), float(parts[1])
        alt = float(parts[2]) if len(parts) > 2 else 0.0
        out.append((lon, lat, alt))
    return out


def _geom_from_placemark(pm: ET.Element) -> list[BaseGeometry]:
    geoms: list[BaseGeometry] = []
    for pt in pm.iter(f"{{{KML_NS}}}Point"):
        c = _parse_coords(pt.findtext(f"{{{KML_NS}}}coordinates", default=""))
        if c:
            geoms.append(Point(c[0][0], c[0][1]))
    for ls in pm.iter(f"{{{KML_NS}}}LineString"):
        c = _parse_coords(ls.findtext(f"{{{KML_NS}}}coordinates", default=""))
        if len(c) >= 2:
            geoms.append(LineString([(x, y) for x, y, _ in c]))
    for poly in pm.iter(f"{{{KML_NS}}}Polygon"):
        outer_el = poly.find(f"{{{KML_NS}}}outerBoundaryIs/{{{KML_NS}}}LinearRing/{{{KML_NS}}}coordinates")
        if outer_el is None:
            continue
        outer = [(x, y) for x, y, _ in _parse_coords(outer_el.text or "")]
        holes = []
        for inner in poly.findall(f"{{{KML_NS}}}innerBoundaryIs/{{{KML_NS}}}LinearRing/{{{KML_NS}}}coordinates"):
            ring = [(x, y) for x, y, _ in _parse_coords(inner.text or "")]
            if len(ring) >= 4:
                holes.append(ring)
        if len(outer) >= 4:
            geoms.append(Polygon(outer, holes))
    return geoms


def read_kml(path: str | Path) -> list[tuple[dict[str, Any], BaseGeometry]]:
    """Return (properties, WGS-84 geometry) pairs for every Placemark."""
    p = Path(path)
    if not p.exists():
        raise KmlError(f"file not found: {p}")
    try:
        root = ET.parse(p).getroot()
    except ET.ParseError as exc:
        raise KmlError(f"{p}: invalid KML: {exc}") from exc

    out: list[tuple[dict[str, Any], BaseGeometry]] = []
    for pm in root.iter(f"{{{KML_NS}}}Placemark"):
        props: dict[str, Any] = {}
        name = pm.findtext(f"{{{KML_NS}}}name")
        if name:
            props["name"] = name
        desc = pm.findtext(f"{{{KML_NS}}}description")
        if desc:
            props["description"] = desc
        for data in pm.iter(f"{{{KML_NS}}}Data"):
            key = data.get("name")
            if key:
                props[key] = data.findtext(f"{{{KML_NS}}}value")
        for sd in pm.iter(f"{{{KML_NS}}}SimpleData"):
            key = sd.get("name")
            if key:
                props[key] = sd.text
        geoms = _geom_from_placemark(pm)
        if len(geoms) > 1 and all(isinstance(g, Polygon) for g in geoms):
            out.append((props, MultiPolygon(geoms)))
        else:
            for g in geoms:
                out.append((props, g))
    return out


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def _coord_text(coords: Sequence[Sequence[float]]) -> str:
    return " ".join(
        f"{c[0]:.8f},{c[1]:.8f},{(c[2] if len(c) > 2 else 0.0):.2f}" for c in coords
    )


class KmlDocument:
    """Minimal KML builder with folders, styles and ExtendedData."""

    def __init__(self, name: str, description: str = ""):
        self.root = ET.Element(f"{{{KML_NS}}}kml")
        self.doc = ET.SubElement(self.root, f"{{{KML_NS}}}Document")
        ET.SubElement(self.doc, f"{{{KML_NS}}}name").text = name
        if description:
            ET.SubElement(self.doc, f"{{{KML_NS}}}description").text = description
        self._styles: set[str] = set()

    def style(self, style_id: str, color_abgr: str, width: float = 2.0, fill: bool = False) -> str:
        if style_id in self._styles:
            return style_id
        st = ET.SubElement(self.doc, f"{{{KML_NS}}}Style", {"id": style_id})
        ls = ET.SubElement(st, f"{{{KML_NS}}}LineStyle")
        ET.SubElement(ls, f"{{{KML_NS}}}color").text = color_abgr
        ET.SubElement(ls, f"{{{KML_NS}}}width").text = str(width)
        ps = ET.SubElement(st, f"{{{KML_NS}}}PolyStyle")
        ET.SubElement(ps, f"{{{KML_NS}}}color").text = color_abgr
        ET.SubElement(ps, f"{{{KML_NS}}}fill").text = "1" if fill else "0"
        ET.SubElement(ps, f"{{{KML_NS}}}outline").text = "1"
        self._styles.add(style_id)
        return style_id

    def folder(self, name: str, parent: ET.Element | None = None) -> ET.Element:
        f = ET.SubElement(parent if parent is not None else self.doc, f"{{{KML_NS}}}Folder")
        ET.SubElement(f, f"{{{KML_NS}}}name").text = name
        return f

    def placemark(
        self,
        parent: ET.Element,
        name: str,
        geom: BaseGeometry,
        props: dict[str, Any] | None = None,
        style_id: str | None = None,
        altitude_mode: str = "absolute",
        coords3d: Sequence[Sequence[float]] | None = None,
        when: str | None = None,
        time_span: tuple[str, str] | None = None,
    ) -> ET.Element:
        pm = ET.SubElement(parent, f"{{{KML_NS}}}Placemark")
        ET.SubElement(pm, f"{{{KML_NS}}}name").text = name
        if style_id:
            ET.SubElement(pm, f"{{{KML_NS}}}styleUrl").text = f"#{style_id}"
        if props:
            ed = ET.SubElement(pm, f"{{{KML_NS}}}ExtendedData")
            for k, v in props.items():
                d = ET.SubElement(ed, f"{{{KML_NS}}}Data", {"name": str(k)})
                ET.SubElement(d, f"{{{KML_NS}}}value").text = "" if v is None else str(v)
        if when:
            ts = ET.SubElement(pm, f"{{{KML_NS}}}TimeStamp")
            ET.SubElement(ts, f"{{{KML_NS}}}when").text = when
        if time_span:
            ts = ET.SubElement(pm, f"{{{KML_NS}}}TimeSpan")
            ET.SubElement(ts, f"{{{KML_NS}}}begin").text = time_span[0]
            ET.SubElement(ts, f"{{{KML_NS}}}end").text = time_span[1]

        if isinstance(geom, Point):
            el = ET.SubElement(pm, f"{{{KML_NS}}}Point")
            ET.SubElement(el, f"{{{KML_NS}}}altitudeMode").text = altitude_mode
            c = coords3d or [(geom.x, geom.y, 0.0)]
            ET.SubElement(el, f"{{{KML_NS}}}coordinates").text = _coord_text(c)
        elif isinstance(geom, LineString):
            el = ET.SubElement(pm, f"{{{KML_NS}}}LineString")
            ET.SubElement(el, f"{{{KML_NS}}}altitudeMode").text = altitude_mode
            ET.SubElement(el, f"{{{KML_NS}}}tessellate").text = "1"
            c = coords3d or [(x, y, 0.0) for x, y in geom.coords]
            ET.SubElement(el, f"{{{KML_NS}}}coordinates").text = _coord_text(c)
        elif isinstance(geom, Polygon):
            self._polygon(pm, geom, altitude_mode)
        elif isinstance(geom, MultiPolygon):
            mg = ET.SubElement(pm, f"{{{KML_NS}}}MultiGeometry")
            for g in geom.geoms:
                self._polygon(mg, g, altitude_mode)
        elif hasattr(geom, "geoms"):
            mg = ET.SubElement(pm, f"{{{KML_NS}}}MultiGeometry")
            for g in geom.geoms:
                if isinstance(g, LineString):
                    el = ET.SubElement(mg, f"{{{KML_NS}}}LineString")
                    ET.SubElement(el, f"{{{KML_NS}}}coordinates").text = _coord_text(
                        [(x, y, 0.0) for x, y in g.coords]
                    )
        return pm

    def _polygon(self, parent: ET.Element, poly: Polygon, altitude_mode: str) -> None:
        el = ET.SubElement(parent, f"{{{KML_NS}}}Polygon")
        ET.SubElement(el, f"{{{KML_NS}}}altitudeMode").text = "clampToGround"
        ob = ET.SubElement(el, f"{{{KML_NS}}}outerBoundaryIs")
        lr = ET.SubElement(ob, f"{{{KML_NS}}}LinearRing")
        ET.SubElement(lr, f"{{{KML_NS}}}coordinates").text = _coord_text(
            [(x, y, 0.0) for x, y in poly.exterior.coords]
        )
        for ring in poly.interiors:
            ib = ET.SubElement(el, f"{{{KML_NS}}}innerBoundaryIs")
            lr2 = ET.SubElement(ib, f"{{{KML_NS}}}LinearRing")
            ET.SubElement(lr2, f"{{{KML_NS}}}coordinates").text = _coord_text(
                [(x, y, 0.0) for x, y in ring.coords]
            )

    def tostring(self) -> str:
        ET.register_namespace("", KML_NS)
        body = ET.tostring(self.root, encoding="unicode")
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + body

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.tostring(), encoding="utf-8")
        return p


def write_simple_kml(
    path: str | Path,
    name: str,
    items: Iterable[tuple[str, BaseGeometry, dict[str, Any]]],
) -> Path:
    doc = KmlDocument(name)
    folder = doc.folder(name)
    for item_name, geom, props in items:
        doc.placemark(folder, item_name, geom, props)
    return doc.write(path)
