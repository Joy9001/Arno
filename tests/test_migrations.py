from __future__ import annotations

import os

import asyncpg
import pytest

from fin_agent.db.conn import connect, conninfo_to_dict, make_conninfo
from fin_agent.db.migrate import applied, current_version, discover, run_migrations

EXPECTED_TABLES = {
    "settings",
    "users",
    "webauthn_credentials",
    "sessions",
    "webauthn_challenges",
    "setup_tokens",
    "pgledger_accounts",
    "pgledger_transfers",
    "pgledger_entries",
    "pgledger_idempotency_keys",
    "accounts",
    "movements",
    "transfer_labels",
    "captures",
    "imports",
    "statement_lines",
    "statement_layouts",
    "parser_versions",
    "attachments",
    "schedules",
    "goals",
    "scheduled_questions",
    "payees",
    "categories",
    "category_rules",
    "baselines",
    "preferences",
    "conversations",
    "messages",
    "audit_log",
    "notifications",
    "push_subscriptions",
    "jobs",
    "job_schedules",
    "backup_runs",
    "vapid_keys",
    "risk_state",
}

EXPECTED_VIEWS = {"v_spending", "v_cashflow", "v_balances", "v_owed"}


def _agent_ro_dsn() -> str:
    info = conninfo_to_dict(os.environ["DATABASE_URL"])
    info["user"] = "agent_ro"
    info["password"] = os.environ["AGENT_RO_PASSWORD"]
    return make_conninfo(**info)


async def test_every_migration_is_recorded_once(db):
    rows = await applied(db)
    versions = [row["version"] for row in rows]

    assert versions == list(range(1, len(discover()) + 1))
    assert await current_version(db) == len(discover())
    names = [row["name"] for row in rows]
    assert names[0] == "pgledger"
    assert {"views", "grants", "ledger_l1", "enrollment_tokens"} <= set(names)


async def test_migrations_are_idempotent(db):
    assert await run_migrations(db) == []
    assert await current_version(db) == len(discover())


async def test_schema_v1_has_every_designed_table_and_view(db):
    tables = {
        row["table_name"]
        for row in await db.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    }
    views = {
        row["table_name"]
        for row in await db.fetch(
            "SELECT table_name FROM information_schema.views WHERE table_schema = 'public'"
        )
    }

    assert EXPECTED_TABLES <= tables
    assert EXPECTED_VIEWS <= views
    assert {
        "pgledger_accounts_view",
        "pgledger_transfers_view",
        "pgledger_entries_view",
        "pgledger_idempotency_keys_view",
    } <= views


async def test_money_columns_are_whole_paise_bigints(db):
    columns = await db.fetch(
        """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name LIKE '%_paise'
        """
    )

    assert columns, "expected *_paise columns"
    assert {row["data_type"] for row in columns} == {"bigint"}


async def test_pgledger_ids_are_prefixed_ulids(db):
    row = await db.fetchrow("SELECT pgledger_generate_id('acc') AS id")

    assert row["id"].startswith("acc_")
    assert len(row["id"].split("_", 1)[1]) == 26


async def test_agent_ro_can_read_the_ledger_but_not_auth_tables():
    async with connect(_agent_ro_dsn()) as conn:
        assert (await conn.fetchrow("SELECT current_user AS who"))["who"] == "agent_ro"
        assert (await conn.fetchrow("SELECT count(*) AS n FROM accounts"))["n"] == 0
        assert (await conn.fetchrow("SELECT count(*) AS n FROM v_balances"))["n"] == 0
        assert await conn.fetchval("SELECT count(*) FROM schedule_occurrences") == 0
        assert await conn.fetchval("SELECT count(*) FROM schedule_payments") == 0

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("SELECT count(*) FROM users")


async def test_agent_ro_cannot_write_or_create_temp_tables():
    async with connect(_agent_ro_dsn()) as conn:
        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await conn.execute("INSERT INTO categories (name) VALUES ('x')")

        temp_allowed = (
            await conn.fetchrow(
                "SELECT has_database_privilege(current_user, current_database(), 'TEMP') AS allowed"
            )
        )["allowed"]
        assert temp_allowed is False
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("CREATE TEMP TABLE scratch (id int)")


async def test_agent_ro_has_read_only_and_two_second_defaults():
    async with connect(_agent_ro_dsn()) as conn:
        row = await conn.fetchrow(
            "SELECT current_setting('default_transaction_read_only') AS ro, "
            "current_setting('statement_timeout') AS timeout"
        )

    assert row["ro"] == "on"
    assert row["timeout"] == "2s"
