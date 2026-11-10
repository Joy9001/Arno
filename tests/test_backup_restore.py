from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import pytest

from fin_agent.config import get_settings
from fin_agent.db.conn import Connection, connect, conninfo_to_dict, make_conninfo
from fin_agent.ledger.export import export_pg_dump


def _backup_dsn() -> str | None:
    password = os.environ.get("BACKUP_PASSWORD")
    if not password:
        return None
    info = conninfo_to_dict(os.environ["DATABASE_URL"])
    info["user"] = "backup"
    info["password"] = password
    return make_conninfo(**info)


@asynccontextmanager
async def _admin_conn() -> AsyncGenerator[Connection]:
    info = conninfo_to_dict(os.environ["DATABASE_URL"])
    info["dbname"] = "postgres"
    async with connect(make_conninfo(**info)) as conn:
        yield conn


async def _restore_database(dump: bytes, name: str) -> None:
    psql = os.environ.get("PSQL") or shutil.which("psql")
    if psql is None:
        pytest.skip("psql is not installed")
    info = conninfo_to_dict(os.environ["DATABASE_URL"])
    info["dbname"] = name
    dsn = make_conninfo(**info)
    env = os.environ.copy()
    if "password" in info:
        env["PGPASSWORD"] = str(info["password"])
    async with _admin_conn() as admin:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{name}"')
    try:
        result = subprocess.run(
            [psql, "-v", "ON_ERROR_STOP=1", "-d", dsn],
            input=dump,
            capture_output=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")[-2000:]
    except Exception:
        async with _admin_conn() as admin:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        raise


async def _counts(dsn: str) -> dict[str, int]:
    async with connect(dsn) as conn:
        return {
            table: (await conn.fetchrow_required(f"SELECT count(*) AS n FROM {table}"))["n"]
            for table in ("accounts", "movements", "categories", "schedules", "goals")
        }


async def test_backup_role_can_dump_and_only_insert_runs(ledger):
    dsn = _backup_dsn()
    if dsn is None:
        pytest.skip("BACKUP_PASSWORD is not set")

    async with connect(dsn) as conn:
        assert (await conn.fetchrow_required("SELECT current_user AS who"))["who"] == "backup"
        assert (await conn.fetchrow_required("SELECT count(*) AS n FROM accounts"))["n"] >= 0
        await conn.execute(
            """
            INSERT INTO backup_runs (started_at, finished_at, status, dump_bytes, duration_ms)
            VALUES (now(), now(), 'success', 10, 5)
            """
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("INSERT INTO categories (name) VALUES ('Not allowed')")


@pytest.mark.xdist_group("cluster")
@pytest.mark.skipif(
    not (os.environ.get("PG_DUMP") or shutil.which("pg_dump"))
    or not (os.environ.get("PSQL") or shutil.which("psql")),
    reason="pg_dump/psql missing",
)
async def test_pg_dump_restore_round_trip(ledger):
    settings = get_settings(refresh=True)
    dump = export_pg_dump(settings.database_url)
    source_counts = await _counts(settings.database_url)
    restore_name = "fin_agent_restore_test"
    try:
        await _restore_database(dump, restore_name)
        info = conninfo_to_dict(settings.database_url)
        info["dbname"] = restore_name
        restored_counts = await _counts(make_conninfo(**info))
        assert restored_counts == source_counts
    finally:
        async with _admin_conn() as admin:
            await admin.execute(f'DROP DATABASE IF EXISTS "{restore_name}" WITH (FORCE)')


@pytest.mark.xdist_group("cluster")
@pytest.mark.skipif(shutil.which("restic") is None, reason="restic is not installed")
async def test_restic_backup_and_restore_round_trip(ledger, tmp_path: Path):
    settings = get_settings(refresh=True)
    dump = export_pg_dump(settings.database_url)
    source_counts = await _counts(settings.database_url)
    repo = tmp_path / "restic-repo"
    env = os.environ | {
        "RESTIC_REPOSITORY": str(repo),
        "RESTIC_PASSWORD": "local-test-password",
    }

    init = subprocess.run(["restic", "init"], capture_output=True, env=env, check=False)
    assert init.returncode == 0, init.stderr.decode("utf-8", "replace")
    backup = subprocess.run(
        ["restic", "backup", "--stdin", "--stdin-filename", "fin_agent.dump"],
        input=dump,
        capture_output=True,
        env=env,
        check=False,
    )
    assert backup.returncode == 0, backup.stderr.decode("utf-8", "replace")
    extracted = subprocess.run(
        ["restic", "dump", "latest", "/fin_agent.dump"],
        capture_output=True,
        env=env,
        check=False,
    )
    assert extracted.returncode == 0, extracted.stderr.decode("utf-8", "replace")

    restore_name = "fin_agent_restic_restore_test"
    try:
        await _restore_database(extracted.stdout, restore_name)
        info = conninfo_to_dict(settings.database_url)
        info["dbname"] = restore_name
        assert await _counts(make_conninfo(**info)) == source_counts
    finally:
        async with _admin_conn() as admin:
            await admin.execute(f'DROP DATABASE IF EXISTS "{restore_name}" WITH (FORCE)')
