#!/usr/bin/env bash
# One-time in-place conversion to demote `app` from Postgres bootstrap superuser to a normal
# database owner role. Run with api, backup, and phoenix stopped:
#   docker compose exec -T db bash -s -- arno < deploy/demote-app.sh
set -euo pipefail
umask 077

# Always use the container's local Unix socket; never inherit TCP host/password settings.
unset PGHOST PGHOSTADDR PGPORT PGPASSWORD PGPASSFILE PGSERVICE

leave_others=0
dbs=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --leave-others)
      leave_others=1
      shift
      ;;
    --)
      shift
      while [ "$#" -gt 0 ]; do
        dbs+=("$1")
        shift
      done
      ;;
    -*)
      echo "Unknown option: $1" >&2
      echo "Usage: demote-app.sh [--leave-others] <db> [<db> ...]" >&2
      exit 2
      ;;
    *)
      dbs+=("$1")
      shift
      ;;
  esac
done

if [ "${#dbs[@]}" -eq 0 ]; then
  echo "Usage: demote-app.sh [--leave-others] <db> [<db> ...]" >&2
  exit 2
fi

seen_dbs=()
for db in "${dbs[@]}"; do
  if [[ ! "$db" =~ ^[a-zA-Z_][a-zA-Z0-9_]{0,62}$ ]]; then
    echo "Invalid database name: $db" >&2
    exit 2
  fi
  case "$db" in
    postgres|template0|template1|*__converted|*__pre_demote)
      echo "Refusing reserved or internal database name: $db" >&2
      exit 2
      ;;
  esac
  for prev in "${seen_dbs[@]+"${seen_dbs[@]}"}"; do
    if [ "$prev" = "$db" ]; then
      echo "Duplicate database name: $db" >&2
      exit 2
    fi
  done
  seen_dbs+=("$db")
done

sql_in_list() {
  local out="" item
  for item in "$@"; do
    if [ -n "$out" ]; then
      out="$out, "
    fi
    out="$out'$item'"
  done
  printf '%s' "$out"
}

# Connect as the bootstrap superuser (currently either `postgres` or `app`).
super_user=""
if psql -U postgres -d postgres -Atc "SELECT 1" >/dev/null 2>&1; then
  boot_check="$(psql -U postgres -d postgres -Atc "SELECT rolname || ':' || rolsuper FROM pg_roles WHERE oid = 10")"
  if [ "$boot_check" = "postgres:true" ]; then
    super_user="postgres"
  fi
fi
if [ -z "$super_user" ] && psql -U app -d postgres -Atc "SELECT 1" >/dev/null 2>&1; then
  boot_check="$(psql -U app -d postgres -Atc "SELECT rolname || ':' || rolsuper FROM pg_roles WHERE oid = 10")"
  if [ "$boot_check" = "app:true" ]; then
    super_user="app"
  fi
fi
if [ -z "$super_user" ]; then
  echo "Refusing: could not connect as the bootstrap superuser (app or postgres) over the local socket." >&2
  exit 1
fi

psql_super() {
  psql -U "$super_user" -X -q -v ON_ERROR_STOP=1 "$@"
}

boot_role="$(psql_super -d postgres -Atc "SELECT rolname FROM pg_roles WHERE oid = 10")"
app_super="$(psql_super -d postgres -Atc "SELECT rolsuper FROM pg_roles WHERE rolname = 'app'")"
has_convert_role="$(psql_super -d postgres -Atc "SELECT 1 FROM pg_roles WHERE rolname = 'arno_convert'")"
leftover_converted="$(psql_super -d postgres -Atc "SELECT datname FROM pg_database WHERE datname LIKE '%\\_\\_converted' ESCAPE '\\' ORDER BY datname")"

all_named_owned_by_app=1
for db in "${dbs[@]}"; do
  db_owner="$(psql_super -d postgres -Atc "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '$db'")"
  if [ "$db_owner" != "app" ]; then
    all_named_owned_by_app=0
    break
  fi
done

