# Эксплуатация

## Установка и конфигурация

На Linux нужны Python 3.12, OpenSSL, PostgreSQL 16; для контейнерной поставки
Docker Engine и Compose v2. `scripts/install.py` устанавливает Python-зависимости,
проверяет их совместимость и пишет журнал в `GMP_DATA_DIR/logs` (по умолчанию
`artifacts/h3_workspace/logs`). Системные пакеты устанавливаются администратором.
Для Ubuntu/Debian также нужны `python3.12-venv`, `fonts-dejavu-core`, `libgomp1`,
`libexpat1`, `ca-certificates`; шрифт нужен для PDF с кириллицей. В Dockerfile
эти системные зависимости устанавливаются автоматически.

```bash
python3.12 scripts/install.py
export PYTHONPATH="$PWD/src:$PWD/h1/h1_coverage/src:$PWD/h2/src:$PWD/h2"
.venv/bin/python scripts/doctor.py
.venv/bin/python scripts/manage_users.py --file /secure/geoscan/users.json --username admin --role admin
```

Добавьте `viewer` и `planner` той же командой, задавая разные пароли.
Для PostgreSQL по Unix socket создайте БД с владельцем, совпадающим с системным
пользователем службы. Для удалённой БД используйте отдельного несуперпользователя,
TLS и `sslmode=verify-full`. Не передавайте пароль в аргументах команд.

Переменные: `GMP_DATABASE_URL_FILE`, `GMP_AUTH_FILE`, `GMP_DATA_DIR`,
`GMP_DEPLOYMENT_MODE=internal`, `GMP_OFFLINE=1`. Затем:

```bash
.venv/bin/python -m uvicorn gmp.api.app:app --host 127.0.0.1 --port 8090 --workers 1
```

Внешний доступ идёт через nginx:443 с доверенным сертификатом.
`GMP_ALLOW_LOCAL_HTTP=1` разрешает не-Secure cookie только для localhost/loopback
и предназначен для локального испытания; в корпоративном развёртывании он не нужен.

## Контейнерная поставка

```bash
python3 deploy/prepare_h3_bundle.py build/review-release
python3 deploy/bootstrap.py /secure/geoscan-demo
docker compose --env-file /secure/geoscan-demo/compose.env -f build/review-release/compose.yaml up --build -d
```

Bootstrap генерирует локальные сертификаты для `https://localhost`; для сервера
замените origin и TLS-пути на корпоративные сертификаты. Доступы записаны в
приватный `credentials.txt`, не выводятся в журнал и не входят в Git.
Compose включает PostgreSQL с SCRAM/TLS, отдельного пользователя приложения,
закрытую backend-сеть и непривилегированный процесс приложения.
Секретные файлы внутри приватного каталога доступны контейнерам read-only.

Для проверки рядом с действующим сервисом используйте отдельный проект Compose
и свободный локальный порт: `GMP_HTTPS_BIND=127.0.0.1:9443`,
`GMP_PUBLIC_ORIGIN=https://localhost:9443`, `docker compose -p geoscan-review ...`.
Проверка: `python3 scripts/smoke_compose.py /secure/geoscan-demo --url https://localhost:9443`.
Не используйте рабочие секреты или тома для тестового проекта.
Сохраните `plan_id` из отчёта. После перезапуска тестового приложения повторите
команду с `--existing-plan <plan_id>`: она проверит прежний план и экспорт
сертификата, затем выполнит новый расчёт.

## Миграция SQLite → PostgreSQL

1. Закройте приём заданий, дождитесь завершения или отмените активные задания.
2. Остановите службу и сделайте приватную копию всего каталога состояния.
3. Подготовьте пустую PostgreSQL БД и новый каталог состояния.
4. Выполните сначала dry run, затем перенос:

```bash
.venv/bin/python scripts/migrate_postgres.py --source /var/lib/geoscan-h3 --destination /var/lib/geoscan-pg
.venv/bin/python scripts/migrate_postgres.py --source /var/lib/geoscan-h3 --destination /var/lib/geoscan-pg --execute
```

Для скрипта задайте `GMP_DATABASE_URL` через приватное окружение. Проверяются
число записей и SHA-256 скопированных файлов. Входы и результаты сохраняются,
сессии отзываются, старые диагностические логи остаются в резервной копии.
После проверки поменяйте `GMP_DATA_DIR` и подключение к БД, затем запустите службу.
Исходное SQLite-хранилище не изменяется, поэтому доступен откат.

## Логи и восстановление

Приложение хранит JSONL-аудит и диагностические файлы расчётов в одном каталоге
`logs/`. Старые файлы удаляются первыми. Максимум задаётся `GMP_LOG_LIMIT_BYTES`
и не может превышать 500000000 байт. В Compose выделено 450 МБ плюс по два
журнала по 5 МиБ для каждого из трёх контейнеров: суммарно менее 500 МБ.
Результаты миссий и БД не считаются логами и не удаляются этой ротацией.

При резервном копировании остановите приём и службу: сохраните `pg_dump`
и каталог входов/результатов в одной точке времени. Доступ к PostgreSQL
для `pg_dump`/`pg_restore` задавайте через закрытый `PGSERVICEFILE`/`PGPASSFILE`.
Проверяйте восстановление в отдельной БД. Автоматический HA/failover не реализован.

Health: `/health/live`, `/health/ready`. Переполнение очереди: 429 с Retry-After.
Прерванный/ошибочный/непроверенный результат не получает сертификат полёта.

## Воспроизведение испытаний

```bash
export GMP_TEST_DATABASE_URL='postgresql:///geoscan_regression_test'
.venv/bin/python scripts/run_regression.py
.venv/bin/python scripts/evaluate_algorithms.py --junit docs/evidence/system.junit.xml
.venv/bin/python scripts/benchmark_algorithms.py
# Для нагрузки создайте другую пустую БД без таблиц приложения:
export GMP_TEST_DATABASE_URL='postgresql:///geoscan_load_test'
.venv/bin/python scripts/load_test.py --users 1,8,32
```

Браузерные проверки запускаются отдельно от измерения нагрузки, чтобы не
искажать CPU/RAM. Установите Playwright в отдельное тестовое окружение с
зависимостями проекта, выполните `playwright install chromium`, затем
`python web/tests/reviewer_smoke.py`. Скрипт создаёт временный API и пользователей,
проверяет роли, карту и документацию на 1440×1000 и 390×844; после работы
останавливает API. `CHROMIUM_EXECUTABLE` позволяет использовать установленный браузер.

Для сквозной проверки уже запущенной демки с общим кодом используйте
`python web/tests/h3_browser_smoke.py --url https://your-server --env-file /private/demo.env`.
Скрипт запускает LIVE-расчёт, проверяет экспорт и мобильную карту, затем удаляет
только созданные им сцены, включая промежуточные версии. Нужна роль администратора;
для внутреннего профиля с персональными аккаунтами используйте Compose smoke.

CI отдельно проверяет канонический набор с PostgreSQL и поднимает Compose,
проверяя доверенный локальный TLS, запрет записи для viewer и LIVE → SAFE.
Исторический `gmp.planner` и его conformance-набор не относятся к LIVE backend;
его состояние не подменяет эти результаты.
