# h1-coverage — модульный H1 для Geoscan

Пакет: `h1/h1_coverage`  
Контракт выхода: `gmp.h1_h2.v1`  
Модульные контракты: `../contracts/h1_modules.v1.json` + `h1_coverage.contracts`  
Приёмка: `../docs/acceptance.md`

## Модули M1–M13

| ID | Код | Контракт | Тест |
|----|-----|----------|------|
| M1 | `io/scene.py` | `h1.m1.scene_loader.v1` | `test_m01_scene_loader.py` |
| M2 | `geo.py` | `h1.m2.geo_projector.v1` | `test_m02_geo.py` |
| M3 | `io/dem.py` | `h1.m3.terrain.v1` | `test_m03_terrain.py` |
| M4 | `kb/` | `h1.m4.uav_kb.v1` | `test_m04_kb.py` |
| M5 | `coverage/survey.py` | `h1.m5.survey.v1` | `test_m05_survey.py` |
| M6 | `coverage/engine.py` + `sweep.py` | `h1.m6.coverage_engine.v1` | `test_m06_coverage.py` |
| M7 | `coverage/decomp.py` | `h1.m7.decomp_holes.v1` | `test_m07_decomp.py` |
| M8 | `coverage/exclusions.py` | `h1.m8.nfz.v1` | `test_m08_nfz.py` |
| M9 | `coverage/fixed_wing.py` | `h1.m9.fixed_wing.v1` | `test_m09_fixed_wing.py` |
| M10 | `coverage/candidates.py` | `h1.m10.candidates.v1` | `test_m10_candidates.py` |
| M11 | `coverage/atomic.py` | `h1.m11.atomic_task.v1` | `test_m11_atomic.py` |
| M12 | `feasibility.py` | `h1.m12.feasibility.v1` | `test_m12_feasibility.py` |
| M13 | `bundle.py` | `h1.m13.bundle.v1` | `test_m13_bundle.py` |

Реестр: `h1_coverage.modules.MODULES`. Чекеры: `h1_coverage.contracts`.

## Установка / тесты / запуск

Установите общее окружение по [корневому README](../../README.md).
Из корня репозитория:

```bash
.venv/bin/python -m pytest -c pyproject.toml h1/h1_coverage/tests -q
.venv/bin/python scripts/validate_h1_h2_contract.py
```

### M6 / Fields2Cover

Native `fields2cover` often fails to build (TinyXML2/Eigen). Default backend is
`diy_lawnmower_v1`. Optional: `pip install -e "./h1_coverage[f2c]"` when system
deps are present — see `coverage/backend.py` / `backend_status()`.