if [ "$boot_role" = "postgres" ] && [ "$app_super" = "f" ] && [ -z "$has_convert_role" ] && [ -z "$leftover_converted" ] && [ "$all_named_owned_by_app" -eq 1 ]; then
  echo "already converted"
  exit 0
fi

if [ "$boot_role" != "app" ] && [ "$boot_role" != "postgres" ]; then
  echo "Refusing: unexpected bootstrap role '$boot_role' at OID 10." >&2
  exit 1
fi

if [ "$boot_role" = "postgres" ] && [ "$app_super" = "t" ]; then
  echo "Refusing: bootstrap role is 'postgres' but 'app' is still superuser." >&2
  exit 1
fi

# Guard 1: refuse if any other client session is connected.
other_sessions="$(psql_super -d postgres -Atc "
SELECT pid || ' user=' || coalesce(usename, '') || ' db=' || coalesce(datname, '') || ' app=' || coalesce(application_name, '')
FROM pg_stat_activity
WHERE pid <> pg_backend_pid()
  AND backend_type = 'client backend'
ORDER BY pid
")"
if [ -n "$other_sessions" ]; then
  echo "Refusing: other database sessions are connected. Stop api, backup, and phoenix first:" >&2
  printf '%s\n' "$other_sessions" >&2
  exit 1
fi

# Guard 2: refuse if a named database does not exist (unless resuming mid-swap), or if an
# unconverted database already has a __pre_demote backup database.
exclude_names=()
for db in "${dbs[@]}"; do
  exclude_names+=("$db" "${db}__converted" "${db}__pre_demote")
  db_exists="$(psql_super -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '$db'")"
  pre_exists="$(psql_super -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '${db}__pre_demote'")"
  conv_exists="$(psql_super -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '${db}__converted'")"
  if [ -z "$db_exists" ]; then
    if [ -n "$pre_exists" ] && [ -n "$conv_exists" ]; then
      continue
    fi
    echo "Refusing: database '$db' does not exist." >&2
    exit 1
  fi
  db_owner="$(psql_super -d postgres -Atc "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '$db'")"
  if [ "$db_owner" != "app" ] && [ -n "$pre_exists" ]; then
    echo "Refusing: '${db}__pre_demote' already exists while '$db' is still owned by '$db_owner'." >&2
    exit 1
  fi
done

# Guard 3: refuse if other non-template databases are owned by the bootstrap role unless named or --leave-others.
exclude_sql="$(sql_in_list "${exclude_names[@]}")"
other_boot_dbs="$(psql_super -d postgres -Atc "
SELECT datname
FROM pg_database
WHERE NOT datistemplate
  AND datname <> 'postgres'
  AND datdba = 10
  AND datname NOT IN ($exclude_sql)
ORDER BY datname
")"
if [ -n "$other_boot_dbs" ]; then
  if [ "$leave_others" -eq 0 ]; then
    echo "Refusing: other non-template database(s) are owned by the bootstrap role and were not named:" >&2
    printf '  %s\n' $other_boot_dbs >&2
    echo "Name them on the command line to convert them, or pass --leave-others to leave them owned by postgres." >&2
    exit 1
  fi
  echo "Leaving other database(s) owned by postgres: $(echo "$other_boot_dbs" | tr '\n' ' ' | sed 's/ *$//')"
fi

# Step 2 & 3: rename OID 10 from `app` to `postgres`, recreate `app` as normal role with the same
# SCRAM-SHA-256 verifier, clear `postgres` password, and grant memberships.
if [ "$boot_role" = "app" ]; then
  is_scram="$(psql_super -d postgres -Atc "SELECT coalesce(rolpassword LIKE 'SCRAM-SHA-256\$%', false) FROM pg_authid WHERE oid = 10")"
  if [ "$is_scram" != "t" ]; then
    echo "Refusing: bootstrap role 'app' does not have a SCRAM-SHA-256 password verifier." >&2
    exit 1
  fi

  psql_super -d postgres <<'SQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'arno_convert') THEN
        CREATE ROLE arno_convert SUPERUSER LOGIN;
    END IF;
END
$$;
SQL

  psql -U arno_convert -d postgres -X -q -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
ALTER ROLE app RENAME TO postgres;
DO $$
DECLARE
    verifier text;
