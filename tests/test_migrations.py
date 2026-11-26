from __future__ import annotations

import os
import re
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest

from arno.db.bootstrap import set_agent_ro_password, set_backup_password
from arno.db.conn import Connection, connect, conninfo_to_dict, make_conninfo
from arno.db.migrate import (
    MigrationError,
    applied,
    current_version,
    discover,
    pending,
    run_migrations,
)
from tests.conftest import cluster_lock, fresh_database

EXPECTED_TABLES = {
    "accounts",
    "attachments",
    "backup_runs",
    "baselines",
    "captures",
    "categories",
    "category_rules",
    "conversations",
    "goals",
    "imports",
    "llm_usage",
    "messages",
    "movement_legs",
    "movements",
    "notifications",
    "payees",
    "preferences",
    "recap_views",
    "schedule_occurrences",
    "schedule_payments",
    "schedules",
    "scheduled_questions",
    "series_detections",
    "settings",
    "statement_lines",
    "undo_log",
}

VIEW_COLUMNS = {
    "v_accounts": (
        "id kind name institution last4 statement_balance_paise statement_balance_as_of liquid "
        "credit_limit_paise statement_day due_day rate_bps tenure_months emi_schedule_id active "
        "created_at updated_at balance_paise emi_rate_bps emi_fee_paise finance_rate_bps "
        "min_due_bps min_due_floor_paise"
    ),
    "v_balances": "account_id name kind liquid as_of balance_paise",
    "v_legs": (
        "movement_id transfer_id date movement_kind description from_account_id "
        "from_account_name from_account_kind from_account_liquid to_account_id to_account_name "
        "to_account_kind to_account_liquid amount_paise category_id category_name payee_id "
        "payee_name note expected_date"
    ),
    "v_owed": "payee_id display_name account_id balance_paise direction next_expected_date",
    "v_spending": (
        "movement_id date description transfer_id category_id category_name payee_id payee_name "
        "amount_paise"
    ),
}

# What agent_ro may read: the views and the tables the agent's SQL tool documents.
AGENT_RO_READABLE = {
    *VIEW_COLUMNS,
    "movements",
    "captures",
    "schedules",
    "schedule_occurrences",
    "schedule_payments",
    "series_detections",
    "goals",
    "scheduled_questions",
    "imports",
    "statement_lines",
    "payees",
    "categories",
    "category_rules",
    "baselines",
    "preferences",
    "conversations",
    "messages",
    "settings",
}


def _role_dsn(dsn: str, role: str, password_env: str) -> str:
    info = conninfo_to_dict(dsn)
    info["user"] = role
    info["password"] = os.environ[password_env]
    return make_conninfo(**info)


@pytest.fixture
async def scratch() -> AsyncGenerator[str]:
    """A scratch database with the baseline applied; yields its DSN. Needs no ledger code."""
    async with fresh_database("arno_baseline") as dsn:
        async with cluster_lock(), connect(dsn) as conn:
            await run_migrations(conn)
            await set_agent_ro_password(conn, os.environ["AGENT_RO_PASSWORD"])
            await set_backup_password(conn, os.environ["BACKUP_PASSWORD"])
        yield dsn


@pytest.fixture
async def db(scratch: str) -> AsyncGenerator[Connection]:
    async with connect(scratch) as conn:
        yield conn


async def _two_accounts(db: Connection) -> None:
    await db.execute(
        "INSERT INTO accounts (id, kind, name, liquid) VALUES "
        "('acc_a', 'bank', 'Bank A', true), ('acc_b', 'bank', 'Bank B', true), "
        "('acc_sp', 'spending', 'Spending', false)"
    )


# The baseline ---------------------------------------------------------------------------------


@pytest.mark.xdist_group("cluster")
async def test_fresh_database_applies_the_baseline_once():
    async with fresh_database("arno_baseline") as dsn, cluster_lock(), connect(dsn) as conn:
        assert await run_migrations(conn) == [1]
        assert await run_migrations(conn) == []
        assert [(r["version"], r["name"]) for r in await applied(conn)] == [(1, "baseline")]
        assert await pending(conn) == []


async def test_the_migrated_database_is_at_the_baseline(db):
    assert [m.version for m in discover()] == [1]
    assert await current_version(db) == 1
    assert await run_migrations(db) == []


