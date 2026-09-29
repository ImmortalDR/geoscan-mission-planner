"""Minimal GeoJSON IO (WGS-84) with resilient feature parsing."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from shapely.geometry import GeometryCollection, LineString, MultiLineString, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, unary_union

from ..geo import CrsPipeline, ensure_2d, looks_like_lonlat, make_valid
from .encoding import MAX_GEOJSON_BYTES, InputFileError, read_json_file


class GeoJsonError(ValueError):
    pass


def _unwrap(geom: BaseGeometry) -> list[BaseGeometry]:
    """Flatten GeometryCollection / multi-parts into workable pieces."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, GeometryCollection):
        out: list[BaseGeometry] = []
        for g in geom.geoms:
            out.extend(_unwrap(g))
        return out
    return [geom]


def normalize_survey_geom(geom: BaseGeometry) -> BaseGeometry | None:
    """
    Keep Polygon/MultiPolygon as-is; MultiLineString → merged LineString when possible.
    Returns None if geometry cannot be a survey area.
    Z coordinates are dropped (survey footprint is planar; height is AGL/DEM).
    """
    parts = _unwrap(make_valid(ensure_2d(geom)))
    if not parts:
        return None
    areas = [p for p in parts if p.geom_type in ("Polygon", "MultiPolygon")]
    if areas:
        u = unary_union(areas)
        return make_valid(u) if not u.is_empty else None
    lines = [p for p in parts if p.geom_type in ("LineString", "MultiLineString")]
    if not lines:
        return None
    if len(lines) == 1 and isinstance(lines[0], LineString):
        return lines[0] if lines[0].length > 1e-6 else None
    merged = linemerge(unary_union(lines))
    if isinstance(merged, LineString) and not merged.is_empty:
        return merged
    if isinstance(merged, MultiLineString) and not merged.is_empty:
        best = max(merged.geoms, key=lambda g: g.length)
        return best if best.length > 1e-6 else None
    return None


def assert_wgs84_lonlat(geom: BaseGeometry, *, label: str) -> None:
    """Refuse projected metres mistakenly stored as GeoJSON lon/lat."""
    minx, miny, maxx, maxy = geom.bounds
    for x, y in ((minx, miny), (maxx, maxy), (geom.centroid.x, geom.centroid.y)):
        if not looks_like_lonlat(float(x), float(y)):
            raise GeoJsonError(
                f"{label}: coordinates look like projected metres "
                f"(got ~{x:.1f},{y:.1f}); expected WGS-84 lon/lat"
            )


def read_features_wgs84(
    path: Path,
    *,
    skip_invalid: bool = True,
    max_bytes: int | None = None,
    check_lonlat: bool = False,
) -> tuple[list[tuple[dict[str, Any], BaseGeometry]], list[str]]:
    """
    Return (features, warnings).
    Skips features without type/geometry when ``skip_invalid``.
    """
    if max_bytes is None:
        max_bytes = MAX_GEOJSON_BYTES
    warnings: list[str] = []
    try:
        raw = read_json_file(path, max_bytes=max_bytes, label=path.name)
    except InputFileError as exc:
        raise GeoJsonError(str(exc)) from exc

    if not isinstance(raw, dict):
        raise GeoJsonError(f"{path.name}: expected FeatureCollection object")

    feats = raw.get("features")
    if feats is None and raw.get("type") == "Feature":
        feats = [raw]
    if not isinstance(feats, list):
        raise GeoJsonError(f"{path.name}: missing features[]")

    out: list[tuple[dict[str, Any], BaseGeometry]] = []
    for i, f in enumerate(feats):
        if not isinstance(f, dict):
            if skip_invalid:
                warnings.append(f"{path.name}: feature[{i}] skipped (not an object)")
                continue
            raise GeoJsonError(f"{path.name}: feature[{i}] not an object")
        ftype = f.get("type")
        if ftype is not None and ftype != "Feature":
            if skip_invalid:
                warnings.append(f"{path.name}: feature[{i}] skipped (type={ftype!r})")
                continue
            raise GeoJsonError(f"{path.name}: feature[{i}] type must be Feature")
        if "type" not in f:
            if skip_invalid:
                warnings.append(f"{path.name}: feature[{i}] skipped (missing type)")
                continue
            raise GeoJsonError(f"{path.name}: feature[{i}] missing type")

        gj = f.get("geometry")
        if gj is None:
            if skip_invalid:
                warnings.append(f"{path.name}: feature[{i}] skipped (null geometry)")
                continue
            raise GeoJsonError(f"{path.name}: feature[{i}] has null geometry")
        if isinstance(gj, dict) and "type" not in gj:
            if skip_invalid:
                warnings.append(f"{path.name}: feature[{i}] skipped (geometry missing type)")
                continue
            raise GeoJsonError(f"{path.name}: feature[{i}] geometry missing type")
        try:
            geom = shape(gj)
        except Exception as exc:
            if skip_invalid:
                warnings.append(f"{path.name}: feature[{i}] skipped (bad geometry)")
                continue
            raise GeoJsonError(f"{path.name}: feature[{i}] bad geometry ({exc})") from exc
        if geom.is_empty:
            continue
        props = dict(f.get("properties") or {})
        if not geom.is_valid:
            props["_repaired_invalid"] = True
            warnings.append(
                f"{path.name}: feature[{i}] id={props.get('id')} self-intersecting/invalid → repaired"
            )
        geom = make_valid(ensure_2d(geom))
        if check_lonlat:
            try:
                assert_wgs84_lonlat(geom, label=f"{path.name} feature[{i}]")
            except GeoJsonError:
                raise
        out.append((props, geom))
    return out, warnings


def read_features_metric(
    path: Path, crs: CrsPipeline, *, max_bytes: int | None = None
) -> tuple[list[tuple[dict[str, Any], BaseGeometry]], list[str]]:
    feats, warns = read_features_wgs84(path, max_bytes=max_bytes)
    return [(props, crs.to_metric(geom)) for props, geom in feats], warns


def write_feature_collection(path: Path, features: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import json

    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, indent=2),
        encoding="utf-8",
    )


def feature(geom: BaseGeometry, props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": mapping(geom), "properties": props}
