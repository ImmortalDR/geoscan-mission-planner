# H1 inputs — все входы в Scene & Coverage

Канон загрузки: [`h1_coverage.io.scene.load_scene`](../h1_coverage/src/h1_coverage/io/scene.py)
Машиночитаемый реестр: [`h1_coverage.io.manifest`](../h1_coverage/src/h1_coverage/io/manifest.py)
Проверка папки: `h1-coverage inspect --scene <dir>`

---

## 1. Точки входа (как вызвать H1)

| Канал | Как | Что на входе |
|--------|-----|--------------|
| CLI run | `h1-coverage run --scene <dir> --output bundle.json` | каталог сцены |
| CLI inspect | `h1-coverage inspect --scene <dir>` | только аудит файлов + load |
| CLI modules | `h1-coverage modules` | список M1–M13 + реестр файлов |
| CLI fixtures | `h1-coverage export-fixtures --out-dir …` | conformance dirs → live bundles |
| Python dir | `run_h1(scene_dir)` / `run_h1_to_file` | каталог |
| Python Scene | `run_h1_scene(scene)` | уже собранный `Scene` (тесты, UI) |
| Python load | `load_scene(dir)` | только M1 → `Scene` |
| gmp bridge | `gmp.coverage.engine.build_coverage(scene)` | `scene.source_dir` → `run_h1` |
| gmp shim | `gmp.h1.run_h1` / `gmp` CLI `coverage` | то же |

**Не вход H1:** готовый `*.bundle.json` (это *выход* M13 для H2).
**Не вход H1:** HTTP/API upload — пока нет; кладите файлы в каталог и зовите CLI/Python.

---

## 2. Каталог сцены (основной формат)

Как в customer conformance (`h1_coverage/fixtures/conformance/S00_…` или `h3/datasets/…`).

| Файл | Обязательность | Роль в H1 |
|------|----------------|-----------|
| `survey_areas.geojson` | **required†** | зона съёмки WGS-84 |
| `landing_sites.geojson` | **required†** | старт/посадка |
| `scene.kml` | optional / †alt | fallback survey+sites если GeoJSON нет |
| `no_fly_zones.geojson` | optional | hard/soft NFZ → effective area |
| `allowed_airspace.geojson` | optional | clip съёмки |
| `obstacles.geojson` | optional | soft exclusion (+ `horizontal_buffer_m`) |
| `temporal_airspace.geojson` | optional | грузим + warn; окна — зона H2/H3 |
| `dem.tif` | optional | DEM; нет → flat + warn |
| `mission.json` | optional | ветер, objectives, window, flags |
| `fleet.json` | optional | борта → KB |
| `payload_catalog.json` | optional | GSD / overlap / AGL |
| `metadata.json` | optional | `scenario_id` |
| `expected_assertions.json` | optional | только тесты приёмки |

† Минимум: **(survey GeoJSON или KML survey)** и **(landing GeoJSON или KML points)**.

### Геометрия survey

- `Polygon` / `MultiPolygon` — mode `area` (дыры = interiors)
- `LineString` / `MultiLineString` + `half_width_m` (default 40) → corridor buffer, mode `corridor`
- `GeometryCollection` — areal parts preferred; else longest line
- null / empty features — пропускаются
- `Point` как survey — отказ
- props: `id`, `survey_type`, `payload_profile`, `mode` / `survey_mode`, `half_width_m`

### Сайты

`role`: `both` | `start` | `landing` | `reserve`. Нужен ≥1 start/landing/both.
Polygon pad → centroid. Только `reserve` → ошибка.

### Ошибки vs warn

| Ситуация | Поведение |
|----------|-----------|
| Нет survey / sites | `SceneError` |
| Битый JSON (mission/geojson/…) | `SceneError` с именем файла |
| Нет DEM | warn, flat |
| Soft NFZ / obstacles | cut из effective |
| Temporal airspace | load + warn (не режет расписание в H1) |
| KML-only survey/sites/NFZ | ok + warn |

---

## 2b. Матрица кейсов файлов (тесты)

Канон: `h1_coverage/tests/test_m01_file_cases.py`

| Кейс | Файл / геометрия | Ожидание |
|------|------------------|----------|
| Polygon area | `survey_areas` Polygon | job mode=area |
| MultiPolygon | MultiPolygon | load ok |
| Polygon + hole | interiors | warn holes |
| LineString corridor | LineString + half_width | mode=corridor |
| MultiLineString | MultiLineString | corridor |
| Multi jobs | 2+ features | N jobs |
| Soft/hard NFZ | `hard` true/false | оба в `no_fly_zones` |
| Obstacles + buffer | `horizontal_buffer_m` | soft exclusion |
| Temporal | `temporal_airspace.geojson` | warn pass-through |
| Empty optional FC | features=[] | ok |
| Site polygon | landing Polygon | centroid Point |
| Roles start/landing/reserve | props.role | preserved |
| Null geometry | skipped | remaining jobs |
| Point survey | Point | SceneError |
| Bad mission.json | invalid JSON | SceneError |
| Bad geojson | invalid JSON | SceneError |
| Reserve-only sites | role=reserve | SceneError |
| KML LineString | scene.kml #survey | corridor |
| KML NFZ | styleUrl #nfz | NFZ from KML |
| S01 live | conformance | obstacles+temporal |
| S03 live | conformance | temporal ≥1 |
| Metres as lon/lat | UTM-like coords in GeoJSON | `SceneError` |
| Self-intersecting | bowtie polygon | repair + warn |
| Duplicate job/site id | same `id` twice | `SceneError` |
| Empty `fleet.json` | `"uavs": []` | `SceneError` |
| `require_fleet` | mission flag, no fleet | `SceneError` |
| Corrupt `dem.tif` | garbage bytes | warn, flat DEM |
| Feature without `type` | skipped | warn + remaining |
| UTF-16 / UTF-8 BOM | mission / geojson | load ok |
| Giant GeoJSON | over size cap | `SceneError` |

Проверка: `pytest h1_coverage/tests/test_m01_file_cases.py h1_coverage/tests/test_m01_buggy_inputs.py -q`

---

## 3. Поток нормализации (M1)

```text
WGS-84 layers → CRS UTM by survey centroid → metric Scene
  jobs[].geom (m)
  sites[].point (m)
  NFZ / allowed / obstacles (m)
  dem sampler in metric
  warnings[] (KML fallback, no DEM, temporal present, …)
```

Дальше пайплайн: M5…M13 → `H1H2Bundle` (`gmp.h1_h2.v1`).

---

## 4. Что сознательно не принимаем (пока)

| Вход | Статус |
|------|--------|
| Одиночный `.geojson` / `.kml` без каталога | нет — нужен dir (или собрать `Scene` и `run_h1_scene`) |
| Shapefile / GPX / QGC plan | нет |
| ZIP upload API | нет (можно распаковать вручную в dir) |
| Готовый bundle как «пересчитать» | нет — это выход |
| Lon/lat в задачах наружу | запрещено контрактом (метры CRS) |

---

## 5. Быстрая проверка

```bash
h1-coverage inspect --scene h1_coverage/fixtures/conformance/S00_smoke_rgb
h1-coverage run --scene …/S00_smoke_rgb --output /tmp/S00.bundle.json --no-strict-coverage
```

```python
from h1_coverage.io.inspect import inspect_scene_dir
from h1_coverage.pipeline import run_h1, run_h1_scene

print(inspect_scene_dir("…/S00_smoke_rgb")["summary"])
r = run_h1("…/S00_smoke_rgb")
```
