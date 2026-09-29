# H3: протокол проверки поставки

Исторический протокол H3 до объединения модулей. Развёртывание объединённого
MVP от 22.09.2026 описано в `MVP_DEPLOYMENT.md`; новые измеренные результаты
хранятся в `/root/h3/handoff/acceptance/` и не подменяют этот отчёт.

Дата: 21.09.2026, Москва. Адрес: https://45.87.246.124/.
Это проверенный PoC в согласованной расчётной модели, не система допуска
реальных полётов и не доказательство глобального оптимума.

Исторические проверки ниже выполнены до переключения публичного адреса на
просмотрщик H2. На момент подготовки коммита H3-служба остановлена; этот отчёт
не подтверждает доступность H3 по указанному адресу сейчас. Ранние локальные
изменения H1 не входят в H3-коммит; исходники H1 и датасет поставляются отдельно.

## Фактически выполнено

| Проверка | Результат | Свидетельство |
| --- | --- | --- |
| Все исходные пары через независимый runtime gate | 14/14; 11 SAFE, 3 доказанных INFEASIBLE; все исходные SHA-256 сохранены | `evidence/references.json` |
| Реальный HTTPS расчёт всех сцен | 12 сцен, 13 расчётов: 10 SAFE, 3 доказанных INFEASIBLE; 0 ошибок/таймаутов | `evidence/live-all.json` |
| Полный живой прогон | 455.739 с; максимальный отдельный расчёт 110.558 с; solver budget 5 с не равен полному времени H1 + H2 + validator | `evidence/live-all.json` |
| Полное покрытие | Максимальный численный остаток 0.000001079 м², ниже установленного численного допуска | `evidence/live-all.json` |
| Эталоны через HTTPS | Все 13 основных результатов; режим явно fixture, не live | `evidence/service-smoke.json` |
| Отказ и рекомендации | S06: другая база и замена БВС; S07: замена БВС. Только после нового расчёта и независимой проверки | `evidence/live-all.json` |
| Применение рекомендации | Новая версия с SAFE-планом; исходный отказ сохранён | `evidence/service-smoke.json` |
| Защита API | Отказ анонимному пользователю, Secure/HttpOnly/SameSite cookie, CSRF, rate limit, отзыв сессии при смене кода | `tests/api/test_h3_workspace.py` |
| Загрузки | ZIP roundtrip, отдельные GeoTIFF/GeoJSON, KML с типизированными свойствами; ZIP traversal отклонён | API-тесты и `evidence/terrain-smoke.json` |
| Граница GeoTIFF | Переименованный VRT отклонён до вызова GDAL; повреждённый TIFF и другой драйвер возвращают 422 | `tests/api/test_h3_workspace.py` |
| Пользовательское имя KML | Выбор слоя в UI, новая версия и неизменная геометрия; одиночный GeoTIFF нормализуется в dem.tif | `evidence/upload-browser/report.json` |
| DSM | Реальная загрузка Copernicus, источник + SHA-256, повторное использование кеша, GeoTIFF без пропущенных пикселей | `evidence/terrain-smoke.json` |
| Экспорт | GeoJSON, KML, точный JSON результата, certificate, PDF, DOCX; маршрутные экспорты запрещены для отказа | `evidence/service-smoke.json` |
| Браузер | Редактор, версии, LIVE/fixture, движение по маршруту, шесть экспортов; ошибок JS нет | `evidence/frontend-browser/report.json` |
| Финальная версия интерфейса | Сохранённый LIVE SAFE МГУ, desktop/mobile: карта и результат видны, нет null и переполнения | `evidence/final-browser/report.json` |
| Мобильная карта | 1440/390/360 px; canvas непустой, тысячи разных цветов; нет горизонтального переполнения | `evidence/root-map-probe/report.json` |
| Сохранность | Экспорт завершённого расчёта побайтно одинаков до и после restart службы | `evidence/restart.json` |
| Параллельное чтение | 10 одновременных запросов: все HTTP 200, суммарно 0.126 с | `evidence/concurrent-reads.json` |
| Docker | Образ построен, Compose поднят на локальном 4443, доверенный HTTPS, live S00, PDF и сохранность тома после restart | `evidence/docker-smoke.json` |
| Контракт H1/H2 | 7 bundle соответствуют gmp.h1_h2.v1 | `scripts/validate_h1_h2_contract.py` |
| Парсинг старых customer fixtures | 12 parser-тестов прошли | `tests/conformance/test_customer_conformance.py -k parser` |
| HTTPS | Доверенный сертификат IP; `certbot renew --dry-run` успешен, таймер продления включён дважды в день | systemd и `/var/log/letsencrypt/letsencrypt.log` |

Пути `evidence/` относятся к корню переносимого пакета. В рабочем репозитории
соответствующие файлы лежат в `artifacts/h3/`. Полные ответы не содержат
паролей, cookies или клиентских токенов. Браузерные снимки сделаны после входа.

