# h2 — Scheduling & 4D Deconfliction

Читает `H1H2Bundle` (`gmp.h1_h2.v1` / `v2` / `v3`), строит `h2.plan.v1`.
Не двигает геометрию задач. Не ставит `uav_id` в bundle.

| Файл | |
|------|--|
| [`docs/routing.md`](docs/routing.md) | Реализация Routing, отдельные галсы, дозарядка и граф на карте |
| [`docs/routing-results.md`](docs/routing-results.md) | Все 18 шаблонов, обе метрики, время расчёта и открытые требования |
| [`docs/routing-solver-task.md`](docs/routing-solver-task.md) | Задание на Routing Solver: граф галсов, ограничения, базы, ресурс и H3 |
| [`docs/annealing.md`](docs/annealing.md) | Отжиг, глубина, критерии, воспроизводимость и замеры |
| [`docs/fixed-estimator.md`](docs/fixed-estimator.md) | Быстрая оценка заданного плана, сетка и замер S17 |
| [`docs/scene.md`](docs/scene.md) | Sidecar сцены |
| [`docs/output.md`](docs/output.md) | Формат плана |
| [`docs/collision.md`](docs/collision.md) | Конфликты |
| [`docs/validation.md`](docs/validation.md) | Валидация |
| [`docs/demo.md`](docs/demo.md) | Demo path |
| [`viewer/`](viewer/) | Карта / UI |

Контракт: [`../docs/contract/`](../docs/contract/).
Где тестить multi-UAV / S01×10: [`../docs/contract/H2_TEST_SCENES.md`](../docs/contract/H2_TEST_SCENES.md).

## Установка

Используется единый venv `/root/.venv` (из корня репозитория):

```bash
# Из корня репозитория:
bash setup.sh          # первый раз
# или отдельно:
/root/.venv/bin/python3 -m pip install -e ".[test]" -q
```

## Тесты

```bash
cd h2
/root/.venv/bin/python3 -m pytest tests/ -q
```

## E2E acceptance (H1 → H2)

```bash
cd h2
/root/.venv/bin/python3 scripts/e2e_acceptance.py
```

Ожидаемый вывод:

```
S00_smoke_rgb FEASIBLE residual= 0 OK
S02_multipolygon_holes FEASIBLE residual= 0 OK
S05_4d_deconfliction FEASIBLE residual= 0 OK
S03_temporal_airspace_daylight FEASIBLE residual= 0 OK
```

## Из корня (Makefile)

```bash
make validate   # контракт + H2 e2e
make test-h2    # только H2 тесты
```