BEGIN
    SELECT rolpassword INTO verifier FROM pg_authid WHERE oid = 10;
    IF verifier IS NULL OR verifier NOT LIKE 'SCRAM-SHA-256$%' THEN
        RAISE EXCEPTION 'bootstrap role password is not SCRAM-SHA-256';
    END IF;
    EXECUTE format(
        'CREATE ROLE app WITH LOGIN CREATEDB CREATEROLE NOSUPERUSER NOBYPASSRLS NOREPLICATION PASSWORD %L',
        verifier
    );
END
$$;
ALTER ROLE postgres PASSWORD NULL;
COMMIT;
SQL

  super_user="postgres"
  psql_super -d postgres <<'SQL'
GRANT pg_signal_backend TO app;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_ro') THEN
        EXECUTE 'GRANT agent_ro TO app WITH ADMIN TRUE, INHERIT FALSE, SET FALSE';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backup') THEN
        EXECUTE 'GRANT backup TO app WITH ADMIN TRUE, INHERIT FALSE, SET FALSE';
    END IF;
END
$$;
SQL

  echo "Converted role 'app' to non-superuser and renamed bootstrap role to 'postgres'."
else
  # Ensure expected attributes and memberships on a resumed run.
  psql_super -d postgres <<'SQL'
ALTER ROLE app WITH LOGIN CREATEDB CREATEROLE NOSUPERUSER NOBYPASSRLS NOREPLICATION;
ALTER ROLE postgres PASSWORD NULL;
GRANT pg_signal_backend TO app;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_ro') THEN
        EXECUTE 'GRANT agent_ro TO app WITH ADMIN TRUE, INHERIT FALSE, SET FALSE';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backup') THEN
        EXECUTE 'GRANT backup TO app WITH ADMIN TRUE, INHERIT FALSE, SET FALSE';
    END IF;
END
$$;
SQL
fi

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT

dump_db_state() {
  local target_db="$1"
  local out_dir="$2"

  # 1. Table/view list
  psql_super -d "$target_db" -At <<'SQL' > "$out_dir/tables.txt"
SELECT table_schema || '.' || table_name || '|' || table_type
FROM information_schema.tables
WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY table_schema, table_name, table_type;
SQL

  # 2. Row counts and per-table md5 content hashes
  psql_super -d "$target_db" -At <<'SQL' > "$out_dir/hashes.txt"
SELECT format(
    'SELECT %L AS tbl, count(*) AS rows, md5(coalesce(string_agg(md5(t::text), '''' ORDER BY md5(t::text)), '''')) AS hash FROM %I.%I AS t;',
    table_schema || '.' || table_name,
    table_schema,
    table_name
)
FROM information_schema.tables
WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
  AND table_type = 'BASE TABLE'
ORDER BY table_schema, table_name \gexec
SQL

  # 3. Backup manifest check when fin_agent_backup_manifest() exists
  local has_manifest
  has_manifest="$(psql_super -d "$target_db" -Atc "SELECT to_regproc('public.fin_agent_backup_manifest()') IS NOT NULL")"
  if [ "$has_manifest" = "t" ]; then
    psql_super -d "$target_db" -Atc "SELECT (fin_agent_backup_manifest()->'tables')::text" > "$out_dir/manifest.txt"
  else
    : > "$out_dir/manifest.txt"
  fi

  # 4. schema_version rows when schema_version exists
  local has_sv
  has_sv="$(psql_super -d "$target_db" -Atc "SELECT to_regclass('public.schema_version') IS NOT NULL")"
  if [ "$has_sv" = "t" ]; then
    psql_super -d "$target_db" -Atc "SELECT version || '|' || name || '|' || checksum || '|' || applied_at FROM schema_version ORDER BY version" > "$out_dir/schema_version.txt"
  else
    : > "$out_dir/schema_version.txt"
  fi

  # 5. Table/view/routine/sequence/schema/database privileges for agent_ro and backup (minus grantor)
  psql_super -d "$target_db" -At <<'SQL' > "$out_dir/grants.txt"
SELECT table_schema || '.' || table_name || '|' || grantee || '|' || privilege_type || '|' || is_grantable
FROM information_schema.role_table_grants
WHERE grantee IN ('agent_ro', 'backup')
ORDER BY table_schema, table_name, grantee, privilege_type, is_grantable;

SELECT routine_schema || '.' || routine_name || '|' || grantee || '|' || privilege_type || '|' || is_grantable
FROM information_schema.role_routine_grants
WHERE grantee IN ('agent_ro', 'backup')
ORDER BY routine_schema, routine_name, grantee, privilege_type, is_grantable;

SELECT n.nspname || '.' || c.relname || '|' || pg_get_userbyid(a.grantee) || '|' || a.privilege_type || '|' || a.is_grantable
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL aclexplode(coalesce(c.relacl, '{}'::aclitem[])) a
WHERE n.nspname NOT LIKE 'pg_%'
  AND n.nspname <> 'information_schema'
  AND c.relkind = 'S'
  AND a.grantee <> 0
  AND pg_get_userbyid(a.grantee) IN ('agent_ro', 'backup')
ORDER BY n.nspname, c.relname, 1;

SELECT 'SCHEMA:' || n.nspname || '|' || pg_get_userbyid(a.grantee) || '|' || a.privilege_type || '|' || a.is_grantable
FROM pg_namespace n
CROSS JOIN LATERAL aclexplode(coalesce(n.nspacl, '{}'::aclitem[])) a
WHERE n.nspname NOT LIKE 'pg_%'
  AND n.nspname <> 'information_schema'
  AND a.grantee <> 0
  AND pg_get_userbyid(a.grantee) IN ('agent_ro', 'backup')
ORDER BY n.nspname, 1;
SQL

  # 6. Database-level ACL (normalized so OID 10 maps to `app`)
  psql_super -d postgres -At <<SQL > "$out_dir/datacl.txt"
SELECT
    CASE WHEN a.grantee = 0 THEN 'PUBLIC'
         WHEN a.grantee = 10 THEN 'app'
         ELSE pg_get_userbyid(a.grantee) END || '|' ||
    a.privilege_type || '|' ||
    a.is_grantable
FROM pg_database d
CROSS JOIN LATERAL aclexplode(coalesce(d.datacl, '{}'::aclitem[])) a
WHERE d.datname = '$target_db'
ORDER BY 1;
SQL

  # 7. Default ACLs (normalized so OID 10 maps to `app`)
  psql_super -d "$target_db" -At <<'SQL' > "$out_dir/default_acls.txt"
SELECT
    CASE WHEN d.defaclrole = 10 THEN 'app' ELSE pg_get_userbyid(d.defaclrole) END || '|' ||
    coalesce(n.nspname, '') || '|' ||
    d.defaclobjtype::text || '|' ||
    CASE WHEN a.grantee = 0 THEN 'PUBLIC'
         WHEN a.grantee = 10 THEN 'app'
         ELSE pg_get_userbyid(a.grantee) END || '|' ||
    a.privilege_type || '|' ||
    a.is_grantable
FROM pg_default_acl d
LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
CROSS JOIN LATERAL aclexplode(d.defaclacl) a
ORDER BY 1;
SQL
}

