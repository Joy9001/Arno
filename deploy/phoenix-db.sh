#!/bin/sh
# Also run explicitly inside db for existing volumes or password changes:
#   docker compose exec db sh /docker-entrypoint-initdb.d/20-phoenix-db.sh
(
set -eu
if [ -z "${PHOENIX_PASSWORD:-}" ]; then
  echo "Phoenix database setup skipped: PHOENIX_PASSWORD is empty." >&2
  exit 0
fi
export PHOENIX_PASSWORD
export PGUSER="${PGUSER:-${POSTGRES_USER:-postgres}}" PGDATABASE="${PGDATABASE:-${POSTGRES_DB:-postgres}}"
psql -q -v ON_ERROR_STOP=1 <<'SQL'
\getenv pwd PHOENIX_PASSWORD
SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'phoenix') AS phoenix_exists \gset
\if :phoenix_exists
ALTER ROLE phoenix PASSWORD :'pwd';
\else
CREATE ROLE phoenix LOGIN PASSWORD :'pwd';
\endif
SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'phoenix') AS phoenix_db_exists \gset
\if :phoenix_db_exists
\else
CREATE DATABASE phoenix OWNER phoenix;
REVOKE CONNECT ON DATABASE phoenix FROM PUBLIC;
\endif
SQL
)
