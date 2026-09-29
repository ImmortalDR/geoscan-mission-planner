"""GeoJSON reading/writing. External geometry is always WGS-84."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from ..geo import CrsPipeline, make_valid


class GeoJsonError(ValueError):
    pass


def read_features(path: str | Path) -> list[dict[str, Any]]:
    """Read a FeatureCollection / Feature / bare geometry into a feature list."""
    p = Path(path)
    if not p.exists():
        raise GeoJsonError(f"file not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise GeoJsonError(f"{p}: invalid JSON: {exc}") from exc

    crs = data.get("crs")
    if isinstance(crs, dict):
        name = json.dumps(crs)
        if "4326" not in name and "CRS84" not in name.upper():
            raise GeoJsonError(f"{p}: only EPSG:4326/CRS84 GeoJSON is accepted, got {name}")

    t = data.get("type")
    if t == "FeatureCollection":
        feats = data.get("features") or []
    elif t == "Feature":
        feats = [data]
    elif t in ("Polygon", "MultiPolygon", "Point", "LineString", "MultiLineString", "MultiPoint"):
        feats = [{"type": "Feature", "properties": {}, "geometry": data}]
    else:
        raise GeoJsonError(f"{p}: unsupported GeoJSON type {t!r}")

    out = []
    for i, f in enumerate(feats):
        geom = f.get("geometry")
        if geom is None:
            raise GeoJsonError(f"{p}: feature #{i} has no geometry")
        out.append({"properties": dict(f.get("properties") or {}), "geometry": geom})
    return out


def read_metric_features(
    path: str | Path, pipeline: CrsPipeline, repair: bool = True
) -> list[tuple[dict[str, Any], BaseGeometry]]:
    """Read features and convert their geometry into the metric planning CRS."""
    out = []
    for f in read_features(path):
        g = shape(f["geometry"])
        g = pipeline.to_metric(g)
        if repair:
            g = make_valid(g)
        out.append((f["properties"], g))
    return out


def feature(geom_wgs84: BaseGeometry, props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "properties": props, "geometry": mapping(geom_wgs84)}


def feature_collection(features: Iterable[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    fc: dict[str, Any] = {"type": "FeatureCollection", "features": list(features)}
    fc.update(extra)
    return fc


def write_feature_collection(path: str | Path, fc: dict[str, Any]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(fc, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def metric_features_to_fc(
    pipeline: CrsPipeline, items: Sequence[tuple[BaseGeometry, dict[str, Any]]]
) -> dict[str, Any]:
    return feature_collection(
        feature(pipeline.to_geographic(g), props) for g, props in items
    )
