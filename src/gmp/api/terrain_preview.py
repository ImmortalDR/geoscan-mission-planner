"""Bounded, georeferenced height previews of the actual scene GeoTIFF."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds, transform

from .inputs import open_geotiff, read_json, terrain_covers


def terrain_summary(directory: Path) -> dict:
    available = (directory / "dem.tif").is_file()
    metadata = read_json(directory / "metadata.json")
    terrain = metadata.get("terrain") or {}
    if not available:
        source, label = "missing", "Рельеф не приложен"
    elif terrain.get("kind") == "user_supplied":
        source, label = "user", "Рельеф из пользовательского файла"
    elif terrain.get("kind") == "synthetic_flat_surface":
        source, label = "test", "Тестовый рельеф"
    elif terrain.get("kind") == "real_dsm" or metadata.get("real_elevation"):
        source, label = "copernicus", "Реальный рельеф · Copernicus DSM"
    else:
        source, label = "file", "Рельеф из файла сцены"
    return dict(available=available, source=source, label=label,
                covers_scene=terrain_covers(directory) if available else False,
                filename="dem.tif" if available else None)


def terrain_preview(path: Path):
    stat = path.stat()
    return _render(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=8)
def _render(path: str, mtime: int, size: int):
    with open_geotiff(Path(path)) as source:
        if source.count != 1 or not source.crs or source.width * source.height > 16_000_000:
            raise ValueError("DEM must have one band, a CRS and at most 16 million pixels")
        values = source.read(1, masked=True).astype("float32").filled(np.nan)
        valid = values[np.isfinite(values)]
        if not valid.size:
            raise ValueError("DEM has no valid elevation values")
        low, high = float(valid.min()), float(valid.max())
        left, bottom, right, top = transform_bounds(source.crs, "EPSG:3857", *source.bounds, densify_pts=21)
        if not np.isfinite([left, bottom, right, top]).all() or right <= left or top <= bottom:
            raise ValueError("DEM cannot be displayed on this map")
        scale = 768 / max(right-left, top-bottom)
        width, height = max(1, round((right-left)*scale)), max(1, round((top-bottom)*scale))
        pixels = np.full((height, width), np.nan, dtype="float32")
        reproject(values, pixels, src_transform=source.transform, src_crs=source.crs,
                  src_nodata=np.nan, dst_transform=from_bounds(left,bottom,right,top,width,height),
                  dst_crs="EPSG:3857", dst_nodata=np.nan, resampling=Resampling.nearest,
                  num_threads=1)
    mask = np.isfinite(pixels)
    normalized = np.clip((np.nan_to_num(pixels, nan=low)-low)/(high-low), 0, 1) if high > low else np.full_like(pixels, .5)
    stops = np.array([[44,105,164],[69,157,116],[209,206,127],[169,114,72],[244,239,229]])
    rgba = np.zeros((height,width,4), dtype="uint8")
    for channel in range(3):
        rgba[:,:,channel] = np.interp(normalized, np.linspace(0,1,len(stops)), stops[:,channel]).astype("uint8")
    rgba[:,:,3] = mask.astype("uint8")*255
    image = BytesIO()
    Image.fromarray(rgba).save(image, format="PNG")
    lon, lat = transform("EPSG:3857", "EPSG:4326", [left,right,right,left], [top,top,bottom,bottom])
    info = dict(min_m=low, max_m=high, flat=high==low, width=width, height=height,
                coordinates=[list(pair) for pair in zip(lon,lat)],
                colors=["#2c69a4","#459d74","#d1ce7f","#a97248","#f4efe5"],
                units="м · высота поверхности в системе исходного файла")
    return info, image.getvalue()