async def test_a_changed_migration_is_refused(db, tmp_path: Path):
    changed = tmp_path / "0001_baseline.sql"
    changed.write_text(discover()[0].sql + "\n-- edited\n", encoding="utf-8")

    with pytest.raises(MigrationError, match="changed after it was applied"):
        await run_migrations(db, tmp_path)


async def test_the_schema_has_exactly_the_designed_tables_and_views(db):
    tables = {
        r["table_name"]
        for r in await db.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
    }
    assert tables - {"schema_version"} == EXPECTED_TABLES

    for view, columns in VIEW_COLUMNS.items():
        got = await db.fetchval(
            "SELECT string_agg(column_name, ' ' ORDER BY ordinal_position) "
            "FROM information_schema.columns WHERE table_schema = 'public' AND table_name = $1",
            view,
        )
        assert got == columns, view
    views = {
        r["table_name"]
        for r in await db.fetch("SELECT table_name FROM information_schema.views")
        if r["table_name"].startswith("v_")
    }
    assert views == set(VIEW_COLUMNS)


# A database from before the squash moves to a fresh one --------------------------------------


@pytest.mark.xdist_group("cluster")
async def test_a_database_with_the_old_version_1_is_refused():
    async with fresh_database("arno_legacy") as dsn, connect(dsn) as conn:
        await conn.execute(
            "CREATE TABLE schema_version (version integer PRIMARY KEY, name text NOT NULL, "
            "checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now());"
            "INSERT INTO schema_version (version, name, checksum) VALUES (1, 'pgledger', 'x')"
        )
        with pytest.raises(MigrationError, match="pre-baseline schema"):
            await run_migrations(conn)
        with pytest.raises(MigrationError, match="legacy-upgrades.md"):
            await pending(conn)


@pytest.mark.xdist_group("cluster")
async def test_a_database_with_a_pgledger_table_is_refused():
    async with fresh_database("arno_legacy") as dsn, connect(dsn) as conn:
        await conn.execute("CREATE TABLE pgledger_accounts (id text PRIMARY KEY)")
        with pytest.raises(MigrationError, match="pre-baseline schema"):
            await run_migrations(conn)


# Roles ----------------------------------------------------------------------------------------


async def test_the_boundary_roles_are_unprivileged(db):
    rows = await db.fetch(
        "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
        "FROM pg_roles WHERE rolname IN ('agent_ro', 'backup')"
    )
    assert {r["rolname"] for r in rows} == {"agent_ro", "backup"}
    for row in rows:
        assert not any(v for k, v in row.items() if k != "rolname"), row["rolname"]


