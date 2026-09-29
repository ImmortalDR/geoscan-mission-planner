# H1 — критерии приёмки по модулям

**Статус:** норматив для H1.  
**Каноническая реализация:** [`h1_coverage/`](h1_coverage/) (с нуля; аудит P0 закрыты).  
**Машиночитаемо:** [`contracts/h1_modules.v1.json`](contracts/h1_modules.v1.json)  
**Входные данные:** [`h1_coverage/fixtures/conformance/`](h1_coverage/fixtures/conformance/) + [`fixtures/h1_modules/INDEX.md`](fixtures/h1_modules/INDEX.md)  
**Тесты (канон):** `h1_coverage/tests/test_m01_*.py` … `test_m13_*.py` (+ `test_module_registry.py`)  
**Реестр модулей:** `h1_coverage.modules.MODULES` · чекеры `h1_coverage.contracts`  
**Legacy gmp:** `h3/tests/unit/test_h1_module_acceptance.py`  
**Шов H1↔H2:** [`../docs/contract/H1_H2.md`](../docs/contract/H1_H2.md)

---

## Как читать

У каждого модуля:

| Поле | Смысл |
|------|--------|
| **Код** | Где живёт в `h1_coverage/` (канон) / legacy `gmp/` |
| **Контракт** | Что на входе / что на выходе (инварианты) |
| **Приёмка** | Чеклист «можно ставить галочку» |
| **Данные** | Какой сценарий/файл гонять |
| **Тест** | Имя pytest |

Правило: модуль **принят**, если зелёны его unit-тесты приёмки **и** не сломан шов `gmp.h1_h2.v1` на bundle (для M13).

---

## M1 — SceneLoader

**Код:** `h1_coverage/io/scene.py`  
**Контракт id:** `h1.m1.scene_loader.v1`

**Вход:** каталог сцены (`survey_areas.geojson` обязателен; NFZ, sites, mission, fleet, DEM — по наличию).  
**Выход:** `Scene` с валидной геометрией, ≥1 job, ≥1 site.

**Приёмка**
- [x] Без `survey_areas.geojson` → ошибка, не тихий пустой Scene
- [x] Полигон читается; `is_valid` после `make_valid`
- [x] Есть ≥1 площадка с ролью старта/посадки
- [x] CRS сцены выбран по центроиду (UTM)

**Данные:** `h1_coverage/fixtures/conformance/S00_smoke_rgb/` (+ S02 holes)  
**Тест:** `test_m1_scene_loader_s00`

---

## M2 — GeoProjector

**Код:** `h1_coverage/geo.py` (`CrsPipeline`)  
**Контракт id:** `h1.m2.geo_projector.v1`

**Вход:** lon/lat WGS-84.  
**Выход:** метры `EPSG:326xx` / обратно.

**Приёмка**
- [x] Москва ~37.6, 55.75 → `EPSG:32637`
- [x] Round-trip lon/lat → xy → lon/lat, ошибка &lt; 1e-6° (+ ≪ 1 м)
- [x] Координаты задач в метрах не выглядят как lon/lat (эвристика контракта H1↔H2)

**Данные:** `fixtures/h1_modules/m2_roundtrip_points.json`  
**Тест:** `test_m2_crs_roundtrip`

---

## M3 — TerrainModel

**Код:** `h1_coverage/io/dem.py` (`DemSampler`)  
**Контракт id:** `h1.m3.terrain.v1`

**Вход:** `dem.tif` + metric CRS.  
**Выход:** высота земли в точке; min/max/mean по растру.

**Приёмка**
- [x] DEM из S00 открывается
- [x] `elevation(x,y)` возвращает finite float
- [x] Нет файла → Scene без DEM / warning (не падение всего пайплайна на smoke)

**Данные:** `S00_smoke_rgb/dem.tif`  
**Тест:** `test_m3_dem_sample_s00`

---

## M4 — UAV Knowledge Base

**Код:** `h1_coverage/kb/catalog.py`, `seed.yaml`  
**Контракт id:** `h1.m4.uav_kb.v1`  
**Связь:** `docs/contract/H1_H2.md` §2.4.1

