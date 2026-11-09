"""The milestone 37 restore drill: restic repository + password -> a verified database.

The backup sidecar writes two files into every snapshot: the `pg_dump` and
`backup_manifest.json`, a row count and content hash for every table. Restoring into a fresh
database and recomputing the same manifest proves the ledger, balances, schedules, goals,
preferences, conversations, attachments, and audit history all came back.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from asyncpg import PostgresError

from fin_agent.db.conn import Connection, connect, conninfo_to_dict, make_conninfo, quote_ident

MANIFEST_NAME = "backup_manifest.json"
DUMP_NAMES = ("fin_agent.dump", "fin_agent.sql")

REQUIRED_TABLES = {
    "accounts",
    "movements",
    "pgledger_accounts",
    "pgledger_entries",
    "pgledger_transfers",
    "schedules",
    "goals",
    "preferences",
    "conversations",
    "messages",
    "attachments",
    "audit_log",
    "schema_version",
}

# Login roles live on the cluster, not in a database, so `pg_dump` cannot carry them. A restore
# into a fresh cluster must recreate them before anything names them. The list and settings mirror
# migrations 0002 and 0006; their passwords are set again from the environment at boot.
ROLE_STATEMENTS = (
    "ALTER ROLE agent_ro NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS",
    "ALTER ROLE agent_ro SET default_transaction_read_only = on",
    "ALTER ROLE agent_ro SET statement_timeout = '2s'",
    "ALTER ROLE backup NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS",
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
    "GRANT EXECUTE ON FUNCTION fin_agent_backup_manifest() TO backup",
)


class RestoreError(RuntimeError):
    """The restore could not run, or the restored database failed verification."""


@dataclass
class RestoreReport:
    snapshot: str
    dump: Path
    manifest: dict[str, Any] | None
    checks: dict[str, bool] = field(default_factory=dict)
    mismatches: list[str] = field(default_factory=list)
    restored_database: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values()) and not self.mismatches

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "snapshot": self.snapshot,
            "dump": str(self.dump),
            "restored_database": self.restored_database,
            "manifest_present": self.manifest is not None,
            "checks": self.checks,
            "mismatches": self.mismatches,
        }


async def build_manifest(conn: Connection) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT fin_agent_backup_manifest() AS manifest")
    return dict(row["manifest"])


def _binary(env_name: str, name: str) -> str:
    executable = os.environ.get(env_name) or shutil.which(name)
    if executable is None:
        raise RestoreError(
            f"{name} is not installed. Run the drill where restic and the PostgreSQL 18 client "
            "are available, or restore the snapshot to a directory and pass --from-dir."
        )
    return executable


def _restic(repository: str, password: str) -> tuple[str, dict[str, str]]:
    return _binary("RESTIC", "restic"), os.environ | {
        "RESTIC_REPOSITORY": repository,
        "RESTIC_PASSWORD": password,
    }


def extract_snapshot(
    repository: str,
    password: str,
    target: Path,
    *,
    snapshot: str = "latest",
) -> Path:
    """Dump the dump and manifest out of one snapshot into `target`.

    `restic dump` is used rather than a full tree restore: on Windows a full restore tries to set
    timestamps on directories it creates outside the target, which fails. The two files are all the
    drill needs.
    """
    restic, env = _restic(repository, password)
    target.mkdir(parents=True, exist_ok=True)
    listing = subprocess.run(
        [restic, "ls", "--no-lock", "--json", snapshot],
        capture_output=True,
        env=env,
        check=False,
    )
    if listing.returncode != 0:
        raise RestoreError(
            "restic ls failed: " + listing.stderr.decode("utf-8", "replace").strip()[-1000:]
        )
    wanted = {*DUMP_NAMES, MANIFEST_NAME}
    found = False
    for line in listing.stdout.decode("utf-8", "replace").splitlines():
        try:
            node = json.loads(line)
        except json.JSONDecodeError:
            continue
        if node.get("struct_type") != "node" or node.get("type") != "file":
            continue
        if node.get("name") not in wanted:
            continue
        extracted = subprocess.run(
            [restic, "dump", "--no-lock", snapshot, node["path"]],
            capture_output=True,
            env=env,
            check=False,
        )
        if extracted.returncode != 0:
            raise RestoreError(
                "restic dump failed: " + extracted.stderr.decode("utf-8", "replace").strip()[-1000:]
            )
        (target / node["name"]).write_bytes(extracted.stdout)
        found = True
    if not found:
        raise RestoreError(f"Snapshot {snapshot} has no Fin-Agent dump or manifest.")
    return target


def find_backup_files(root: Path) -> tuple[Path, Path | None]:
    dumps = [path for path in root.rglob("*") if path.is_file() and path.name in DUMP_NAMES]
    if not dumps:
        dumps = [path for path in root.rglob("*.dump") if path.is_file()]
    if not dumps:
        raise RestoreError(f"No pg_dump file found under {root}")
    manifests = [path for path in root.rglob(MANIFEST_NAME) if path.is_file()]
    return dumps[0], manifests[0] if manifests else None


def _admin_dsn(target_dsn: str) -> tuple[str, dict[str, Any]]:
    info = conninfo_to_dict(target_dsn)
    name = str(info.get("dbname", ""))
    if not name:
        raise RestoreError("The target database URL needs a database name.")
    admin = make_conninfo(**{**info, "dbname": "postgres"})
    return admin, info


async def _database_has_tables(target_dsn: str) -> bool:
    try:
        async with connect(target_dsn) as conn:
            count = await conn.fetchval(
                """
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                """
            )
            return bool(count)
    except OSError, PostgresError:
        return False


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
        database = await conn.fetchval("SELECT current_database()")
        await conn.execute(f"GRANT CONNECT ON DATABASE {quote_ident(database)} TO agent_ro, backup")
        await conn.execute(f"REVOKE TEMP ON DATABASE {quote_ident(database)} FROM PUBLIC")


async def grant_backup_reader(target_dsn: str) -> None:
    """Give the backup role its read access again after the table privileges were restored."""
    async with connect(target_dsn) as conn:
        for statement in BACKUP_GRANTS:
            await conn.execute(statement)


def _restore_dump(dump: Path, target_dsn: str) -> None:
    env = os.environ.copy()
    info = conninfo_to_dict(target_dsn)
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

    dbname = str(info.get("dbname", ""))
    if dump.suffix == ".sql":
        executable = _binary("PSQL", "psql")
        command = [executable, "-v", "ON_ERROR_STOP=1", "-d", dbname, "-f", str(dump)]
    else:
        # The dump's GRANTs are applied: `ensure_roles` has already created the roles they name,
        # and `agent_ro`'s SELECT list is the migrations', not a second copy in this file.
        executable = _binary("PG_RESTORE", "pg_restore")
        command = [
            executable,
            "--no-owner",
            "-d",
            dbname,
            str(dump),
        ]
    result = subprocess.run(command, capture_output=True, env=env, check=False)
    if result.returncode != 0:
        raise RestoreError(
            f"{Path(executable).name} failed: "
            + result.stderr.decode("utf-8", "replace").strip()[-2000:]
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


async def verify_database(
    conn: Connection,
    *,
    manifest: dict[str, Any] | None,
    dump_sha256: str | None,
    require_manifest: bool = True,
) -> tuple[dict[str, bool], list[str]]:
    checks: dict[str, bool] = {}
    mismatches: list[str] = []

    tables = await conn.fetch(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        """
    )
    present = {str(row["table_name"]) for row in tables}
    checks["required_tables"] = REQUIRED_TABLES <= present

    version = await conn.fetchval("SELECT coalesce(max(version), 0) AS version FROM schema_version")
    checks["schema_version"] = int(version) > 0

    unbalanced = await conn.fetchval(
        """
        SELECT count(*) FROM (
            SELECT e.transfer_id
            FROM pgledger_entries e
            GROUP BY e.transfer_id
            HAVING sum(e.amount) <> 0
        ) AS bad
        """
    )
    checks["ledger_transfers_balance"] = unbalanced == 0

    drifted = await conn.fetchval(
        """
        SELECT count(*) FROM (
            SELECT a.id
            FROM pgledger_accounts a
            LEFT JOIN (
                SELECT account_id, sum(amount) AS total FROM pgledger_entries GROUP BY account_id
            ) e ON e.account_id = a.id
            WHERE coalesce(e.total, 0) <> a.balance
        ) AS bad
        """
    )
    checks["account_balances_match_entries"] = drifted == 0

    if manifest is None:
        checks["manifest_present"] = not require_manifest
        checks["manifest_matches"] = not require_manifest
        if require_manifest:
            mismatches.append(
                "No backup_manifest.json in the snapshot; content could not be compared."
            )
        return checks, mismatches

    checks["manifest_matches"] = True
    if dump_sha256 and manifest.get("dump_sha256") and manifest["dump_sha256"] != dump_sha256:
        checks["manifest_matches"] = False
        mismatches.append("The dump's SHA-256 does not match the manifest.")

    actual = await build_manifest(conn)
    expected_tables = manifest.get("tables") or {}
    actual_tables = actual.get("tables") or {}
    for table in sorted(set(expected_tables) | set(actual_tables)):
        expected = expected_tables.get(table)
        found = actual_tables.get(table)
        if expected is None:
            checks["manifest_matches"] = False
            mismatches.append(f"{table}: present after restore but missing from the manifest")
        elif found is None:
            checks["manifest_matches"] = False
            mismatches.append(f"{table}: in the manifest but missing after restore")
        elif expected != found:
            checks["manifest_matches"] = False
            mismatches.append(
                f"{table}: manifest {expected.get('rows')} rows/{expected.get('md5')}, "
                f"restored {found.get('rows')} rows/{found.get('md5')}"
            )
    return checks, mismatches