verify_converted() {
  local orig_db="$1"
  local conv_db="$2"

  mkdir -p "$tmpdir/orig" "$tmpdir/conv"
  dump_db_state "$orig_db" "$tmpdir/orig"
  dump_db_state "$conv_db" "$tmpdir/conv"

  local label
  for label in tables hashes manifest schema_version grants datacl default_acls; do
    if ! diff -u "$tmpdir/orig/${label}.txt" "$tmpdir/conv/${label}.txt" >&2; then
      echo "Verification failed for $conv_db vs $orig_db ($label mismatch). Leaving $orig_db untouched." >&2
      return 1
    fi
  done

  local non_app_owners
  non_app_owners="$(psql_super -d "$conv_db" -At <<SQL
SELECT 'database:' || d.datname || ':' || pg_get_userbyid(d.datdba)
FROM pg_database d
WHERE d.datname = '$conv_db' AND pg_get_userbyid(d.datdba) <> 'app'
UNION ALL
SELECT 'class:' || n.nspname || '.' || c.relname || ':' || pg_get_userbyid(c.relowner)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname NOT LIKE 'pg_%'
  AND n.nspname <> 'information_schema'
  AND pg_get_userbyid(c.relowner) <> 'app'
UNION ALL
SELECT 'proc:' || n.nspname || '.' || p.proname || ':' || pg_get_userbyid(p.proowner)
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname NOT LIKE 'pg_%'
  AND n.nspname <> 'information_schema'
  AND pg_get_userbyid(p.proowner) <> 'app'
UNION ALL
SELECT 'type:' || n.nspname || '.' || t.typname || ':' || pg_get_userbyid(t.typowner)
FROM pg_type t
JOIN pg_namespace n ON n.oid = t.typnamespace
WHERE n.nspname NOT LIKE 'pg_%'
  AND n.nspname <> 'information_schema'
  AND t.typtype <> 'b'
  AND pg_get_userbyid(t.typowner) <> 'app'
UNION ALL
SELECT 'schema:' || n.nspname || ':' || pg_get_userbyid(n.nspowner)
FROM pg_namespace n
WHERE n.nspname NOT LIKE 'pg_%'
  AND n.nspname <> 'information_schema'
  AND NOT (n.nspname = 'public' AND pg_get_userbyid(n.nspowner) = 'pg_database_owner')
  AND pg_get_userbyid(n.nspowner) <> 'app';
SQL
)"
  if [ -n "$non_app_owners" ]; then
    echo "Verification failed: objects in $conv_db not owned by app:" >&2
    printf '%s\n' "$non_app_owners" >&2
    return 1
  fi

  rm -rf "$tmpdir/orig" "$tmpdir/conv"
  echo "Verified $conv_db matches $orig_db and all objects are owned by app."
}

pre_demote_dbs=()
for db in "${dbs[@]}"; do
  db_exists="$(psql_super -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '$db'")"
  pre_exists="$(psql_super -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '${db}__pre_demote'")"
  conv_exists="$(psql_super -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '${db}__converted'")"

  # Resume mid-swap case: db missing, both __pre_demote and __converted present.
  if [ -z "$db_exists" ] && [ -n "$pre_exists" ] && [ -n "$conv_exists" ]; then
    verify_converted "${db}__pre_demote" "${db}__converted"
    psql_super -d postgres -c "ALTER DATABASE \"${db}__converted\" RENAME TO \"$db\";"
    pre_demote_dbs+=("${db}__pre_demote")
    echo "Completed swap for $db (kept ${db}__pre_demote)."
    continue
  fi

  db_owner="$(psql_super -d postgres -Atc "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '$db'")"
  if [ "$db_owner" = "app" ] && [ -z "$conv_exists" ]; then
    echo "Database $db is already owned by app; skipping."
    if [ -n "$pre_exists" ]; then
      pre_demote_dbs+=("${db}__pre_demote")
    fi
    continue
  fi

  if [ -n "$conv_exists" ]; then
    psql_super -d postgres -c "DROP DATABASE \"${db}__converted\" WITH (FORCE);"
  fi

  dump_file="$tmpdir/${db}.dump"
  toc_file="$tmpdir/${db}.toc"
  pg_dump -U postgres -Fc --no-owner -f "$dump_file" "$db"

  psql_super -d postgres -c "CREATE DATABASE \"${db}__converted\" OWNER app;"

  # Exclude DEFAULT ACL entries from the dump TOC (since OID 10 was renamed to `postgres`, the
  # dump encodes them `FOR ROLE postgres`, which `app` cannot run) and replay them below for `app`.
  pg_restore -l "$dump_file" | grep -v ' DEFAULT ACL ' > "$toc_file" || true
  if [ -s "$toc_file" ]; then
    pg_restore --no-owner --exit-on-error -L "$toc_file" -U app -d "${db}__converted" "$dump_file"
  fi

  # Reapply database-level ACLs (GRANT CONNECT / REVOKE TEMP, etc.) that single-DB dumps omit.
  has_datacl="$(psql_super -d postgres -Atc "SELECT datacl IS NOT NULL FROM pg_database WHERE datname = '$db'")"
  if [ "$has_datacl" = "t" ]; then
    psql_super -d postgres -c "REVOKE ALL ON DATABASE \"${db}__converted\" FROM PUBLIC;"
    psql_super -d postgres -At <<SQL | psql_super -d postgres