Финальный сфокусированный прогон после исправлений KML и проверки GeoTIFF:
`pytest tests/safety tests/report tests/api -q --disable-warnings`:
**78 passed**, 50.26 с. Машиночитаемый протокол:
`evidence/unit-api-tests.xml`. Предупреждения зависимостей о будущих изменениях
API не являются падениями тестов. Версии 63 установленных пакетов и Python
зафиксированы в `evidence/runtime.json`.

Те же тесты повторены из отдельного переносимого каталога в `/tmp`, с H1 и
датасетом из поставки, без импорта исходников рабочего проекта: **78 passed**,
47.92 с (`evidence/portable-tests.xml`). У исходного датасета повторно сверены
все 255 файлов манифеста: расхождений SHA-256 нет.

## Проверка H3-коммита отдельно от раннего H1

Перед объединением в main файлы git index выгружены в отдельный каталог
`/tmp/geoscan-h3-index-check`. Незакоммиченные изменения H1 в эту копию не
попали. Каноническая библиотека `h1_coverage` и исходный датасет подключены
как внешние зависимости; `gmp` импортируется только из проверяемой копии.

- `pytest tests/api tests/report tests/safety -q`: **83 passed**, 61.43 с.
  Включает LIVE-расчёт, проверенную рекомендацию и преобразование задач без
  приватной функции из раннего bridge H1.
- Unit/property/parser, без solver-conformance: **23 passed**, 2.95 с.
- Проверка существующих семи bundle: `gmp.h1_h2.v1` соблюдён.
- Браузерный прогон локальных снимков из чистой копии прошёл после исправления
  инициализации карты: три локации, desktop/mobile, переключение подложки,
  ошибки JPG/manifest, отсутствие переполнения и ошибок JS. Все 394 запроса
  OSM намеренно заблокированы локально. Протокол рабочего проекта:
  `artifacts/h3/imagery-clean-index/report.json`.
- Отдельный старый solver-conformance S00 в этой копии: **1 failed**,
  ожидался SAFE, получен UNSAFE; старые метрики показывают coverage 100%,
  независимая проверка сертификат не выдала. Полный старый прогон остановлен
  общим лимитом 180 с; его успешное завершение не заявляется.

Преобразование H1-задач выполняется в `gmp.api.planner_adapter`, не меняет
их геометрию, параметры или назначение. Код генерации H1 не переносился и
не изменялся. Это не утверждение о прохождении полного старого conformance.

## Служба

- `geoscan-h3.service`: systemd, непривилегированный пользователь, loopback 8090.
- nginx публикует HTTPS 443; новый HTTP-порт наружу не открыт.
- Данные: `/var/lib/geoscan-h3`; исходники процесса: `/opt/geoscan-h3`.
- Общий код находится только в приватном файле окружения 0600 вне исходников.
- Один вычислительный worker, очередь до 20 заданий, память службы до 3 ГБ.
- Процесс планирования и независимая проверка ограничены по времени.
- Пользовательские проекты: 30 дней; неизменный исходный датасет не удаляется.
- Автозапуск включён, реальный restart выполнен. Перезагрузка всей ОС ради
  теста не проводилась: на сервере работают другие приложения.
- Старый `gmp.service` на 8080 не перезапускался при этой установке.
- Тестовый Docker daemon и локальный Compose после проверки остановлены.

## Известные Ограничения

1. Полосы сенсора непрерывны. Перекрытие отдельных кадров, закрытие обзора
   рельефом вне траектории, полноценная динамика и актуальные разрешения не
   сертифицируются. Полный перечень находится в `H3_SAFETY_SCOPE.md`.
2. `makespan_s` заканчивается последней посадкой. Отдельные финальная
   выгрузка данных и подготовка перед первым взлётом не моделируются.
3. Заявленные операционные параметры проверяются как входные допущения.
   Нельзя считать это подтверждением всех ограничений производителя или
   состояния конкретного аппарата. DSM не заменяет актуальный реестр препятствий.
4. Короткий общий код подходит только контролируемому PoC. Нет индивидуальных
   ролей, производственного резервного копирования и изоляции пользователей.
5. Автоматические рекомендации ограничены тремя кандидатами на запрос;
   они не являются полным перебором всех возможных изменений постановки.
6. Старый прямой customer-conformance S00, обходящий новый H3 API adapter,
   остаётся красным: ожидался SAFE, получен UNSAFE с coverage 0%. Усиленная
   проверка не скрывает недостаточные/несогласованные данные старого пути.
   Полный старый suite зелёным не объявляется. Новый публичный H3-путь прошёл
   все 12 согласованных сцен в LIVE; код генерации H1 и solver H2 не менялся.

## Соответствие H3