**Вход:** YAML seed.  
**Выход:** профили с provenance; ≥10 моделей.

**Приёмка**
- [x] Есть профили линейки Geoscan: `geoscan_201`, `geoscan_401*` (в seed: `geoscan_401_geo`), `geoscan_701`, `geoscan_801`, `geoscan_gemini`
- [x] У фактов есть provenance (source / confidence)
- [x] `operational_*` после derating, не сырой passport max как лимит

**Данные:** `src/gmp/kb/uav_catalog_seed_v2.yaml`  
**Тест:** `test_m4_kb_mvp_models`

---

## M5 — SurveyModel (GSD → полоса)

**Код:** `h1_coverage/coverage/survey.py`  
**Контракт id:** `h1.m5.survey.v1`

**Вход:** GSD, overlap, камера.  
**Выход:** `agl_m ≥ 40`, `swath_spacing_m > 0`.

**Приёмка**
- [x] `agl_for_gsd` ↔ `gsd_for_agl` обратимы
- [x] При side_overlap=0.7 spacing ≈ footprint_w × 0.3
- [x] `agl_m` никогда &lt; 40 после derive

**Данные:** `fixtures/h1_modules/m5_payload_rgb.json` + `S00/.../payload_catalog.json`  
**Тест:** `test_m5_gsd_and_swath`

---

## M6 — CoverageEngine (галсы)

**Код:** `h1_coverage/coverage/engine.py`, `sweep.py`, `backend.py`  
**Контракт id:** `h1.m6.coverage_engine.v1`

**Вход:** `Scene` после exclusions.  
**Выход:** список трансектов / tasks; `coverage_percent` на plannable area.

**Приёмка**
- [x] S00: `build_coverage` → `task_count ≥ 1`, `coverage_percent ≈ 100` (на effective area)
- [x] Длины трансектов &gt; 0
- [x] Галсы в метрах CRS сцены

**Данные:** `S00_smoke_rgb`  
**Тест:** `test_m6_coverage_s00`

---

## M7 — Decomposition / holes

**Код:** `h1_coverage/coverage/decomp.py`, `sweep.py`  
**Контракт id:** `h1.m7.decomp_holes.v1`

**Вход:** Polygon with holes / MultiPolygon (S02).  
**Выход:** галсы, середины которых не лежат внутри отверстий.

**Приёмка**
- [x] S02 загружается, jobs с отверстиями или multipart
- [x] После coverage нет пересечения галс-середины с hole (жёсткий assert)
- [x] `coverage_percent` считается по effective, не по «дырявому» сырому контуру без учёта hole

**Данные:** `S02_multipolygon_holes`  
**Тест:** `test_m7_holes_s02`

---

## M8 — NFZManager

**Код:** `h1_coverage/coverage/exclusions.py`  
**Контракт id:** `h1.m8.nfz.v1`  
**Гарантия H1↔H2:** G1

**Вход:** survey ∩ allowed − NFZ(buffer) − obstacles.  
**Выход:** `ExclusionResult.effective`; reasons.

**Приёмка**
- [x] Effective area ⊆ allowed (с учётом inset)
- [x] Effective ∩ hard_NFZ.is_empty (после buffer)
- [x] На S08: галсы coverage не пересекают NFZ

**Данные:** `S08_fixed_wing_turnaround`, `fixtures/h1_modules/m8_nfz_metric.json`  
**Тест:** `test_m8_nfz_no_intersect`

---

## M9 — FixedWingBuffer

**Код:** `h1_coverage/coverage/fixed_wing.py`  
**Контракт id:** `h1.m9.fixed_wing.v1`  
**Гарантия:** G5 через feasibility

**Вход:** трансекты + NFZ + `turnaround_buffer_m`.  
**Выход:** нарушения манёвра / флаг unsafe для FW.

**Приёмка**
- [x] S08: `expected_assertions.fixed_wing_maneuver_buffer_must_be_checked`
- [x] Если буфер бьёт NFZ → задача не eligible для fixed_wing (или explicit infeasible path)

**Данные:** `S08_fixed_wing_turnaround`  
**Тест:** `test_m9_fixed_wing_s08`

---

