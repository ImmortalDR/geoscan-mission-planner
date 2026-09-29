# Coverage engine (DIY)

# H1 implementation status

**Бэклог:** [`docs/backlog.md`](docs/backlog.md) — **P0/P1/P2 закрыт** (остались только wont → H2 или F2C/geopandas).

| | |
|--|--|
| Пакет | `h1_coverage` M1–M13 |
| Coverage | **DIY lawnmower** production; F2C bridge unwired (honest `backend_status`) |
| DIY «до ума»? | [`docs/coverage.md`](docs/coverage.md) — можно, дорого, для хакатона обычно не надо |
| Hints | `extensions.h1.*` — energy, site_recommendation, reserve, terrain |
| Golden | `h1_coverage/fixtures/golden/S00_first_transect.json` |
| Live | includes `S01_full_customer_acceptance_100km2.bundle.json` |

## P2 (этот проход)

| ID | Статус |
|----|--------|
| S-04 | done — multi-sortie `energy_hints` |
| S-06 | done — candidate site recommend |
| S-07 | done — reserve_sites + gap warn |
| A-06 | done — `extensions.h1.terrain` |
| A-07 | wont — DIY KML хватает |
| Q-03 | done — golden S00 transect |
| Q-04 | done — S00&lt;25s, S01&lt;180s |

Дальше по продукту: **H2**, не добивка H1.

---

# H1: coverage and runtime corrections, 2026-09-22

The public H1/H2 schema is unchanged. No source scenes, canonical bundle
fixtures, or immutable H3 dataset files were regenerated.

## Corrections

- Open-tour 2-opt now compares actual changed edges. The old tail case could
  accept a longer tour and cycle; repeated centroids have a regression test.
  Centroid coordinates are cached during ordering.
- Coverage completeness is checked before choosing the lowest-cost candidate.
  All generated angles remain available for this check; `keep_candidates`
  limits the report, not the feasible search space. The selected candidate is
  always included in that report.
- Coverage uses the complete effective survey area as its denominator, even
  when an optional headland inset is used to generate flight lines.
- A decomposition cell narrower than one swath receives a center pass instead
  of two lines outside the cell that would both be discarded.

## Verification

The non-S01 test selection passed: 103 tests in 36.22 seconds. The strengthened
S01 stress test passed in 32.49 seconds with strict coverage, `headland_m=0`,
and at least 99.9 percent coverage for each of the five survey jobs: 99.998
percent aggregate, 99.990 percent GEO, and 100 percent for the other four jobs.
The other two S01 checks passed in 51.69 seconds. In total, all 106 tests passed
across these separately bounded runs. Test
exports now use pytest's temporary directory, not repository fixtures.
All 13 module contracts remain wired.

Reproduce with commands individually bounded to 120 seconds:

```bash
cd /root/h1
timeout 120s .venv/bin/python -m pytest h1_coverage/tests -q -k 'not s01'
timeout 120s .venv/bin/python -m pytest h1_coverage/tests/test_max_backlog.py::test_s01_100km2_stress -q
timeout 120s .venv/bin/python -m pytest h1_coverage/tests/test_p2_backlog.py::test_q04_perf_s01_under_180s h1_coverage/tests/test_m01_file_cases.py::test_conformance_s01_obstacles_temporal -q
.venv/bin/python scripts/validate_h1_module_contracts.py
```

## Remaining Scope

Coverage remains the DIY backend, not Fields2Cover. The optional 8-metre
headland used in the older performance test still leaves GEO coverage below
99.9 percent, even though the aggregate exceeds that threshold. The live
integration uses `headland_m=0`; the independent H3 gate must still check every
survey job. Coverage success does not establish energy feasibility, a complete
schedule, collision freedom, or flight clearance.

---


Короткая пометка для людей (не агентный чеклист).

## Как есть сейчас

Production path H1 — **своя змейка** (`diy_lawnmower_v1`), не Fields2Cover.  
Этого **хватает**, чтобы отдать валидный `H1H2Bundle` в H2 и показать демо по шву.

Утверждение «DIY ничем не хуже F2C» — **неверно**. DIY закрывает роль «нарисовать полосы». F2C в плане брали как готовый станок: headlands, разные раскладки, развороты, отлаженные дыры.

## Можно ли допилить DIY до ума без F2C?

**Да.** Альтернатива мосту на F2C — довести свою геометрию:

1. **Headlands** — нормальный отступ и обход края поля, не «inset по желанию».
2. **BCD / нарезка** — устойчивая работа на вогнутостях и дырах, меньше обрывков и лишних перелётов.
3. **Dubins / Reeds–Shepp** — развороты fixed-wing как кинематика, не полукруг «на глаз».
4. Жёсткие тесты на гадких полигонах (не только красивый S00).

Это и есть тот мини-проект, который F2C должен был **снять** с команды.

## Чего это будет стоить

Ориентир порядка величины (один сильный разработчик / плотный агентный цикл):

| Объём | Что получите | Срок (грубо) |
|-------|----------------|--------------|
| Косметика | Чуть лучше углы/клип, те же дыры в сложных местах | дни |
| «До ума» DIY | Headlands + вменяемый BCD + нормальные FW-развороты + регрессии | **недели** (часто ~2–4) |
| Паритет с зрелым F2C на заказских сценах | Краевые случаи, вогнутости, большие дыры, стресс 100 км² | ближе к **месяцу** геометрии |

Плюс риск: чем дальше пилите сами, тем больше своего «зоопарка» багов вместо чужой отлаженной библиотеки.

Другой путь — **мост Shapely ↔ Fields2Cover**: меньше своего матча, больше боли со сборкой натива и типами. По смыслу цель та же: покрытие перестаёт быть «змейкой для шва».

## Надо ли это для уровня хакатона?

**Обычно нет.**

Для хакатона / питча достаточно:

- честно сказать: coverage = DIY, шов с H2 работает;
- зелёные smoke-сцены и валидный bundle;
- не обещать «как Fields2Cover» и не маскировать backend.

Допилка DIY «до ума» нужна, если:

- жюри/заказчик копает качество галсов и FW-разворотов;
- сложные дыры/вогнутости портят демо визуально или ломают feasible;
- после хакатона идёте в продукт и хотите соответствовать заявленной архитектуре coverage.

**Правило:** сначала честность формулировок, потом геометрия. Не путать «H1 готов отдать задачи H2» с «coverage-engine на уровне F2C».

См. также: [`../docs/architecture.md`](../docs/architecture.md) §14, [`docs/coverage.md`](docs/coverage.md), корневой [`../../docs/STATUS.md`](../../docs/STATUS.md).