async def restore_from_directory(
    directory: Path,
    target_dsn: str,
    *,
    force: bool = False,
    require_manifest: bool = True,
    snapshot_name: str = "local",
) -> RestoreReport:
    dump, manifest_path = find_backup_files(directory)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path else None
    if manifest is not None and manifest.get("format") != "fin-agent-backup-manifest":
        raise RestoreError(f"{manifest_path} is not a Fin-Agent backup manifest.")
    dump_hash = _sha256(dump)

    name = await prepare_database(target_dsn, force=force)
    _restore_dump(dump, target_dsn)
    try:
        await grant_backup_reader(target_dsn)
    except PostgresError as exc:
        raise RestoreError(f"Could not restore the backup role's grants: {exc}") from exc
    async with connect(target_dsn) as conn:
        checks, mismatches = await verify_database(
            conn,
            manifest=manifest,
            dump_sha256=dump_hash,
            require_manifest=require_manifest,
        )
    report = RestoreReport(
        snapshot=snapshot_name,
        dump=dump,
        manifest=manifest,
        checks=checks,
        mismatches=mismatches,
        restored_database=name,
    )
    if not report.ok:
        raise RestoreError(
            "Restore verification failed: " + "; ".join(mismatches or ["a check did not pass"])
        )
    return report


async def restore_from_restic(
    *,
    repository: str,
    password: str,
    target_dsn: str,
    snapshot: str = "latest",
    force: bool = False,
    require_manifest: bool = True,
    keep_directory: Path | None = None,
) -> RestoreReport:
    if not password:
        raise RestoreError(
            "RESTIC_PASSWORD is not set; the off-site copies are unreadable without it."
        )
    if keep_directory is not None:
        directory = extract_snapshot(repository, password, keep_directory, snapshot=snapshot)
        return await restore_from_directory(
            directory,
            target_dsn,
            force=force,
            require_manifest=require_manifest,
            snapshot_name=snapshot,
        )
    with tempfile.TemporaryDirectory(
        prefix="fin-agent-restore-", ignore_cleanup_errors=True
    ) as tmp:
        directory = extract_snapshot(repository, password, Path(tmp), snapshot=snapshot)
        return await restore_from_directory(
            directory,
            target_dsn,
            force=force,
            require_manifest=require_manifest,
            snapshot_name=snapshot,
        )
