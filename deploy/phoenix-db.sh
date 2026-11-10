#!/bin/sh
# Also run explicitly inside db for existing volumes or password changes.
(
set -eu
if [ -z "${PHOENIX_PASSWORD:-}" ]; then
  echo "Phoenix database setup skipped: PHOENIX_PASSWORD is empty." >&2
  exit 0
fi
export PGUSER="${POSTGRES_USER:-app}" PGDATABASE="${POSTGRES_DB:-arno}"
if psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='phoenix'" | grep -q 1; then
  psql -v ON_ERROR_STOP=1 -v pwd="$PHOENIX_PASSWORD" <<'SQL'
ALTER ROLE phoenix PASSWORD :'pwd';
SQL
else
  psql -v ON_ERROR_STOP=1 -v pwd="$PHOENIX_PASSWORD" <<'SQL'
CREATE ROLE phoenix LOGIN PASSWORD :'pwd';
SQL
fi
if ! psql -tAc "SELECT 1 FROM pg_database WHERE datname='phoenix'" | grep -q 1; then
  psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE phoenix OWNER phoenix"
  psql -v ON_ERROR_STOP=1 -c "REVOKE CONNECT ON DATABASE phoenix FROM PUBLIC"
fi

)
