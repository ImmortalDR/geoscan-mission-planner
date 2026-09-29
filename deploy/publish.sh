#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
APP=/root/h3
VENV=$APP/.venv
# PostgreSQL role + database
sudo -u postgres psql -v ON_ERROR_STOP=1 <<'SQL' || true
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gmp') THEN
    CREATE ROLE gmp LOGIN PASSWORD 'gmp';
  END IF;
END$$;
SQL
sudo -u postgres psql -v ON_ERROR_STOP=1 -c "SELECT 1 FROM pg_database WHERE datname='gmp'" | grep -q 1 || sudo -u postgres createdb -O gmp gmp
sudo -u postgres psql -d gmp -c "CREATE EXTENSION IF NOT EXISTS postgis;" || true
sudo -u postgres psql -d gmp -f "$APP/deploy/db_schema_core.sql" || true
# nginx
cp "$APP/deploy/nginx-gmp.conf" /etc/nginx/sites-available/gmp
ln -sfn /etc/nginx/sites-available/gmp /etc/nginx/sites-enabled/gmp
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx
systemctl enable nginx
# systemd service
cp "$APP/deploy/gmp.service" /etc/systemd/system/gmp.service
systemctl daemon-reload
systemctl enable gmp
systemctl restart gmp
sleep 1
systemctl --no-pager --full status gmp | head -20
curl -fsS http://127.0.0.1:8080/health/live
echo
curl -fsS http://127.0.0.1/health/live
echo
echo "GMP is published on :8080 and :80"