async def test_agent_ro_reads_the_views_and_listed_tables_only(scratch):
    async with connect(_role_dsn(scratch, "agent_ro", "AGENT_RO_PASSWORD")) as conn:
        assert (await conn.fetchrow_required("SELECT current_user AS who"))["who"] == "agent_ro"
        for relation in sorted(AGENT_RO_READABLE):
            await conn.fetch(f"SELECT * FROM {relation} LIMIT 1")
        for relation in ("undo_log", "accounts", "movement_legs", "attachments", "notifications"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetch(f"SELECT * FROM {relation} LIMIT 1")


async def test_agent_ro_cannot_write_or_create_temp_tables(scratch):
    async with connect(_role_dsn(scratch, "agent_ro", "AGENT_RO_PASSWORD")) as conn:
        for sql in (
            "INSERT INTO settings (id) VALUES (2)",
            "UPDATE settings SET display_name = 'x'",
            "DELETE FROM categories",
            "CREATE TEMP TABLE scratch (id int)",
            "CREATE TABLE scratch (id int)",
        ):
            with pytest.raises((asyncpg.PostgresError, asyncpg.InterfaceError)):
                await conn.execute(sql)


async def test_agent_ro_is_read_only_with_a_two_second_timeout(scratch):
    async with connect(_role_dsn(scratch, "agent_ro", "AGENT_RO_PASSWORD")) as conn:
        assert await conn.fetchval("SHOW default_transaction_read_only") == "on"
        assert await conn.fetchval("SHOW statement_timeout") == "2s"
        with pytest.raises(asyncpg.QueryCanceledError):
            await conn.execute("SELECT pg_sleep(3)")


async def test_backup_reads_everything_and_writes_only_backup_runs(scratch):
    async with connect(_role_dsn(scratch, "backup", "BACKUP_PASSWORD")) as conn:
        for relation in ("undo_log", "accounts", "attachments", "schema_version", "v_accounts"):
            await conn.fetch(f"SELECT * FROM {relation} LIMIT 1")
        await conn.execute("INSERT INTO backup_runs (started_at, status) VALUES (now(), 'success')")
        for sql in ("UPDATE backup_runs SET status = 'failed'", "DELETE FROM backup_runs"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(sql)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("INSERT INTO settings (id) VALUES (2)")


# Ids and the ledger shape ---------------------------------------------------------------------


async def test_new_id_is_prefixed_time_sortable_and_matches_the_citation_pattern(db):
    ids = [await db.fetchval("SELECT new_id('mov')") for _ in range(3)]

    assert all(re.fullmatch(r"mov_[0-9a-f]{32}", i) for i in ids)
    assert ids == sorted(ids)
    assert len(set(ids)) == 3


async def test_a_leg_moves_a_positive_amount_between_two_different_accounts(db):
    await _two_accounts(db)
    await db.execute(
        "INSERT INTO movements (id, date, description, kind) "
        "VALUES ('mov_x', DATE '2026-01-01', 'Test', 'transfer')"
    )
    insert = (
        "INSERT INTO movement_legs (movement_id, from_account_id, to_account_id, amount_paise) "
        "VALUES ('mov_x', $1, $2, $3)"
    )

    for args in (("acc_a", "acc_b", 0), ("acc_a", "acc_b", -5), ("acc_a", "acc_a", 5)):
        with pytest.raises(asyncpg.CheckViolationError):
            await db.execute(insert, *args)
    await db.execute(insert, "acc_a", "acc_b", 5)


async def test_references_are_checked_at_commit_so_restore_order_is_free(db):
    await _two_accounts(db)
    async with db.transaction():
        # The leg precedes its movement; the foreign key is checked when the transaction ends.
        await db.execute(
            "INSERT INTO movement_legs (movement_id, from_account_id, to_account_id, amount_paise) "
            "VALUES ('mov_late', 'acc_a', 'acc_b', 5)"
        )
        await db.execute(
            "INSERT INTO movements (id, date, description, kind) "
            "VALUES ('mov_late', DATE '2026-01-01', 'Late', 'transfer')"
        )
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.execute(
            "INSERT INTO movement_legs (movement_id, from_account_id, to_account_id, "
            "amount_paise) VALUES ('mov_none', 'acc_a', 'acc_b', 5)"
        )


async def test_views_compute_balances_spending_and_legs_from_the_legs(db):
    await _two_accounts(db)
    await db.execute(
        "INSERT INTO movements (id, date, description, kind) VALUES "
        "('mov_in', DATE '2026-01-01', 'Salary', 'income'), "
        "('mov_out', DATE '2026-01-02', 'Lunch', 'expense')"
    )
    await db.execute("INSERT INTO accounts (id, kind, name) VALUES ('acc_inc', 'income', 'Income')")
    await db.execute(
        "INSERT INTO movement_legs (movement_id, from_account_id, to_account_id, amount_paise) "
        "VALUES ('mov_in', 'acc_inc', 'acc_a', 10000), ('mov_out', 'acc_a', 'acc_sp', 2500)"
    )

    balances = {
        r["id"]: r["balance_paise"]
        for r in await db.fetch("SELECT id, balance_paise FROM v_accounts")
    }
    assert balances == {"acc_a": 7500, "acc_b": 0, "acc_sp": 2500, "acc_inc": -10000}
    assert await db.fetchval("SELECT sum(balance_paise) FROM v_accounts") == 0

    running = await db.fetch(
        "SELECT as_of, balance_paise FROM v_balances WHERE account_id = 'acc_a' ORDER BY as_of"
    )
    assert [r["balance_paise"] for r in running] == [10000, 7500]
    assert await db.fetchval("SELECT sum(amount_paise) FROM v_spending") == 2500
    leg = await db.fetchrow_required(
        "SELECT from_account_name, to_account_name, movement_kind FROM v_legs "
        "WHERE movement_id = 'mov_out'"
    )
    assert (leg["from_account_name"], leg["to_account_name"], leg["movement_kind"]) == (
        "Bank A",
        "Spending",
        "expense",
    )


async def test_undo_log_rejects_a_snapshot_that_is_not_an_array(db):
    insert = "INSERT INTO undo_log (actor, summary, snapshot) VALUES ('user', 'x', $1::jsonb)"

    await db.execute(insert, [])
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(insert, {"table": "movements"})
