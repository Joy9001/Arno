from __future__ import annotations

import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from pydantic_ai.usage import RequestUsage

from arno.agent.usage import record_usage
from arno.backup_restore import (
    DUMP_NAME,
    RestoreError,
    find_dump_path,
    prepare_database,
    restore_from_restic,
)
from arno.config import get_settings
from arno.db.conn import connect, conninfo_to_dict, make_conninfo, quote_ident
from arno.jobs import backup as backup_job
from arno.jobs.backup import run_backup
from arno.ledger.accounts import create_account
from arno.ledger.export import export_pg_dump
from arno.ledger.movements import record_expense
from tests.conftest import cluster_lock, fresh_database
from tests.fixtures.postgres import binary, client_env, dump_database, missing

CLIENT_SKIP = missing() if binary("PG_DUMP", "pg_dump") else "the PostgreSQL client is missing"
RESTIC_SKIP = CLIENT_SKIP or (None if binary("RESTIC", "restic") else "restic is not installed")
PASSWORD = "local-test-password"


def _repo_holding(dump: Path, tmp_path: Path) -> str:
    """A restic repository whose latest snapshot holds `dump` where the backup job stores it."""
    repo = str(tmp_path / "restic-repo")
    env = os.environ | {"RESTIC_REPOSITORY": repo, "RESTIC_PASSWORD": PASSWORD}
    restic = str(binary("RESTIC", "restic"))
    assert (
        subprocess.run([restic, "init"], capture_output=True, env=env, check=False).returncode == 0
    )
    stored = subprocess.run(
        [restic, "backup", "--stdin", "--stdin-filename", DUMP_NAME],
        input=dump.read_bytes(),
        capture_output=True,
        env=env,
        check=False,
    )
    assert stored.returncode == 0, stored.stderr.decode("utf-8", "replace")
    return repo


def test_find_dump_path_takes_the_current_name_then_the_sidecars():
    def listing(*paths: str) -> bytes:
        lines = [{"struct_type": "snapshot"}] + [
            {"struct_type": "node", "type": "file", "path": path} for path in paths
        ]
        return os.linesep.encode().join(json.dumps(line).encode() for line in lines)

    assert find_dump_path(listing("/fin_agent.dump")) == "/fin_agent.dump"
    assert find_dump_path(listing("/tmp/backup_manifest.json", "/tmp/fin_agent.dump")) == (
        "/tmp/fin_agent.dump"
    )
    assert find_dump_path(listing("/tmp/fin_agent.dump", "/fin_agent.dump")) == "/fin_agent.dump"
    assert find_dump_path(listing("/other")) is None


async def _restore(repo: str, target: str) -> dict[str, bool]:
    async with cluster_lock():
        return await restore_from_restic(
            repository=repo, password=PASSWORD, target_dsn=target, force=True
        )


def _backup_dsn() -> str | None:
    password = os.environ.get("BACKUP_PASSWORD")
    if not password:
        return None
    info = conninfo_to_dict(os.environ["DATABASE_URL"])
    info["user"] = "backup"
    info["password"] = password
    return make_conninfo(**info)


