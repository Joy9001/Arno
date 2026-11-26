"""Restore a restic snapshot into a fresh database, then verify the ledger.

The backup job writes one `pg_dump` custom-format file per snapshot, stored as `DUMP_NAME`. Older
snapshots hold it at `/tmp/fin_agent.dump` and also hold a `backup_manifest.json`, which is ignored.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any

from asyncpg import PostgresError

from arno.db.conn import Connection, connect, conninfo_to_dict, make_conninfo, quote_ident

# The backup format predates the Arno name; existing archives carry it.
DUMP_NAME = "fin_agent.dump"  # stored at `/fin_agent.dump`: restic 0.18 rejects a directory
# The old sidecar's snapshots hold the dump here, so a restore tries it too.
_SIDECAR_DUMP_PATH = "/tmp/fin_agent.dump"  # noqa: S108 - a path inside the snapshot


def find_dump_path(listing: bytes) -> str | None:
    """The dump's path in a `restic ls --json` listing: the current one, else the sidecar's."""
    paths = set()
    for line in listing.decode("utf-8", "replace").splitlines():
        try:
            node = json.loads(line)
        except json.JSONDecodeError:
            continue
        if node.get("type") == "file":
            paths.add(node.get("path"))
    return next((p for p in (f"/{DUMP_NAME}", _SIDECAR_DUMP_PATH) if p in paths), None)


REQUIRED_TABLES = {
    "accounts",
    "movements",
    "movement_legs",
    "schedules",
    "goals",
    "preferences",
    "conversations",
    "messages",
    "attachments",
    "undo_log",
    "schema_version",
}

# Login roles live on the cluster, not in a database, so `pg_dump` cannot carry them. A restore
# into a fresh cluster must recreate them before anything names them. The list and settings mirror
# the baseline migration without the superuser-only `NO*` flags (PostgreSQL 18 rejects even
# `NOSUPERUSER`/`NOREPLICATION`/`NOBYPASSRLS` from a non-superuser role); `ensure_roles` verifies
# those three attributes are false instead. Their passwords are set again from the environment at
# boot.
ROLE_STATEMENTS = (
    "ALTER ROLE agent_ro NOCREATEDB NOCREATEROLE",
    "ALTER ROLE agent_ro SET default_transaction_read_only = on",
    "ALTER ROLE agent_ro SET statement_timeout = '2s'",
    "ALTER ROLE backup NOCREATEDB NOCREATEROLE",
)

# `agent_ro`'s SELECT list is the security boundary and is not guessed here: it comes back from
# the dump's own grants. The dumper's broad read access is safe to re-grant in full, which also
# repairs a dump made by an older image whose `pg_dump --no-privileges` carried no ACLs at all
# (`agent_ro` on such a dump stays SELECT-less until a current backup replaces it).
BACKUP_GRANTS = (
    "GRANT USAGE ON SCHEMA public TO backup",
    "GRANT SELECT ON ALL TABLES IN SCHEMA public TO backup",
    "GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO backup",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO backup",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON SEQUENCES TO backup",
    "GRANT INSERT ON backup_runs TO backup",
)


class RestoreError(RuntimeError):
    """The restore could not run, or the restored database failed verification."""


def find_binary(env_name: str, name: str) -> str:
    executable = os.environ.get(env_name) or shutil.which(name)
    if executable is None:
        raise RestoreError(
            f"{name} is not installed. Run the restore where restic and the PostgreSQL 18 client "
            "are available."
        )
    return executable


def _admin_dsn(target_dsn: str) -> tuple[str, dict[str, Any]]:
    info = conninfo_to_dict(target_dsn)
    name = str(info.get("dbname", ""))
    if not name:
        raise RestoreError("The target database URL needs a database name.")
    admin = make_conninfo(**{**info, "dbname": "postgres"})
    return admin, info


async def _database_has_tables(target_dsn: str) -> bool:
    """Whether the target database holds any table.

    An inspection that fails is not an empty database: the caller is about to drop whatever is
    there, so an unreadable target refuses the restore instead of being treated as replaceable.
    """
    try:
        async with connect(target_dsn) as conn:
            count = await conn.fetchval(
                """
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                """
            )
            return bool(count)
    except (OSError, PostgresError) as exc:
        raise RestoreError(
            f"Could not inspect the target database before replacing it: {exc}"
        ) from exc


async def prepare_database(target_dsn: str, *, force: bool = False) -> str:
    """Create the target, or replace it only when it is empty or `force` is set.

    The login roles are cluster state, not database state, so they are recreated here too: a
    restore into a fresh cluster has none, and the API boot (`ALTER ROLE …`) would fail. Boot sets
    their passwords from the environment.
    """
    admin, info = _admin_dsn(target_dsn)
    name = str(info["dbname"])
    async with connect(admin) as conn:
        exists = (
            await conn.fetchrow("SELECT 1 FROM pg_database WHERE datname = $1", name) is not None
        )
        if exists:
            if await _database_has_tables(target_dsn) and not force:
                raise RestoreError(
                    f"Database {name!r} already has tables. Pass --force to replace it, or choose "
                    "a fresh database."
                )
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                name,
            )
            await conn.execute(f"DROP DATABASE IF EXISTS {quote_ident(name)}")
        await conn.execute(f"CREATE DATABASE {quote_ident(name)}")
    try:
        await ensure_roles(target_dsn)
    except PostgresError as exc:
        raise RestoreError(f"Could not recreate the database's login roles: {exc}") from exc
    return name


async def ensure_roles(target_dsn: str) -> None:
    """Recreate the two login roles and their cluster settings after a fresh-cluster restore."""
    async with connect(target_dsn) as conn:
        for role in ("agent_ro", "backup"):
            exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
            if exists is None:
                await conn.execute(f"CREATE ROLE {quote_ident(role)} LOGIN")
        for statement in ROLE_STATEMENTS:
            await conn.execute(statement)
        elevated = await conn.fetchval(
            """
            SELECT array_agg(rolname ORDER BY rolname)
            FROM pg_roles
            WHERE rolname IN ('agent_ro', 'backup')
              AND (rolsuper OR rolreplication OR rolbypassrls)
            """
        )
        if elevated:
            raise RestoreError(
                f"Role(s) {elevated} hold privileged attributes that a non-superuser cannot clear"
            )
        database = (await conn.fetchrow_required("SELECT current_database() AS name"))["name"]
        if not isinstance(database, str):
            raise RestoreError("The target database's name could not be read.")
        await conn.execute(f"GRANT CONNECT ON DATABASE {quote_ident(database)} TO agent_ro, backup")
        await conn.execute(f"REVOKE TEMP ON DATABASE {quote_ident(database)} FROM PUBLIC")


async def grant_backup_reader(target_dsn: str) -> None:
    """Give the backup role its read access again after the table privileges were restored."""
    async with connect(target_dsn) as conn:
        for statement in BACKUP_GRANTS:
            await conn.execute(statement)


def pg_env(dsn: str) -> dict[str, str]:
    """The libpq environment for one DSN, so no password reaches a command line."""
    env = os.environ.copy()
    info = conninfo_to_dict(dsn)
    for source, target in (
        ("host", "PGHOST"),
        ("port", "PGPORT"),
        ("user", "PGUSER"),
        ("password", "PGPASSWORD"),
        ("dbname", "PGDATABASE"),
        ("sslmode", "PGSSLMODE"),
    ):
        if source in info:
            env[target] = str(info[source])
    return env


async def verify_database(conn: Connection) -> dict[str, bool]:
    checks: dict[str, bool] = {}

    tables = await conn.fetch(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        """
    )
    present = {str(row["table_name"]) for row in tables}
    checks["required_tables"] = REQUIRED_TABLES <= present

    version = (
        await conn.fetchrow_required(
            "SELECT coalesce(max(version), 0) AS version FROM schema_version"
        )
    )["version"]
    checks["schema_version"] = int(version) > 0

    orphans = await conn.fetchval(
        "SELECT count(*) FROM movements m "
        "WHERE NOT EXISTS (SELECT 1 FROM movement_legs l WHERE l.movement_id = m.id)"
    )
    checks["movements_have_legs"] = orphans == 0
    return checks


