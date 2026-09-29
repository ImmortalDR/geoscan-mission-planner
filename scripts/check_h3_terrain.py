#!/usr/bin/env python3
"""Exercise real DSM download/cache and loose-file overlays through HTTPS."""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import time
import zipfile

import httpx
import rasterio
from rasterio.io import MemoryFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    values = dict(line.split("=", 1) for line in args.env_file.read_text().splitlines() if line and not line.startswith("#"))
    rows = []
    with httpx.Client(base_url=args.url, timeout=300) as client:
        auth = client.post("/api/v1/auth/login", json={"code": values["GMP_ACCESS_CODE"]})
        auth.raise_for_status()
        client.headers["X-CSRF-Token"] = auth.json()["csrf_token"]

        def post(path, **kwargs):
            response = client.post(path, **kwargs)
            response.raise_for_status()
            return response.json()

        source = post("/api/v1/scenes?scenario_id=S00_smoke_rgb")
        started = time.monotonic()
        terrain = post(f"/api/v1/scenes/{source['id']}/terrain")
        assert terrain["validation"]["valid"], terrain["validation"]
        assert terrain["metadata"]["real_elevation"]
        provenance = terrain["metadata"]["terrain"]
        assert provenance["sources"] and all(len(s["sha256"]) == 64 for s in provenance["sources"])
        rows.append({"check": "real DSM fetched", "seconds": time.monotonic() - started,
                     "scene_id": terrain["id"], "sources": provenance["sources"]})
        response = client.get(f"/api/v1/scenes/{terrain['id']}/download")
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            dem = archive.read("dem.tif")
            survey = archive.read("survey_areas.geojson")
        with MemoryFile(dem) as memory:
            with memory.open() as raster:
                assert raster.crs and raster.count == 1
                values = raster.read(1, masked=True)
                assert values.count() == raster.width * raster.height
                rows.append({"check": "nonempty GeoTIFF", "shape": list(values.shape),
                             "minimum_m": float(values.min()), "maximum_m": float(values.max())})
        repeated = post(f"/api/v1/scenes/{terrain['id']}/terrain")
        assert repeated["metadata"]["terrain"]["sources"] == provenance["sources"]
        rows.append({"check": "source cache reused", "scene_id": repeated["id"]})
        for filename, content in [("dem.tif", dem), ("survey_areas.geojson", survey)]:
            overlaid = post(f"/api/v1/scenes/upload?base_scene_id={terrain['id']}", files=[("files", (filename, content))])
            assert overlaid["validation"]["valid"], overlaid["validation"]
            rows.append({"check": f"loose {filename} overlay", "scene_id": overlaid["id"]})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"passed": True, "checks": rows}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