async def _restore_sql_dump(dump: bytes, target_dsn: str) -> None:
    psql = binary("PSQL", "psql")
    if psql is None:
        pytest.skip("psql is not installed")
    info = conninfo_to_dict(target_dsn)
    result = subprocess.run(
        [psql, "-v", "ON_ERROR_STOP=1", "-d", str(info.get("dbname", ""))],
        input=dump,
        capture_output=True,
        env=client_env(target_dsn),
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")[-2000:]


async def _counts(dsn: str) -> dict[str, int]:
    async with connect(dsn) as conn:
        return {
            table: (await conn.fetchrow_required(f"SELECT count(*) AS n FROM {table}"))["n"]
            for table in ("accounts", "movements", "categories", "schedules", "goals")
        }


async def _cost_state(dsn: str) -> dict[str, list[dict]]:
    async with connect(dsn) as conn:
        return {
            table: await conn.fetch(f"SELECT * FROM {table} ORDER BY 1") for table in ("llm_usage",)
        }


async def test_the_backup_role_reads_everything_and_only_inserts_its_own_runs(ledger):
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
async def test_prepare_database_refuses_to_replace_a_populated_database_without_force():
    async with fresh_database("arno_restore_populated") as target:
        async with connect(target) as conn:
            await conn.execute("CREATE TABLE kept (id int)")

        with pytest.raises(RestoreError, match="already has tables"):
            await prepare_database(target)

        async with connect(target) as conn:
            assert await conn.fetchval("SELECT to_regclass('public.kept')") is not None

        # `ensure_roles` is cluster-wide DDL; other workers may be migrating a clone right now.
        async with cluster_lock():
            await prepare_database(target, force=True)
        async with connect(target) as conn:
            assert await conn.fetchval("SELECT to_regclass('public.kept')") is None


@pytest.mark.xdist_group("cluster")
async def test_prepare_database_fails_closed_when_it_cannot_inspect_the_target():
    """An unreadable target must refuse: it is about to be dropped, not treated as empty."""
    async with fresh_database("arno_restore_probe") as target:
        info = conninfo_to_dict(target)
        name = str(info["dbname"])
        admin = make_conninfo(**{**info, "dbname": "postgres"})
        async with connect(admin) as conn:
            await conn.execute(f"ALTER DATABASE {quote_ident(name)} ALLOW_CONNECTIONS false")
        try:
            with pytest.raises(RestoreError, match="Could not inspect the target database"):
                await prepare_database(target)
        finally:
            async with connect(admin) as conn:
                await conn.execute(f"ALTER DATABASE {quote_ident(name)} ALLOW_CONNECTIONS true")


@pytest.mark.xdist_group("cluster")
@pytest.mark.skipif(CLIENT_SKIP is not None, reason=CLIENT_SKIP or "")
async def test_pg_dump_restore_round_trip(ledger):
    settings = get_settings(refresh=True)
    # A real restore must preserve the usage log.
    for model in ("gpt-test", "gpt-other"):
        await record_usage(
            ledger,
            "chat",
            model,
            RequestUsage(input_tokens=10, output_tokens=5),
            Decimal("0.012345"),
        )
    source_cost_state = await _cost_state(settings.database_url)
    dump = export_pg_dump(settings.database_url)
    source_counts = await _counts(settings.database_url)
    async with fresh_database("arno_restore") as target:
        await _restore_sql_dump(dump, target)
        assert await _counts(target) == source_counts
        assert await _cost_state(target) == source_cost_state


@pytest.mark.xdist_group("cluster")
@pytest.mark.skipif(RESTIC_SKIP is not None, reason=RESTIC_SKIP or "")
async def test_the_backup_job_round_trips_through_restic_and_restore(
    ledger, tmp_path: Path, monkeypatch
):
    get_settings(refresh=True)
    await ledger.execute(
        "INSERT INTO preferences (body, created_by) VALUES ('Restore preference', 'user')"
    )
    source_counts = await _counts(os.environ["DATABASE_URL"])
    repo = str(tmp_path / "restic-repo")
    monkeypatch.setenv("RESTIC_REPOSITORY", repo)
    monkeypatch.setenv("RESTIC_PASSWORD", PASSWORD)

    assert await run_backup(ledger) is True
    run = await ledger.fetchrow_required("SELECT * FROM backup_runs ORDER BY created_at DESC")
    assert run["status"] == "success" and run["restic_snapshot"] and run["dump_bytes"] > 0

    async with fresh_database("arno_restic_restore") as target:
        checks = await _restore(repo, target)
        assert checks and all(checks.values())
        assert await _counts(target) == source_counts
        async with connect(target) as restored:
            assert (
                await restored.fetchval(
                    "SELECT count(*) FROM preferences WHERE body = 'Restore preference'"
                )
                == 1
            )


@pytest.mark.skipif(RESTIC_SKIP is not None, reason=RESTIC_SKIP or "")
async def test_a_failed_backup_is_recorded_without_the_password(ledger, tmp_path, monkeypatch):
    get_settings(refresh=True)
    monkeypatch.setenv("RESTIC_REPOSITORY", str(tmp_path / "restic-repo"))
    monkeypatch.setenv("RESTIC_PASSWORD", PASSWORD)
    monkeypatch.setenv("PG_DUMP", str(tmp_path / "no-such-pg_dump"))

    assert await run_backup(ledger) is False
    run = await ledger.fetchrow_required("SELECT * FROM backup_runs ORDER BY created_at DESC")
    assert run["status"] == "failed" and run["error"]
    assert PASSWORD not in run["error"]
    assert run["restic_snapshot"] is None


async def test_an_unexpected_backup_error_is_recorded_generically_and_the_alert_still_runs(
    ledger, monkeypatch
):
    get_settings(refresh=True)
    monkeypatch.setenv("RESTIC_REPOSITORY", "rclone:secret-remote:repo")
    monkeypatch.setenv("RESTIC_PASSWORD", PASSWORD)

    def explode(*_args):
        raise KeyError("rclone:secret-remote:repo")

    alerts = []

    async def spy(conn):
        alerts.append(conn)

    monkeypatch.setattr(backup_job, "_ship", explode)
    monkeypatch.setattr(backup_job, "backup_alert", spy)

    await backup_job.handle_backup_check()

    run = await ledger.fetchrow_required("SELECT * FROM backup_runs ORDER BY created_at DESC")
    assert (run["status"], run["error"]) == ("failed", "backup failed: KeyError")
    assert len(alerts) == 1


@pytest.mark.xdist_group("cluster")
@pytest.mark.skipif(RESTIC_SKIP is not None, reason=RESTIC_SKIP or "")
async def test_a_restored_dump_keeps_the_agents_read_boundary(ledger, tmp_path: Path):
    """The baseline's boundary comes from the dump's own grants, not a second copy in code."""
    dump_database(tmp_path / "fin_agent.dump", os.environ["DATABASE_URL"], privileges=True)

    repo = _repo_holding(tmp_path / "fin_agent.dump", tmp_path)
    async with fresh_database("arno_restore_grants") as target:
        await _restore(repo, target)
        async with connect(target) as restored:
            reads = {
                table: await restored.fetchval(
                    "SELECT has_table_privilege('agent_ro', $1, 'SELECT')", table
                )
                for table in (
                    "v_accounts",
                    "v_legs",
                    "v_balances",
                    "movements",
                    "payees",
                    "preferences",
                    "schedule_occurrences",
                    "v_spending",
                )
            }
            assert all(reads.values()), reads
            # Nothing from auth, secrets, jobs, raw ledger tables, or the undo trail.
            hidden = {
                table: await restored.fetchval(
                    "SELECT has_table_privilege('agent_ro', $1, 'SELECT')", table
                )
                for table in (
                    "accounts",
                    "movement_legs",
                    "undo_log",
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
                "SELECT has_table_privilege('backup', 'backup_runs', 'INSERT')"
            )
            assert not await restored.fetchval(
                "SELECT has_table_privilege('backup', 'backup_runs', 'UPDATE')"
            )

            # 0006's default privileges come back too, so a later table needs no new grant.
            await restored.execute("CREATE TABLE restore_later (id int)")
            assert await restored.fetchval(
                "SELECT has_table_privilege('backup', 'restore_later', 'SELECT')"
            )


@pytest.mark.xdist_group("cluster")
@pytest.mark.skipif(RESTIC_SKIP is not None, reason=RESTIC_SKIP or "")
async def test_an_older_dump_without_grants_still_restores(ledger, tmp_path: Path):
    """A dump from an older image still restores and runs; only `agent_ro` stays unprivileged."""
    bank = await create_account(ledger, kind="bank", name="Old dump bank")
    await record_expense(ledger, account_id=bank["id"], amount_paise=5_000, description="Tea")
    dump_database(tmp_path / "fin_agent.dump", os.environ["DATABASE_URL"], privileges=False)

    repo = _repo_holding(tmp_path / "fin_agent.dump", tmp_path)
    async with fresh_database("arno_restore_old") as target:
        await _restore(repo, target)
        async with connect(target) as restored:
            assert await restored.fetchval("SELECT count(*) FROM movements") == 1
            assert not await restored.fetchval(
                "SELECT has_table_privilege('agent_ro', 'accounts', 'SELECT')"
            )
            assert await restored.fetchval(
                "SELECT has_table_privilege('backup', 'accounts', 'SELECT')"
            )
