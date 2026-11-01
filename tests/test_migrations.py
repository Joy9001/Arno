from __future__ import annotations

import os

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

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
}

EXPECTED_VIEWS = {"v_spending", "v_cashflow", "v_balances", "v_owed"}


def _agent_ro_dsn() -> str:
    info = conninfo_to_dict(os.environ["DATABASE_URL"])
    info["user"] = "agent_ro"
    info["password"] = os.environ["AGENT_RO_PASSWORD"]
    return make_conninfo(**info)


def test_every_migration_is_recorded_once(db):
    rows = applied(db)
    versions = [row["version"] for row in rows]

    assert versions == list(range(1, len(discover()) + 1))
    assert current_version(db) == len(discover())
    assert rows[0]["name"] == "pgledger"
    assert [row["name"] for row in rows][-3:] == ["views", "grants", "ledger_l1"]


def test_migrations_are_idempotent(db):
    assert run_migrations(db) == []
    assert current_version(db) == len(discover())


def test_schema_v1_has_every_designed_table_and_view(db):
    tables = {
        row["table_name"]
        for row in db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
    }
    views = {
        row["table_name"]
        for row in db.execute(
            "SELECT table_name FROM information_schema.views WHERE table_schema = 'public'"
        ).fetchall()
    }

    assert EXPECTED_TABLES <= tables
    assert EXPECTED_VIEWS <= views
    assert {
        "pgledger_accounts_view",
        "pgledger_transfers_view",
        "pgledger_entries_view",
        "pgledger_idempotency_keys_view",
    } <= views


def test_money_columns_are_whole_paise_bigints(db):
    columns = db.execute(
        """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name LIKE '%_paise'
        """
    ).fetchall()

    assert columns, "expected *_paise columns"
    assert {row["data_type"] for row in columns} == {"bigint"}


def test_pgledger_ids_are_prefixed_ulids(db):
    row = db.execute("SELECT pgledger_generate_id('acc') AS id").fetchone()

    assert row["id"].startswith("acc_")
    assert len(row["id"].split("_", 1)[1]) == 26


def test_agent_ro_can_read_the_ledger_but_not_auth_tables():
    with psycopg.connect(_agent_ro_dsn(), row_factory=dict_row) as conn:
        assert conn.execute("SELECT current_user AS who").fetchone()["who"] == "agent_ro"
        assert conn.execute("SELECT count(*) AS n FROM accounts").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM v_balances").fetchone()["n"] == 0

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT count(*) FROM users")
        conn.rollback()


def test_agent_ro_cannot_write_or_create_temp_tables():
    with psycopg.connect(_agent_ro_dsn(), row_factory=dict_row) as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("INSERT INTO categories (name) VALUES ('x')")
        conn.rollback()

        temp_allowed = conn.execute(
            "SELECT has_database_privilege(current_user, current_database(), 'TEMP') AS allowed"
        ).fetchone()["allowed"]
        assert temp_allowed is False
        with pytest.raises(psycopg.errors.Error):
            conn.execute("CREATE TEMP TABLE scratch (id int)")
        conn.rollback()


def test_agent_ro_has_read_only_and_two_second_defaults():
    with psycopg.connect(_agent_ro_dsn(), row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT current_setting('default_transaction_read_only') AS ro, "
            "current_setting('statement_timeout') AS timeout"
        ).fetchone()

    assert row["ro"] == "on"
    assert row["timeout"] == "2s"