Независимый validator и связанный с результатом certificate реализованы;
частичное покрытие не маскируется успехом; рекомендации перепланируются и
проверяются. API, карта, загрузки, версии, очередь, история, экспорт и служба
работают. Docker-поставка, README, сценарий показа и исходники приложены.
Инструкция ориентирована на подготовленный Linux/Docker-хост; измеренная
сборка образа с доступом к реестрам укладывалась в 15 минут. Установка самой
ОС/Docker и оформление прав на реальные полёты в это время не входят.

Для демонстрации: `DEMO_PATH.md`, `docs/H3_DEMO_SCRIPT.md`.

---

# MVP Integration Verification, 2026-09-22

This report concerns the integrated canonical H1 -> standalone H2 -> independent
H3 pipeline, not the historical `gmp.planner` path or the old H2 fixture viewer.

## Before Public Cutover

| Check | Measured result |
| --- | --- |
| Canonical H1 tests | 106 passed in three bounded runs |
| Standalone H2 tests | 115 passed, 2 skipped |
| H3 safety, reports, API | 117 passed |
| Additional parser/property tests | 13 passed; legacy solver tests excluded |
| H1/H2 bundle contract | 8 bundles passed |
| Immutable reference pairs | 14/14 passed: 11 SAFE, 3 INFEASIBLE |
| Dataset integrity | 255 files unchanged |
| Integrated LIVE staging, budget 40 seconds | 13/13 passed: 10 SAFE, 3 proven INFEASIBLE |
| Real local imagery browser checks | 3 locations, desktop/mobile, failure fallback; no JS errors |

Evidence is in `/root/h3/handoff/acceptance/`, including
`unit-api-tests.xml`, `references.json`, `staging-final-live.json`, and
`imagery/report.json`. The first low-budget run is retained separately as
`staging-live.json`: at 5 seconds S04 ran out of search time and remained
uncertified. The final 40-second test did not alter the inputs or weaken the
validator. The UI normally uses a 40-second optimization budget; this is not a
promise about complete request wall-clock time.

## Public HTTPS Deployment

Release `20260922-mvp-3` is served on `https://45.87.246.124:443` by nginx and
`geoscan-mvp.service`. Its 110 backend Python files are identical to the fully
accepted staging snapshot. Port 8443 redirects to 443. The old H3 service is
stopped and disabled; the H2 viewer remains on loopback 8090 only.

- All 13 actual LIVE plans were fetched again and verified over trusted HTTPS:
  10 SAFE, 3 proven INFEASIBLE; input and certificate hashes match.
- The initial public polling run had one HTTP read timeout during server load.
  That plan completed SAFE. `public-live.json` preserves the initial failure;
  `public-confirmed.json` records a read-only recheck of the SAME plan IDs,
  not replacement calculations. This is not a claim of zero transport failures.
- The separate service smoke passed 110 checks, including all 13 primary
  fixtures, S00 LIVE, S06 refusal and certified recommendation application,
  authentication, CSRF, ZIP ingestion, versioning, and six export formats.
- The final browser run passed LIVE H2 provenance, editor/version creation,
  moving playback, desktop/390/360-pixel views, nonblank map pixels, and exports.
  No JavaScript errors or horizontal overflow. The earlier browser wait timeout
  under concurrent large-job load is retained separately, not hidden.
- Custom-name KML upload, geometry preservation, and GeoTIFF filename mapping
  passed a separate browser test.
- All original 19 scenes and 16 plan records survived cutover unchanged.
- An actual restart of `geoscan-mvp.service` preserved the completed mission
  export byte-for-byte. Autostart and the existing TLS renewal timer are enabled.

Final evidence: `public-confirmed.json`, `service.json`,
`browser-final/report.json`, `upload-browser/report.json`,
`history-preserved.json`, and `restart.json` in the evidence directory above.

## Safety and Scope

- No immutable scenario, fleet, area, or reference file was regenerated.
- H2 preserves H1 task geometry and eligibility. Actual DEM and temporal
  constraints are passed into the standalone planner.
- H3 alone issues a certificate bound to the exact input/result hashes.
- A proven necessary-condition refusal can bypass planning; provenance
  explicitly records that H1/H2 were not executed.
- An incomplete search result remains uncertified. Timeout does not prove
  impossibility. A small time budget can fail to find an existing solution.
- Global optimality and real-world flight authorization are not claimed.
  See `H3_SAFETY_SCOPE.md` for sensor, dynamics, and terrain-model limitations.
- The old direct `gmp.planner` customer-conformance suite is not the new
  runtime path and is not declared passing by this report.
- The updated Docker build context includes H1 and H2, but a new Docker-image
  acceptance is not implied by the systemd deployment checks.

Repository locations, versioned runtime layout, private state preservation, and
rollback are described in `MVP_DEPLOYMENT.md`. Existing uncommitted work was
not automatically committed or pushed.
