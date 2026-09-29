"""Minimal KML reader (WGS-84) for M1 SceneLoader.

Parses Placemark Polygon / Point / LineString without geopandas.
Used when GeoJSON is absent or as a companion.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..geo import make_valid

_NS = {"kml": "http://www.opengis.net/kml/2.2"}


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _coords(text: str) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for token in re.split(r"\s+", (text or "").strip()):
        if not token:
            continue
        parts = token.split(",")
        if len(parts) < 2:
            continue
        lon, lat = float(parts[0]), float(parts[1])
        pts.append((lon, lat))
    return pts


def _ring_to_coords(ring_el: ET.Element) -> list[tuple[float, float]]:
    for child in ring_el.iter():
        if _local(child.tag) == "coordinates" and child.text:
            return _coords(child.text)
    return []


def _polygon(el: ET.Element) -> Polygon | None:
    outer: list[tuple[float, float]] = []
    holes: list[list[tuple[float, float]]] = []
    for child in el:
        name = _local(child.tag)
        if name == "outerBoundaryIs":
            for ring in child.iter():
                if _local(ring.tag) == "LinearRing":
                    outer = _ring_to_coords(ring)
        elif name == "innerBoundaryIs":
            for ring in child.iter():
                if _local(ring.tag) == "LinearRing":
                    h = _ring_to_coords(ring)
                    if len(h) >= 4:
                        holes.append(h)
    if len(outer) < 4:
        return None
    return make_valid(Polygon(outer, holes or None))


def _linestring(el: ET.Element) -> LineString | None:
    for child in el.iter():
        if _local(child.tag) == "coordinates" and child.text:
            pts = _coords(child.text)
            if len(pts) >= 2:
                return LineString(pts)
    return None


def read_kml_features(path: Path) -> list[tuple[dict[str, Any], BaseGeometry]]:
    """Return (properties, geom) in WGS-84. props: name, style, role/description."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"{path.name}: invalid KML ({exc})") from exc

    out: list[tuple[dict[str, Any], BaseGeometry]] = []
    for pm in root.iter():
        if _local(pm.tag) != "Placemark":
            continue
        name = ""
        desc = ""
        style = ""
        geom: BaseGeometry | None = None
        for child in pm:
            t = _local(child.tag)
            if t == "name" and child.text:
                name = child.text.strip()
            elif t == "description" and child.text:
                desc = child.text.strip()
            elif t == "styleUrl" and child.text:
                style = child.text.strip().lstrip("#")
            elif t == "Polygon":
                geom = _polygon(child)
            elif t == "LineString":
                geom = _linestring(child)
            elif t == "Point":
                for sub in child.iter():
                    if _local(sub.tag) == "coordinates" and sub.text:
                        pts = _coords(sub.text)
                        if pts:
                            geom = Point(pts[0])
                        break
            elif t == "MultiGeometry":
                polys = []
                lines = []
                for sub in child.iter():
                    if _local(sub.tag) == "Polygon":
                        p = _polygon(sub)
                        if p is not None and not p.is_empty:
                            polys.append(p)
                    elif _local(sub.tag) == "LineString":
                        ln = _linestring(sub)
                        if ln is not None and not ln.is_empty:
                            lines.append(ln)
                if polys:
                    geom = make_valid(unary_union(polys))
                elif lines:
                    geom = make_valid(unary_union(lines))
        if geom is None or geom.is_empty:
            continue
        props = {"id": name or f"kml_{len(out)+1}", "name": name, "style": style, "role": desc or "both"}
        if style == "survey" or (
            style == "" and geom.geom_type in ("Polygon", "MultiPolygon", "LineString", "MultiLineString")
        ):
            props["survey_type"] = "rgb"
            if geom.geom_type in ("LineString", "MultiLineString"):
                props["mode"] = "corridor"
        if style == "nfz":
            props["layer"] = "nfz"
        out.append((props, geom))
    return out


def kml_survey_polygons(path: Path) -> list[tuple[dict[str, Any], BaseGeometry]]:
    feats = read_kml_features(path)
    return [
        (p, g)
        for p, g in feats
        if p.get("style") == "survey"
        or (
            g.geom_type in ("Polygon", "MultiPolygon", "LineString", "MultiLineString")
            and p.get("style") not in ("nfz", "allowed", "site")
        )
    ]
