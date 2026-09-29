#!/bin/sh
set -eu
install -d -o postgres -g postgres -m 0700 /var/lib/postgresql/tls
install -o postgres -g postgres -m 0600 /run/secrets/db_key /var/lib/postgresql/tls/server.key
install -o postgres -g postgres -m 0644 /run/secrets/db_ca /var/lib/postgresql/tls/server.crt
exec /usr/local/bin/docker-entrypoint.sh "$@"
