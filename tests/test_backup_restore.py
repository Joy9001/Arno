from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTCredentials

from arno.agent.chatgpt_auth import DatabaseChatGPTCredentialSource
from arno.agent.cost_journal import CostAttempt, record_final, record_intent
from arno.agent.pricing import CostObservation
from arno.agent.spend import make_spend_store, post_direct_call
from arno.backup_restore import RestoreError, prepare_database, restore_from_directory
from arno.config import get_settings
from arno.db.conn import connect, conninfo_to_dict, make_conninfo, quote_ident
from arno.ledger.accounts import create_account
from arno.ledger.export import export_pg_dump
from arno.ledger.movements import record_expense
from tests.conftest import cluster_lock, fresh_database
from tests.fixtures.postgres import binary, client_env, dump_database, missing

CLIENT_SKIP = missing() if binary("PG_DUMP", "pg_dump") else "the PostgreSQL client is missing"


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
            table: await conn.fetch(f"SELECT * FROM {table} ORDER BY 1")
            for table in ("llm_calls", "model_credentials", "spend_counters", "spend_dedup")
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
    # A real restore must preserve accounting and sign-in state, including an unfinished call.
    now = datetime.now(UTC)
    for suffix in ("1", "2"):
        attempt = CostAttempt(
            attempt_id=suffix * 32,
            trace_id="b" * 32,
            span_id=suffix * 16,
            role="chat",
            provider="openai",
            model="gpt-test",
            billing_mode="metered",
            workload="test-restore",
            session_id="opaque-session",
            started_at=now,
        )
        await record_intent(ledger, attempt)
        if suffix == "1":
            await record_final(
                ledger,
                attempt,
                CostObservation(Decimal("0.012345678"), "test", True, "restore/v1"),
                now,
            )
    await DatabaseChatGPTCredentialSource().save(
        OpenAIChatGPTCredentials(
            client_id="restore-client",
            access_token="synthetic-access",
            refresh_token="synthetic-refresh",
            scope="openid chatgpt.tokens.use.direct",
        )
    )
    store, pool = await make_spend_store(settings.database_url)
    try:
        await post_direct_call(
            store,
            CostObservation(Decimal("0.012345678"), "test", True, "restore/v1"),
            token="restore-attempt",
        )
    finally:
        if pool is not None:
            await pool.close()
    source_cost_state = await _cost_state(settings.database_url)
    dump = export_pg_dump(settings.database_url)
    source_counts = await _counts(settings.database_url)
    async with fresh_database("arno_restore") as target:
        await _restore_sql_dump(dump, target)
        assert await _counts(target) == source_counts
        assert await _cost_state(target) == source_cost_state


@pytest.mark.xdist_group("cluster")
@pytest.mark.skipif(shutil.which("restic") is None, reason="restic is not installed")
async def test_restic_backup_and_restore_round_trip(ledger, tmp_path: Path):
    settings = get_settings(refresh=True)
    await ledger.execute(
        "INSERT INTO scheduled_questions (prompt, rule) VALUES ('Restore question', 'daily')"
    )
    await ledger.execute(
        "INSERT INTO jobs (key, kind) VALUES ('restore-test:1', 'scheduled_question')"
    )
    await ledger.execute(
        "INSERT INTO preferences (body, created_by) VALUES ('Restore preference', 'user')"
    )
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
        # The backup format predates the Arno name; existing archives carry it.
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

    async with fresh_database("arno_restic_restore") as target:
        await _restore_sql_dump(extracted.stdout, target)
        assert await _counts(target) == source_counts
        async with connect(target) as restored:
            assert (
                await restored.fetchval(
                    "SELECT count(*) FROM scheduled_questions WHERE prompt = 'Restore question'"
                )
                == 1
            )
            assert (
                await restored.fetchval("SELECT count(*) FROM jobs WHERE key = 'restore-test:1'")
                == 1
            )
            assert (
                await restored.fetchval(
                    "SELECT count(*) FROM preferences WHERE body = 'Restore preference'"
                )
                == 1
            )


@pytest.mark.xdist_group("cluster")
@pytest.mark.skipif(CLIENT_SKIP is not None, reason=CLIENT_SKIP or "")
async def test_a_restored_dump_keeps_the_agents_read_boundary(ledger, tmp_path: Path):
    """Migration 0036's boundary comes from the dump's own grants, not a second copy in code."""
    dump_database(tmp_path / "fin_agent.dump", os.environ["DATABASE_URL"], privileges=True)

    async with fresh_database("arno_restore_grants") as target:
        async with cluster_lock():
            report = await restore_from_directory(
                tmp_path, target, force=True, require_manifest=False
            )
        assert report.ok, report.as_dict()
        async with connect(target) as restored:
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
            # Nothing from auth, secrets, jobs, raw ledger views/tables, or the audit trail.
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
            await restored.execute("CREATE TABLE restore_later (id int)")
            assert await restored.fetchval(
                "SELECT has_table_privilege('backup', 'restore_later', 'SELECT')"
            )


@pytest.mark.xdist_group("cluster")
@pytest.mark.skipif(CLIENT_SKIP is not None, reason=CLIENT_SKIP or "")
async def test_an_older_dump_without_grants_still_restores(ledger, tmp_path: Path):
    """A dump from an older image still restores and runs; only `agent_ro` stays unprivileged."""
    bank = await create_account(ledger, kind="bank", name="Old dump bank")
    await record_expense(ledger, account_id=bank["id"], amount_paise=5_000, description="Tea")
    dump_database(tmp_path / "fin_agent.dump", os.environ["DATABASE_URL"], privileges=False)

    async with fresh_database("arno_restore_old") as target:
        async with cluster_lock():
            report = await restore_from_directory(
                tmp_path, target, force=True, require_manifest=False
            )
        assert report.ok, report.as_dict()
        async with connect(target) as restored:
            assert await restored.fetchval("SELECT count(*) FROM movements") == 1
            assert not await restored.fetchval(
                "SELECT has_table_privilege('agent_ro', 'accounts', 'SELECT')"
            )
            assert await restored.fetchval(
                "SELECT has_table_privilege('backup', 'accounts', 'SELECT')"
            )
