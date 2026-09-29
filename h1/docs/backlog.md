# H1 backlog — путь к максимуму

**Канон кода:** [`h1_coverage/`](h1_coverage/)  
**Архитектура:** [`docs/architecture.md`](docs/architecture.md)  
**Приёмка:** [`docs/acceptance.md`](docs/acceptance.md)  
**Статус реализации:** [`docs/coverage.md`](docs/coverage.md)

Как вести прогресс: меняй колонку **Статус** (`todo` → `doing` → `done` / `wont`) и дату в **Done**.  
Не тащи сюда зону H2/H3 (назначение борта, 4D, сертификат) — см. ARCHITECTURE §9 / §17.

---

## Легенда

| Статус | Смысл |
|--------|--------|
| `done` | Есть код + тест/валидатор; человек может воспроизвести |
| `doing` | В работе сейчас |
| `todo` | Нужно |
| `wont` | Сознательно не делаем в H1 (чужая зона / out of scope) |

| Приоритет | Смысл |
|-----------|--------|
| **P0** | Ломает демо / контракт / доверие жюри |
| **P1** | «Как у заказчика» — полнота относительно архитектуры/ТЗ |
| **P2** | Вау / polish / research — после P0–P1 |

---

## Сводка прогресса

| Слой | Done | Todo | Примечание |
|------|------|------|------------|
| Основа стыковки (M1–M13 MVP) | ✅ | — | Bundle `gmp.h1_h2.v1`, pytest зелёный |
| Архитектурный паритет (F2C, BCD+, DEM→AGL) | A-* P1/P2 ✅ | A-01/A-07 wont | DIY; BCD; local AGL; terrain meta |
| Сценарии заказчика S01/S03… | S-* ✅ (кроме S-05) | — | S-05 wont H2 |
| Вау для демо | W-01…W-07 ✅ | — | |
| ТЗ-расширения | T-* ✅ (кроме T-05) | — | T-05 wont H2 |
| Качество | Q-01…Q-06 ✅ | — | golden + perf |

*Обновляй эту таблицу при закрытии эпиков.*

**Последнее обновление бэклога:** 2026-09-20 (P2 закрыт: S-04/06/07, A-06, Q-03/04; A-07 wont)

---

## 0. Уже сделано (база — не трогать без регрессии)

| ID | Что | Доказательство |
|----|-----|----------------|
| B-01 | Модульный `h1_coverage` M1–M13 + реестр + контракт-чекеры | `h1_coverage.modules`, `contracts/` |
| B-02 | Overshoot clip в effective (аудит P0) | `test_m6_overshoot_*` |
| B-03 | Единый feasibility | `feasibility.py` + S08/S09/S11 |
| B-04 | Coverage % + strict warning | `CoverageConfig`, engine |
| B-05 | KML fallback / companion | `io/kml.py`, `test_m1_kml_fallback` |
| B-06 | BCD-lite для дыр | `decomp.bcd_vertical_cells` |
| B-07 | DIY SweepBackend + честный статус F2C | `backend_status()`, §14 |
| B-08 | Live S00 → `validate_h1_h2_contract.py` OK | CLI `h1-coverage run` |

---

## A. Архитектурный паритет (код ↔ ARCHITECTURE)

| ID | Статус | P | Модуль | Задача | DoD | Done |
|----|--------|---|--------|--------|-----|------|
| A-01 | wont | P1 | M6 | Native Fields2Cover — **не делаем** (DIY достаточно для демо/H2) | — | 2026-09-20 |
| A-02 | done | P2 | M6 | Headlands / отступ края из F2C или Shapely-аналог | метрика «линия не ближе X м к границе» в тесте | |
| A-03 | done | P1 | M9 | Дуга разворота (Dubins / RS) вместо кружка на endpoint | S08 ловит unsafe на межгалсовой дуге; рисунок в мета | |
| A-04 | done | P1 | M7 | BCD: merge мелких ячеек + обход порядка ячеек (идеи ETH, без ROS) | меньше обрывков на S02; тест длины transition | 2026-09-20 |
| A-05 | done | P1 | M3 | DEM влияет на AGL/GSD предупреждения сильнее (рельеф → local AGL check) | warning/fail policy на холмах; тест на synthetic DEM | 2026-09-20 |
| A-06 | done | P2 | M3 | Опциональный terrain-following adapter (только warnings/meta, не 3D spline) | `extensions.h1.terrain` без ломки schema | 2026-09-20 |
| A-07 | wont | P2 | M1 | geopandas/GDAL path для «тяжёлых» KML/MultiGeometry | DIY kml уже покрывает conformance; лишняя зависимость | 2026-09-20 |
| A-08 | done | P1 | M4 | Полный KB seed ближе к gmp v2 (больше моделей, derating docs) | provenance + operational ≠ passport в тесте | 2026-09-20 |
| A-09 | done | P1 | docs | Синхронизировать §6 файловую структуру с `h1_coverage/` | ARCHITECTURE §6 указывает на канон | 2026-09-20 |

---

## S. Сценарии conformance (заказчик)