## M10 — CoverageCandidateGenerator

**Код:** `h1_coverage/coverage/candidates.py`  
**Контракт id:** `h1.m10.candidates.v1`

**Вход:** area, wind, spacing.  
**Выход:** ≥2 кандидата с разными углами; score учитывает ветер.

**Приёмка**
- [x] S09: `wind_direction_must_affect_sweep_score` — лучший кандидат зависит от `wind.direction`
- [x] keep_candidates ≥ 2 на нетривиальной площади
- [x] Ranking стабилен при том же seed/входе

**Данные:** `S09_wind_feasibility`  
**Тест:** `test_m10_wind_scores_s09`

---

## M11 — AtomicTaskBuilder

**Код:** `h1_coverage/coverage/atomic.py`  
**Контракт id:** `h1.m11.atomic_task.v1`  
**Гарантии:** G2, G3, G6

**Вход:** упорядоченные трансекты.  
**Выход:** `AtomicTask[]` с согласованными entry/exit/lengths.

**Приёмка**
- [x] `entry == transects[0].coords[0]`, `exit == last`
- [x] `|survey_length_m - sum(lengths)| < 1e-3` (или согласованный допуск кода)
- [x] ids уникальны; повторный прогон того же S00 → те же id (детерминизм)

**Данные:** `S00_smoke_rgb`  
**Тест:** `test_m11_atomic_invariants_s00`

---

## M12 — FeasibilityMatrix

**Код:** `h1_coverage/feasibility.py`  
**Контракт id:** `h1.m12.feasibility.v1`

**Вход:** tasks + fleet + wind.  
**Выход:** eligible set; причины отказа.

**Приёмка**
- [x] S09: борта с `max_wind < wind` не в eligible
- [x] S11: payload mismatch → не назначаются (нет violations при корректном фильтре)
- [x] Ветер **не** меняет численную energy-модель (assert из S09)

**Данные:** `S09_wind_feasibility`, `S11_payload_compatibility`  
**Тест:** `test_m12_feasibility_s09_s11`

---

## M13 — BundleExporter

**Код:** `h1_coverage/bundle.py`, `pipeline.py`, `cli.py`  
**Контракт id:** `h1.m13.bundle.v1` = schema `gmp.h1_h2.v1`

**Вход:** Scene + CoverageResult + feasibility.  
**Выход:** `fixtures/h1_h2/*.bundle.json`

**Приёмка**
- [x] `validate_h1_h2_contract.py` exit 0
- [x] Live export S00/S02/S08/S09/S11 через `python -m gmp.cli coverage`
- [x] G2/G4/G5/G6/G7 в `validate_bundle_dict` / валидаторе
- [x] Smoke fixture `S00_contract_smoke.bundle.json` валидна

**Данные:** live bundles + `S00_contract_smoke.bundle.json`  
**Тест:** `test_m13_bundle_contract`, `test_m13_live_export_s00`

```bash
PYTHONPATH=src python -m gmp.cli coverage \
  --scene datasets/geoscan_customer_conformance/S00_smoke_rgb \
  --output fixtures/h1_h2/S00_smoke_rgb.bundle.json \
  --export-geojson fixtures/h1_h2/S00_smoke_rgb_transects.geojson
```


---

## Сводная таблица сценарий → модули

| Сценарий | Модули primary |
|----------|----------------|
| S00 | M1 M2 M3 M5 M6 M11 M13 |
| S02 | M1 M7 M8 |
| S08 | M8 M9 M12 |
| S09 | M10 M12 |
| S11 | M4 M12 |
| KB seed | M4 |

---

## Как прогнать

```bash
# Канон (h1_coverage)
cd h1
source .venv/bin/activate
pip install -e "./h1_coverage[dev]"
pytest h1_coverage/tests -v
h1-coverage run \
  --scene h1_coverage/fixtures/conformance/S00_smoke_rgb \
  --output /tmp/S00.bundle.json

# Legacy gmp (reference)
cd ../h3
PYTHONPATH=src .venv/bin/pytest tests/unit/test_h1_module_acceptance.py -v
PYTHONPATH=src .venv/bin/python scripts/validate_h1_h2_contract.py
```