async def restore_from_restic(
    *,
    repository: str,
    password: str,
    target_dsn: str,
    snapshot: str = "latest",
    force: bool = False,
) -> dict[str, bool]:
    """Pipe `restic dump` into `pg_restore`, then verify; raise unless every check passes."""
    if not password:
        raise RestoreError(
            "RESTIC_PASSWORD is not set; the off-site copies are unreadable without it."
        )
    restic = find_binary("RESTIC", "restic")
    pg_restore = find_binary("PG_RESTORE", "pg_restore")
    env = os.environ | {"RESTIC_REPOSITORY": repository, "RESTIC_PASSWORD": password}
    # Look before dropping anything: an unreadable snapshot must not cost the target database.
    listing = subprocess.run(
        [restic, "ls", "--no-lock", "--json", snapshot],
        capture_output=True,
        env=env,
        check=False,
    )
    dump_path = find_dump_path(listing.stdout) if listing.returncode == 0 else None
    if dump_path is None:
        raise RestoreError(
            f"Snapshot {snapshot} has no Arno dump: "
            + listing.stderr.decode("utf-8", "replace").strip()[-1000:]
        )
    dump = [restic, "dump", "--no-lock", snapshot, dump_path]
    await prepare_database(target_dsn, force=force)
    # stderr goes to a file, not a pipe: an unread full pipe would block the dump forever.
    with (
        tempfile.TemporaryFile() as src_stderr,
        subprocess.Popen(dump, stdout=subprocess.PIPE, stderr=src_stderr, env=env) as src,
    ):
        assert src.stdout  # a PIPE
        # The dump's GRANTs are applied: `ensure_roles` has already created the roles they name,
        # and `agent_ro`'s SELECT list is the migrations', not a second copy in this file.
        loaded = subprocess.run(
            [pg_restore, "--no-owner", "-d", str(conninfo_to_dict(target_dsn)["dbname"])],
            stdin=src.stdout,
            capture_output=True,
            env=pg_env(target_dsn),
            check=False,
        )
        src.stdout.close()
        src.wait()
        src_stderr.seek(0)
        dump_error = src_stderr.read().decode("utf-8", "replace")
    if src.returncode != 0 or loaded.returncode != 0:
        raise RestoreError(
            "restore failed: " + (dump_error + loaded.stderr.decode("utf-8", "replace"))[-2000:]
        )
    try:
        await grant_backup_reader(target_dsn)
    except PostgresError as exc:
        raise RestoreError(f"Could not restore the backup role's grants: {exc}") from exc
    async with connect(target_dsn) as conn:
        checks = await verify_database(conn)
    if not all(checks.values()):
        failed = ", ".join(name for name, ok in checks.items() if not ok)
        raise RestoreError(f"Restore verification failed: {failed}")
    return checks
