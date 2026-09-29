# H1 module fixtures — где брать входы

**Полный каталог входов H1:** [`../../docs/inputs.md`](../../docs/inputs.md)

Канонические **полные сцены**:

```text
h1/h1_coverage/fixtures/conformance/   # symlink → datasets
  S00_smoke_rgb/
  S02_multipolygon_holes/
  S08_fixed_wing_turnaround/
  S09_wind_feasibility/
  S11_payload_compatibility/
  … (S01, S03–S07, S10)

# или оригинал:
h3/datasets/geoscan_customer_conformance/
```

В каждой сцене обычно:

| Файл | Назначение |
|------|------------|
| `survey_areas.geojson` | зона съёмки (WGS-84); LineString → corridor |
| `no_fly_zones.geojson` | NFZ |
| `allowed_airspace.geojson` | разрешённое ВП |
| `obstacles.geojson` | препятствия (soft exclusion) |
| `temporal_airspace.geojson` | временные зоны (load + warn) |
| `landing_sites.geojson` | площадки |
| `dem.tif` | рельеф |
| `fleet.json` | борта сцены |
| `mission.json` | ветер, objectives, окна |
| `payload_catalog.json` | камеры/GSD |
| `expected_assertions.json` | ожидания приёмки сцены |
| `scene.kml` | fallback / companion |

## Локальные мини-фикстуры (этот каталог)

| Файл | Модуль |
|------|--------|
| [`m2_roundtrip_points.json`](m2_roundtrip_points.json) | M2 CRS |
| [`m5_payload_rgb.json`](m5_payload_rgb.json) | M5 Survey |
| [`m8_nfz_metric.json`](m8_nfz_metric.json) | M8 clip в метрах |
| [`module_fixture_map.json`](module_fixture_map.json) | карта модуль → путь |

## Bundle для M13 / H2

```text
h1/fixtures/h1_h2/*.bundle.json
h3/fixtures/h1_h2/S00_contract_smoke.bundle.json
```

## Как открыть сцену в коде

```python
from pathlib import Path
from h1_coverage.io.scene import load_scene
from h1_coverage.io.inspect import inspect_scene_dir
from h1_coverage.pipeline import run_h1

d = Path("h1_coverage/fixtures/conformance/S00_smoke_rgb")
print(inspect_scene_dir(d)["summary"])
scene = load_scene(d)
result = run_h1(d)
```

CLI: `h1-coverage inspect --scene <dir>` · `h1-coverage run --scene <dir> --output out.bundle.json`
