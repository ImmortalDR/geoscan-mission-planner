#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  echo 'Сначала выполните: python3.12 scripts/install.py' >&2
  exit 1
fi
export PYTHONPATH="$PWD/src:$PWD/h1/h1_coverage/src:$PWD/h2/src:$PWD/h2"
export GMP_DATA_DIR="${GMP_DATA_DIR:-$PWD/artifacts/h3_workspace}"
export GMP_ALLOW_LOCAL_HTTP=1
export GMP_PUBLIC_ACCESS=0
port="${GMP_PORT:-8090}"
if [[ -z "${GMP_ACCESS_CODE:-}" ]]; then
  GMP_ACCESS_CODE="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(24))')"
  export GMP_ACCESS_CODE
fi
printf 'Откройте http://127.0.0.1:%s/\nКод входа: %s\nОстановка: Ctrl+C\n' "$port" "$GMP_ACCESS_CODE"
exec .venv/bin/python -m uvicorn gmp.api.app:app --host 127.0.0.1 --port "$port"
