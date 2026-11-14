#!/bin/sh
# Idempotently create or update the non-superuser `app` role and its database.
# Runs during container initdb (as 10-app-role.sh) and in CI over TCP.
(
set -eu
: "${APP_PASSWORD:?APP_PASSWORD is required}"
APP_DB="${APP_DB:-arno}"
export APP_PASSWORD APP_DB
export PGUSER="${PGUSER:-${POSTGRES_USER:-postgres}}"
export PGDATABASE="${PGDATABASE:-postgres}"

psql -q -v ON_ERROR_STOP=1 <<'SQL'
\getenv app_pwd APP_PASSWORD
\getenv app_db APP_DB
SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app') AS app_exists \gset
\if :app_exists
ALTER ROLE app WITH LOGIN CREATEDB CREATEROLE NOSUPERUSER NOBYPASSRLS NOREPLICATION PASSWORD :'app_pwd';
\else
CREATE ROLE app WITH LOGIN CREATEDB CREATEROLE NOSUPERUSER NOBYPASSRLS NOREPLICATION PASSWORD :'app_pwd';
\endif
GRANT pg_signal_backend TO app;
SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'app_db') AS db_exists \gset
\if :db_exists
\else
CREATE DATABASE :"app_db" OWNER app;
\endif
SQL

# During container initdb (local socket, no PGHOST), clear the bootstrap superuser's password so
# `postgres` cannot log in over TCP. CI connects over TCP (PGHOST is set) and skips this step.
if [ -z "${PGHOST:-}" ]; then
  psql -q -v ON_ERROR_STOP=1 -c "ALTER ROLE postgres PASSWORD NULL"
fi
)