| ID | Статус | P | Сценарий | Задача | DoD | Done |
|----|--------|---|----------|--------|-----|------|
| S-00 | done | P0 | S00 | Smoke RGB E2E | bundle + coverage≥99 | 2026-09-19 |
| S-02 | done | P0 | S02 | Holes / MultiPolygon | midpoints not in holes | 2026-09-19 |
| S-08 | done | P0 | S08 | FW + NFZ | G5 / fixed_wing_safe | 2026-09-19 |
| S-09 | done | P0 | S09 | Wind feasibility + candidate scores | wind filters + ranking | 2026-09-19 |
| S-11 | done | P0 | S11 | Payload mismatch | ineligible reasons | 2026-09-19 |
| S-01 | done | P1 | S01 | ~100 км² stress | time budget + memory; coverage gate | |
| S-03 | done | P2 | S03 | Temporal airspace | H1: warn/pass-through meta; логика окон — H2 | 2026-09-20 |
| S-04 | done | P2 | S04 | Multi-sortie energy hints | только coarse endurance в M12 (не расписание) | 2026-09-20 |
| S-05 | wont | — | S05 | 4D deconfliction | зона H2/H3 | |
| S-06 | done | P2 | S06 | Alternative site recommend | hint в `warnings` / extensions | 2026-09-20 |
| S-07 | done | P2 | S07 | Reserve landing | site roles уже есть — assert на bundle | 2026-09-20 |
| S-10 | done | P1 | S10 | Different start/end | `allow_different_start_end` в bundle + тест | |

---

## T. Дыры относительно ТЗ (ARCHITECTURE §17)

| ID | Статус | P | Задача | DoD | Done |
|----|--------|---|--------|-----|------|
| T-01 | done | P1 | Жёсткий Gate B: coverage&lt;99.9% → warning всегда; strict → fail/INFEASIBLE | S11/S01 не молчат | |
| T-02 | done | P1 | LiDAR / geophysics: line_spacing как first-class (уже частично) + фикстура | отдельный job + тест swath | |
| T-03 | done | P1 | Узкий коридор (дорога / ЛЭП): mode «strip along line» | новый fixture + AtomicTask | |
| T-04 | done | P1 | Несколько survey jobs в одном bundle (RGB+LiDAR) | S00-like multi-job E2E | |
| T-05 | wont | — | Запретка по времени суток как solver constraint | H2/H3 | |
| T-06 | done | P2 | Obstacles layer отдельно от NFZ (если есть в данных) | reasons в exclusions | 2026-09-20 |

---

## W. Вау для демо (не ломают `AtomicTask`)

| ID | Статус | P | Задача | DoD | Done |
|----|--------|---|--------|-----|------|
| W-01 | done | P1 | Export галсов GeoJSON (`--export-geojson`) | файл + CLI | |
| W-02 | done | P1 | HTML-карта галсов / NFZ / sites | открыл в браузере за &lt;5 с | |
| W-03 | done | P1 | Таблица top-K углов (длина, turns, wind score) | в `coverage_meta` + print | |
| W-04 | done | P1 | Человекочитаемые `ineligible_reasons` («ветер слишком сильный для 701») | RU/EN строки в bundle или sidecar | |
| W-05 | done | P2 | Круги/дуги разворота FW на карте (S08) | HTML overlay | |
| W-06 | done | P2 | Цвет покрытия N% на схеме | зелёный/красный | |
| W-07 | done | P2 | Отчёт «сравнили 3 угла» markdown/HTML | один артефакт для жюри | |

---

## Q. Качество / сопровождение

| ID | Статус | P | Задача | DoD | Done |
|----|--------|---|--------|-----|------|
| Q-01 | done | P1 | Выложить live `fixtures/h1_h2/*.bundle.json` из `h1_coverage` для H2 | S00/S02/S08/S09/S11 + validate exit 0 | |
| Q-02 | done | P1 | `scripts/validate_h1_module_contracts.py` для канона (JSON ↔ pytest ↔ MODULES) | CI-friendly exit code | |
| Q-03 | done | P2 | Snapshot/golden transects на S00 (детерминизм геометрии) | pytest + tolerance | 2026-09-20 |
| Q-04 | done | P2 | Perf budget: S00 &lt; N с; S01 &lt; M с | бенч в CI optional | 2026-09-20 |
| Q-05 | done | P1 | Обновить `docs/coverage.md` при каждом закрытом эпике | ссылка на ID бэклога | |
| Q-06 | done | P2 | DEMO_PATH.md шаг «H1 coverage» | клик/команда для судьи | |

---

## Порядок работ (рекомендуемый)

Делать **сверху вниз**; не начинать W/T research, пока P0/P1 стыковки красные.

```text
✅ H1 backlog P0/P1/P2 закрыт
wont: A-01 F2C, A-07 geopandas, S-05/T-05 → H2
Дальше: склейка / H2, не H1 polish ради polish.
```

---

## Как отметить прогресс (агент / человек)

1. Взять ID (например `W-02`) → статус `doing`.
2. Сделать код + тест/артефакт.
3. Статус `done`, дата в **Done**, одна строка в [`docs/coverage.md`](docs/coverage.md).
4. Если сознательно не делаем — `wont` + причина в одну фразу.

Не закрывать пункт только потому что «агент сказал ok» — нужен воспроизводимый DoD.