SELECT format(
    'GRANT %s ON DATABASE %I TO %s%s;',
    a.privilege_type,
    '${db}__converted',
    CASE WHEN a.grantee = 0 THEN 'PUBLIC'
         WHEN a.grantee = 10 THEN 'app'
         ELSE quote_ident(pg_get_userbyid(a.grantee)) END,
    CASE WHEN a.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END
)
FROM pg_database d
CROSS JOIN LATERAL aclexplode(d.datacl) a
WHERE d.datname = '$db'
ORDER BY a.grantee, a.privilege_type;
SQL
  fi

  # Reapply default ACLs, mapping the old bootstrap role (OID 10) to `app`.
  psql_super -d "$db" -At <<'SQL' | psql_super -d "${db}__converted"
SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I%s GRANT %s ON %s TO %s%s;',
    CASE WHEN d.defaclrole = 10 THEN 'app' ELSE pg_get_userbyid(d.defaclrole) END,
    CASE WHEN d.defaclnamespace = 0 THEN '' ELSE format(' IN SCHEMA %I', n.nspname) END,
    a.privilege_type,
    CASE d.defaclobjtype
        WHEN 'r' THEN 'TABLES'
        WHEN 'S' THEN 'SEQUENCES'
        WHEN 'f' THEN 'FUNCTIONS'
        WHEN 'T' THEN 'TYPES'
        WHEN 'n' THEN 'SCHEMAS'
    END,
    CASE WHEN a.grantee = 0 THEN 'PUBLIC'
         WHEN a.grantee = 10 THEN 'app'
         ELSE quote_ident(pg_get_userbyid(a.grantee)) END,
    CASE WHEN a.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END
)
FROM pg_default_acl d
LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
CROSS JOIN LATERAL aclexplode(d.defaclacl) a
ORDER BY d.oid, a.grantee, a.privilege_type;
SQL

  verify_converted "$db" "${db}__converted"

  psql_super -d postgres -c "ALTER DATABASE \"$db\" RENAME TO \"${db}__pre_demote\";"
  psql_super -d postgres -c "ALTER DATABASE \"${db}__converted\" RENAME TO \"$db\";"
  pre_demote_dbs+=("${db}__pre_demote")
  echo "Converted database $db (rollback copy kept as ${db}__pre_demote)."
done

psql_super -d postgres -c "DROP ROLE IF EXISTS arno_convert;"

echo ""
echo "Final role table:"
psql_super -d postgres <<'SQL'
SELECT
    r.rolname,
    r.rolsuper,
    r.rolinherit,
    r.rolcreaterole,
    r.rolcreatedb,
    r.rolcanlogin,
    r.rolreplication,
    r.rolbypassrls,
    coalesce(
        (
            SELECT string_agg(
                g.rolname || '(admin=' || m.admin_option || ',inherit=' || m.inherit_option || ',set=' || m.set_option || ')',
                ', ' ORDER BY g.rolname
            )
            FROM pg_auth_members m
            JOIN pg_roles g ON g.oid = m.roleid
            WHERE m.member = r.oid
        ),
        ''
    ) AS memberships
FROM pg_roles r
WHERE r.rolname IN ('postgres', 'app', 'agent_ro', 'backup', 'phoenix')
ORDER BY r.rolname;
SQL

if [ "${#pre_demote_dbs[@]}" -gt 0 ]; then
  echo ""
  echo "Rollback copies kept. Once you have verified the app works, drop them with:"
  drop_args=""
  for pre in "${pre_demote_dbs[@]}"; do
    drop_args="${drop_args} -c 'DROP DATABASE \"$pre\";'"
  done
  echo "  docker compose exec db psql -U postgres -d postgres${drop_args}"
fi
