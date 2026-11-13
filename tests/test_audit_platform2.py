"""Platform audit, wave 2: a restored dump keeps the table grants the migrations wrote.

`pg_dump --no-privileges` used to drop every ACL from the sidecar's backup, so a restored
database answered `permission denied` for `agent_ro`'s `run_query` and for the backup sidecar.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from arno.backup_restore import restore_from_directory
from arno.db.conn import connect, conninfo_to_dict, make_conninfo


def _binary(env_name: str, name: str) -> str | None:
    return os.environ.get(env_name) or shutil.which(name)


@asynccontextmanager
async def _fresh_database(name: str) -> AsyncGenerator[str]:
    info = conninfo_to_dict(os.environ["DATABASE_URL"])
    admin = make_conninfo(**{**info, "dbname": "postgres"})
    async with connect(admin) as conn:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(**{**info, "dbname": name})
    finally:
        async with connect(admin) as conn:
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _dump(directory: Path, *, privileges: bool) -> None:
    """Dump the scratch database the way the sidecar does, or the way older images did."""
    source = os.environ["DATABASE_URL"]
    env = os.environ.copy()
    info = conninfo_to_dict(source)
    if "password" in info:
        env["PGPASSWORD"] = str(info["password"])
    pg_dump = _binary("PG_DUMP", "pg_dump")
    assert pg_dump is not None  # the module's CLIENT_SKIP checks it
    command = [pg_dump, "--no-owner"]
    if not privileges:
        command.append("--no-privileges")
    # The backup format predates the Arno name; existing archives carry it.
    command += ["--format=custom", "-f", str(directory / "fin_agent.dump"), source]
    dumped = subprocess.run(command, capture_output=True, env=env, check=False)
    assert dumped.returncode == 0, dumped.stderr.decode("utf-8", "replace")


CLIENT_SKIP = (
    None
    if (_binary("PG_DUMP", "pg_dump") and _binary("PG_RESTORE", "pg_restore"))
    else "the PostgreSQL client is missing"
)


@pytest.mark.xdist_group("cluster")
@pytest.mark.skipif(CLIENT_SKIP is not None, reason=CLIENT_SKIP or "")
async def test_a_restored_dump_keeps_the_agents_read_boundary(demo_ledger, tmp_path: Path):
    _dump(tmp_path, privileges=True)

    async with _fresh_database("arno_platform2_restore_test") as target:
        report = await restore_from_directory(tmp_path, target, force=True, require_manifest=False)
        assert report.ok, report.as_dict()
        async with connect(target) as restored:
            # Migration 0036's read boundary: semantic views and allowed tables...
            reads = {
                table: await restored.fetchval(
                    "SELECT has_table_privilege('agent_ro', $1, 'SELECT')", table
                )
                for table in (
                    "v_accounts",
                    "v_legs",
                    "v_movement_state",
                    "movements",
                    "payees",
                    "preferences",
                    "schedule_occurrences",
                    "v_spending",
                )
            }
            assert all(reads.values()), reads
            # ...and nothing from auth, secrets, jobs, raw ledger views/tables, or the audit trail.
            hidden = {
                table: await restored.fetchval(
                    "SELECT has_table_privilege('agent_ro', $1, 'SELECT')", table
                )
                for table in (
                    "accounts",
                    "movement_legs",
                    "pgledger_accounts_view",
                    "pgledger_transfers_view",
                    "pgledger_entries_view",
                    "pgledger_idempotency_keys_view",
                    "users",
                    "sessions",
                    "setup_tokens",
                    "webauthn_credentials",
                    "vapid_keys",
                    "audit_log",
                    "jobs",
                    "backup_runs",
                    "attachments",
                )
            }
            assert not any(hidden.values()), hidden
            assert not await restored.fetchval(
                "SELECT has_table_privilege('agent_ro', 'movements', 'INSERT')"
            )

            # The dumper reads everything for `pg_dump` and writes only its own status row.
            assert await restored.fetchval(
                "SELECT has_table_privilege('backup', 'accounts', 'SELECT')"
            )
            assert await restored.fetchval(
                "SELECT has_table_privilege('backup', 'users', 'SELECT')"
            )
            assert await restored.fetchval(
                "SELECT has_table_privilege('backup', 'backup_runs', 'INSERT')"
            )
            assert not await restored.fetchval(
                "SELECT has_table_privilege('backup', 'backup_runs', 'UPDATE')"
            )

            # 0006's default privileges come back too, so a later table needs no new grant.
            await restored.execute("CREATE TABLE audit_platform2_later (id int)")
            assert await restored.fetchval(
                "SELECT has_table_privilege('backup', 'audit_platform2_later', 'SELECT')"
            )


@pytest.mark.xdist_group("cluster")
@pytest.mark.skipif(CLIENT_SKIP is not None, reason=CLIENT_SKIP or "")
async def test_an_older_dump_without_grants_still_restores(demo_ledger, tmp_path: Path):
    """A dump from an older image still restores and runs; only `agent_ro` stays unprivileged."""
    _dump(tmp_path, privileges=False)

    async with _fresh_database("arno_platform2_old_dump_test") as target:
        report = await restore_from_directory(tmp_path, target, force=True, require_manifest=False)
        assert report.ok, report.as_dict()
        async with connect(target) as restored:
            movements = await restored.fetchval("SELECT count(*) FROM movements")
            assert movements is not None
            assert movements > 0
            assert not await restored.fetchval(
                "SELECT has_table_privilege('agent_ro', 'accounts', 'SELECT')"
            )
            assert await restored.fetchval(
                "SELECT has_table_privilege('backup', 'accounts', 'SELECT')"
            )
