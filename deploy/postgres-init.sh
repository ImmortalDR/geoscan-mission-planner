#!/bin/sh
set -eu
export APP_DB_PASSWORD="$(cat /run/secrets/db_password)"
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 <<'SQL'
\getenv app_password APP_DB_PASSWORD
CREATE ROLE geoscan LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_password';
ALTER DATABASE geoscan OWNER TO geoscan;
GRANT ALL ON SCHEMA public TO geoscan;
SQL
unset APP_DB_PASSWORD
