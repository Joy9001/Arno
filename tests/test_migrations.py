from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from arno.db.conn import connect, conninfo_to_dict, make_conninfo
from arno.db.migrate import applied, current_version, discover, run_migrations

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
    "movement_legs",
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

EXPECTED_VIEWS = {
    "v_accounts",
    "v_legs",
    "v_movement_state",
    "v_spending",
    "v_balances",
    "v_owed",
}


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
    assert {
        "views",
        "grants",
        "ledger_l1",
        "enrollment_tokens",
        "measured_indexes",
        "schema_integrity",
        "movement_legs",
        "movement_reversal_state",
        "account_identity",
        "agent_read_model",
        "reference_integrity",
    } <= set(names)


async def test_migrations_are_idempotent(db):
    assert await run_migrations(db) == []
    assert await current_version(db) == len(discover())


async def test_measured_indexes_match_query_expressions(db):
    async with db.transaction():
        await db.execute("SET LOCAL enable_seqscan = off")
        audit_plan = await db.fetchval(
            "EXPLAIN (FORMAT JSON) SELECT count(*) AS n FROM audit_log WHERE created_at > $1",
            datetime(2026, 1, 1, tzinfo=UTC),
        )

    audit_text = audit_plan if isinstance(audit_plan, str) else json.dumps(audit_plan)
    assert "audit_log_created_at_idx" in audit_text


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
    assert "v_cashflow" not in views
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
        assert (await conn.fetchrow_required("SELECT current_user AS who"))["who"] == "agent_ro"
        for readable in (
            "v_accounts",
            "v_legs",
            "v_movement_state",
            "v_spending",
            "v_balances",
            "v_owed",
            "schedule_occurrences",
            "schedule_payments",
        ):
            assert await conn.fetchval(f"SELECT count(*) FROM {readable}") == 0

        for forbidden in (
            "users",
            "accounts",
            "movement_legs",
            "pgledger_accounts_view",
            "pgledger_transfers_view",
            "pgledger_entries_view",
            "pgledger_idempotency_keys_view",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(f"SELECT count(*) FROM {forbidden}")


async def test_agent_ro_cannot_write_or_create_temp_tables():
    async with connect(_agent_ro_dsn()) as conn:
        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await conn.execute("INSERT INTO categories (name) VALUES ('x')")

        temp_allowed = (
            await conn.fetchrow_required(
                "SELECT has_database_privilege(current_user, current_database(), 'TEMP') AS allowed"
            )
        )["allowed"]
        assert temp_allowed is False
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("CREATE TEMP TABLE scratch (id int)")


async def test_agent_ro_has_read_only_and_two_second_defaults():
    async with connect(_agent_ro_dsn()) as conn:
        row = await conn.fetchrow_required(
            "SELECT current_setting('default_transaction_read_only') AS ro, "
            "current_setting('statement_timeout') AS timeout"
        )

    assert row["ro"] == "on"
    assert row["timeout"] == "2s"


async def test_schema_integrity_rejects_fractional_transfer_amount(db):
    from decimal import Decimal

    async with db.transaction():
        a1 = await db.fetchval(
            "SELECT id FROM pgledger_create_account('Frac From', 'INR', true, true, NULL)"
        )
        a2 = await db.fetchval(
            "SELECT id FROM pgledger_create_account('Frac To', 'INR', true, true, NULL)"
        )
        with pytest.raises(asyncpg.CheckViolationError) as exc:
            await db.execute(
                "SELECT * FROM pgledger_create_transfer($1, $2, $3, NULL, NULL)",
                a1,
                a2,
                Decimal("10.50"),
            )
    assert getattr(exc.value, "constraint_name", None) == "pgledger_transfers_amount_integer_check"


async def test_schema_integrity_rejects_reversal_polarity_violation(db):
    async with db.transaction():
        mov_id = await db.fetchval(
            "INSERT INTO movements (date, description, kind) "
            "VALUES ('2026-01-01', 'Coffee', 'expense') RETURNING id"
        )
        with pytest.raises(asyncpg.CheckViolationError) as exc:
            await db.execute(
                "INSERT INTO movements (date, description, kind, reverses_id) "
                "VALUES ('2026-01-01', 'Not reversal', 'expense', $1)",
                mov_id,
            )
    assert getattr(exc.value, "constraint_name", None) == "movements_reversal_pair_check"


async def test_schema_integrity_rejects_self_reversal_and_function_handles_cycle(db):
    with pytest.raises(asyncpg.CheckViolationError) as exc:
        async with db.transaction():
            await db.execute(
                "INSERT INTO movements (id, date, description, kind, reverses_id) "
                "VALUES ('mov_self', '2026-01-01', 'Self', 'reversal', 'mov_self')"
            )
    assert getattr(exc.value, "constraint_name", None) is not None

    tx = db.transaction()
    await tx.start()
    try:
        m0 = await db.fetchval(
            "INSERT INTO movements (date, description, kind) "
            "VALUES ('2026-01-01', 'Seed', 'expense') RETURNING id"
        )
        r1 = await db.fetchval(
            "INSERT INTO movements (date, description, kind, reverses_id) "
            "VALUES ('2026-01-02', 'R1', 'reversal', $1) RETURNING id",
            m0,
        )
        r2 = await db.fetchval(
            "INSERT INTO movements (date, description, kind, reverses_id) "
            "VALUES ('2026-01-03', 'R2', 'reversal', $1) RETURNING id",
            r1,
        )
        await db.execute("UPDATE movements SET reverses_id = $1 WHERE id = $2", r2, r1)
        rows = await db.fetch("SELECT movement_id, is_reversed FROM movement_reversal_state()")
        assert {row["movement_id"] for row in rows} >= {r1, r2}
    finally:
        await tx.rollback()


async def test_schema_integrity_rejects_case_variant_category(db):
    async with db.transaction():
        await db.execute("INSERT INTO categories (name) VALUES ('Food')")
        with pytest.raises(asyncpg.UniqueViolationError) as exc:
            await db.execute("INSERT INTO categories (name) VALUES ('food')")
    assert getattr(exc.value, "constraint_name", None) == "categories_lower_name_idx"


async def test_payee_rename_updates_views_without_touching_accounts(db):
    from arno.ledger.accounts import create_account
    from arno.ledger.movements import record_lending
    from arno.ledger.payees import create_payee, update_payee

    acc = await create_account(db, kind="bank", name="HDFC Bank", opening_balance_paise=10_000)
    payee = await create_payee(db, display_name="Ravi Old", kind="person")
    await record_lending(
        db, account_id=acc["id"], payee_id=payee["id"], amount_paise=5_000, direction="i_lent"
    )

    person_acc_id = payee["person_account_id"]
    before_acc = await db.fetchrow_required(
        "SELECT name, updated_at FROM accounts WHERE id = $1", person_acc_id
    )
    assert before_acc["name"] is None
    acc_audits_before = await db.fetchval(
        "SELECT count(*) FROM audit_log WHERE table_name = 'accounts' AND row_id = $1",
        person_acc_id,
    )

    await update_payee(db, payee["id"], display_name="Ravi Renamed")

    after_acc = await db.fetchrow_required(
        "SELECT name, updated_at FROM accounts WHERE id = $1", person_acc_id
    )
    assert after_acc["name"] is None
    assert after_acc["updated_at"] == before_acc["updated_at"]
    acc_audits_after = await db.fetchval(
        "SELECT count(*) FROM audit_log WHERE table_name = 'accounts' AND row_id = $1",
        person_acc_id,
    )
    assert acc_audits_after == acc_audits_before

    async with connect(_agent_ro_dsn()) as ro:
        assert (
            await ro.fetchval("SELECT name FROM v_accounts WHERE id = $1", person_acc_id)
            == "Ravi Renamed"
        )
        assert (
            await ro.fetchval(
                "SELECT DISTINCT name FROM v_balances WHERE account_id = $1", person_acc_id
            )
            == "Ravi Renamed"
        )
        assert (
            await ro.fetchval("SELECT display_name FROM v_owed WHERE payee_id = $1", payee["id"])
            == "Ravi Renamed"
        )


async def test_goal_rename_updates_goal_account_view(db):
    from arno.ledger.accounts import create_account, update_account
    from arno.ledger.audit import undo_group
    from arno.ledger.goals import create_goal, update_goal
    from arno.ledger.movements import record_goal_contribution

    bank = await create_account(db, kind="bank", name="Savings Bank", opening_balance_paise=50_000)
    goal = await create_goal(db, name="Emergency Fund", target_paise=100_000)
    await record_goal_contribution(
        db, account_id=bank["id"], goal_account_id=goal["account_id"], amount_paise=10_000
    )

    acc_audits_before = await db.fetchval(
        "SELECT count(*) FROM audit_log WHERE table_name = 'accounts' AND row_id = $1",
        goal["account_id"],
    )
    await update_goal(db, goal["id"], name="Rainy Day")

    assert await db.fetchval("SELECT name FROM accounts WHERE id = $1", goal["account_id"]) is None
    assert (
        await db.fetchval(
            "SELECT count(*) FROM audit_log WHERE table_name = 'accounts' AND row_id = $1",
            goal["account_id"],
        )
        == acc_audits_before
    )
    async with connect(_agent_ro_dsn()) as ro:
        assert (
            await ro.fetchval("SELECT name FROM v_accounts WHERE id = $1", goal["account_id"])
            == "Rainy Day goal"
        )
        assert (
            await ro.fetchval(
                "SELECT DISTINCT name FROM v_balances WHERE account_id = $1", goal["account_id"]
            )
            == "Rainy Day goal"
        )

    renamed = await update_account(db, goal["account_id"], name="Japan Trip goal")
    assert await db.fetchval("SELECT name FROM goals WHERE id = $1", goal["id"]) == "Japan Trip"
    assert (
        await db.fetchval("SELECT name FROM v_accounts WHERE id = $1", goal["account_id"])
        == "Japan Trip goal"
    )
    await undo_group(db, renamed["undo_id"])
    assert await db.fetchval("SELECT name FROM goals WHERE id = $1", goal["id"]) == "Rainy Day"
    assert (
        await db.fetchval("SELECT name FROM v_accounts WHERE id = $1", goal["account_id"])
        == "Rainy Day goal"
    )


async def test_person_account_rename_updates_payee_and_undoes(db):
    from arno.ledger.accounts import update_account
    from arno.ledger.audit import undo_group
    from arno.ledger.payees import create_payee

    payee = await create_payee(db, display_name="Meera Old", kind="person")
    person_acc_id = payee["person_account_id"]

    renamed = await update_account(db, person_acc_id, name="Meera New")
    assert renamed["name"] == "Meera New"
    assert (
        await db.fetchval("SELECT display_name FROM payees WHERE id = $1", payee["id"])
        == "Meera New"
    )
    assert (
        await db.fetchval("SELECT name FROM v_accounts WHERE id = $1", person_acc_id) == "Meera New"
    )
    audit_tables = [
        row["table_name"]
        for row in await db.fetch(
            "SELECT table_name FROM audit_log WHERE group_id = $1", renamed["undo_id"]
        )
    ]
    assert audit_tables == ["payees"]

    await undo_group(db, renamed["undo_id"])
    assert (
        await db.fetchval("SELECT display_name FROM payees WHERE id = $1", payee["id"])
        == "Meera Old"
    )
    assert (
        await db.fetchval("SELECT name FROM v_accounts WHERE id = $1", person_acc_id) == "Meera Old"
    )


async def test_account_name_kind_check_enforced(db):
    async with db.transaction():
        pg_person = await db.fetchval(
            "SELECT id FROM pgledger_create_account('acc_check_p', 'INR', true, true, NULL)"
        )
        with pytest.raises(asyncpg.CheckViolationError) as exc_person:
            await db.execute(
                "INSERT INTO accounts (id, kind, ledger_account_id, name, liquid) "
                "VALUES ('acc_check_p', 'person', $1, 'Forbidden', false)",
                pg_person,
            )
    assert getattr(exc_person.value, "constraint_name", None) == "accounts_name_kind_check"

    async with db.transaction():
        pg_bank = await db.fetchval(
            "SELECT id FROM pgledger_create_account('acc_check_b', 'INR', true, true, NULL)"
        )
        with pytest.raises(asyncpg.CheckViolationError) as exc_bank:
            await db.execute(
                "INSERT INTO accounts (id, kind, ledger_account_id, name, liquid) "
                "VALUES ('acc_check_b', 'bank', $1, NULL, true)",
                pg_bank,
            )
    assert getattr(exc_bank.value, "constraint_name", None) == "accounts_name_kind_check"


async def test_subtype_foreign_keys_and_single_user_guard(db):
    cols = {
        (row["table_name"], row["column_name"])
        for row in await db.fetch(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name IN ('goals', 'payees')
            """
        )
    }
    assert ("goals", "account_kind") not in cols
    assert ("payees", "person_account_kind") not in cols

    constraints = {
        row["conname"]
        for row in await db.fetch(
            """
            SELECT c.conname
            FROM pg_constraint c
            JOIN pg_namespace n ON n.oid = c.connamespace
            WHERE n.nspname = 'public'
              AND c.conrelid IN ('accounts'::regclass, 'goals'::regclass, 'payees'::regclass)
            """
        )
    }
    assert "accounts_id_kind_key" not in constraints
    assert "goals_account_id_fkey" in constraints
    assert "payees_person_account_id_fkey" in constraints

    async with db.transaction():
        with pytest.raises(asyncpg.ForeignKeyViolationError) as exc_goal:
            await db.execute(
                "INSERT INTO goals (name, target_paise, account_id) "
                "VALUES ('Bad Goal', 10000, 'acc_missing_00000000000000')"
            )
    assert getattr(exc_goal.value, "constraint_name", None) == "goals_account_id_fkey"

    async with db.transaction():
        with pytest.raises(asyncpg.ForeignKeyViolationError) as exc_payee:
            await db.execute(
                "INSERT INTO payees (display_name, kind, person_account_id) "
                "VALUES ('Bad Person', 'person', 'acc_missing_00000000000000')"
            )
    assert getattr(exc_payee.value, "constraint_name", None) == "payees_person_account_id_fkey"

    async with db.transaction():
        await db.execute("INSERT INTO users (webauthn_user_handle) VALUES (decode('01', 'hex'))")
        with pytest.raises(asyncpg.UniqueViolationError) as exc_user:
            await db.execute(
                "INSERT INTO users (webauthn_user_handle) VALUES (decode('02', 'hex'))"
            )
    assert getattr(exc_user.value, "constraint_name", None) == "users_one_row_idx"


async def test_pgledger_accounts_name_equals_account_id_for_every_kind(db):
    from arno.ledger.accounts import create_account, ensure_system_accounts, update_account
    from arno.ledger.goals import create_goal, update_goal
    from arno.ledger.payees import create_payee, update_payee

    await ensure_system_accounts(db)
    bank = await create_account(db, kind="bank", name="All Kinds Bank")
    await create_account(
        db,
        kind="card",
        name="All Kinds Card",
        credit_limit_paise=100_000,
        statement_day=5,
        due_day=25,
    )
    await create_account(db, kind="cash", name="All Kinds Cash")
    await create_account(db, kind="wallet", name="All Kinds Wallet")
    await create_account(db, kind="loan", name="All Kinds Loan")
    await create_account(db, kind="asset", name="All Kinds Asset")
    goal = await create_goal(db, name="All Kinds Goal", target_paise=100_000)
    payee = await create_payee(db, display_name="All Kinds Person", kind="person")

    await update_account(db, bank["id"], name="All Kinds Bank Renamed")
    await update_goal(db, goal["id"], name="All Kinds Goal Renamed")
    await update_payee(db, payee["id"], display_name="All Kinds Person Renamed")

    rows = await db.fetch(
        """
        SELECT a.id, a.kind, pg.name AS pg_name, pgv.name AS pg_view_name
        FROM accounts a
        JOIN pgledger_accounts pg ON pg.id = a.ledger_account_id
        JOIN pgledger_accounts_view pgv ON pgv.id = a.ledger_account_id
        ORDER BY a.id
        """
    )
    assert len(rows) == 11
    assert {row["kind"] for row in rows} == {
        "bank",
        "card",
        "cash",
        "wallet",
        "loan",
        "asset",
        "goal",
        "person",
        "spending",
        "income",
        "equity",
    }
    for row in rows:
        assert row["pg_name"] == row["id"]
        assert row["pg_view_name"] == row["id"]


async def test_movement_legs_rejects_legless_transfer_on_commit(db):
    with pytest.raises(asyncpg.ForeignKeyViolationError) as exc:
        async with db.transaction():
            a1 = await db.fetchval(
                "SELECT id FROM pgledger_create_account('Legless From', 'INR', true, true, NULL)"
            )
            a2 = await db.fetchval(
                "SELECT id FROM pgledger_create_account('Legless To', 'INR', true, true, NULL)"
            )
            await db.execute(
                "SELECT * FROM pgledger_create_transfer($1, $2, 100, NULL, NULL)",
                a1,
                a2,
            )
    assert getattr(exc.value, "constraint_name", None) == "pgledger_transfers_leg_fkey"


async def test_movement_legs_rejects_deleting_leg(db):
    from arno.ledger.accounts import create_account, ensure_system_accounts
    from arno.ledger.movements import record_expense

    await ensure_system_accounts(db)
    bank = await create_account(db, kind="bank", name="Delete Leg Bank")
    exp = await record_expense(db, account_id=bank["id"], amount_paise=5_000, description="Tea")
    transfer_id = await db.fetchval(
        "SELECT transfer_id FROM movement_legs WHERE movement_id = $1", exp["id"]
    )
    assert transfer_id is not None

    with pytest.raises(asyncpg.ForeignKeyViolationError) as exc:
        async with db.transaction():
            await db.execute("DELETE FROM movement_legs WHERE transfer_id = $1", transfer_id)
    assert getattr(exc.value, "constraint_name", None) == "pgledger_transfers_leg_fkey"
    still_there = await db.fetchval(
        "SELECT 1 FROM movement_legs WHERE transfer_id = $1", transfer_id
    )
    assert still_there == 1


async def test_label_set_change_and_undo_keeps_leg(db):
    from arno.ledger.accounts import create_account, ensure_system_accounts
    from arno.ledger.audit import undo_group
    from arno.ledger.categories import ensure_default_categories
    from arno.ledger.movements import correct_movement, record_expense

    await ensure_system_accounts(db)
    await ensure_default_categories(db)
    bank = await create_account(db, kind="bank", name="Label Undo Bank")
    food = await db.fetchval("SELECT id FROM categories WHERE name = 'Food & Dining'")
    groceries = await db.fetchval("SELECT id FROM categories WHERE name = 'Groceries'")
    exp = await record_expense(db, account_id=bank["id"], amount_paise=12_000, description="Snack")

    first = await correct_movement(db, exp["id"], changes={"category_id": food})
    leg = await db.fetchrow_required(
        "SELECT transfer_id, category_id FROM movement_legs WHERE movement_id = $1", exp["id"]
    )
    assert leg["category_id"] == food

    await undo_group(db, first["undo_id"])
    leg_after_undo = await db.fetchrow_required(
        "SELECT transfer_id, category_id FROM movement_legs WHERE transfer_id = $1",
        leg["transfer_id"],
    )
    assert leg_after_undo["category_id"] is None

    await correct_movement(db, exp["id"], changes={"category_id": food})
    second = await correct_movement(db, exp["id"], changes={"category_id": groceries})
    assert (
        await db.fetchval(
            "SELECT category_id FROM movement_legs WHERE transfer_id = $1", leg["transfer_id"]
        )
        == groceries
    )
    await undo_group(db, second["undo_id"])
    assert (
        await db.fetchval(
            "SELECT category_id FROM movement_legs WHERE transfer_id = $1", leg["transfer_id"]
        )
        == food
    )


async def test_undo_of_pre_migration_label_journal_works(db):
    from pathlib import Path

    from arno.ledger.accounts import create_account, ensure_system_accounts
    from arno.ledger.audit import jsonable, new_id, undo_group
    from arno.ledger.categories import ensure_default_categories
    from arno.ledger.movements import record_expense

    await ensure_system_accounts(db)
    await ensure_default_categories(db)
    bank = await create_account(db, kind="bank", name="Pre-0033 Bank")
    food = await db.fetchval("SELECT id FROM categories WHERE name = 'Food & Dining'")
    groceries = await db.fetchval("SELECT id FROM categories WHERE name = 'Groceries'")
    exp = await record_expense(db, account_id=bank["id"], amount_paise=8_000, description="Coffee")
    leg = dict(
        await db.fetchrow_required("SELECT * FROM movement_legs WHERE movement_id = $1", exp["id"])
    )

    migration_sql = Path("src/arno/db/migrations/0033_movement_legs.sql").read_text(
        encoding="utf-8"
    )
    audit_stmt = next(
        stmt
        for stmt in migration_sql.split(";")
        if "UPDATE audit_log a" in stmt and "WHERE a.table_name = 'transfer_labels'" in stmt
    )

    # 1. Pre-migration standalone label creation (before=NULL, no movements creation in group)
    g_create = await new_id(db, "audg")
    food_row = {**leg, "category_id": food}
    await db.execute(
        "UPDATE movement_legs SET category_id = $1 WHERE transfer_id = $2",
        food,
        leg["transfer_id"],
    )
    await db.execute(
        """
        INSERT INTO audit_log (id, group_id, actor, table_name, row_id, before, after)
        VALUES ($1, $2, 'user', 'transfer_labels', $3, NULL, $4::jsonb)
        """,
        await new_id(db, "aud"),
        g_create,
        leg["transfer_id"],
        json.dumps(jsonable(food_row)),
    )
    await db.execute(audit_stmt)
    await undo_group(db, g_create)
    assert (
        await db.fetchval(
            "SELECT category_id FROM movement_legs WHERE transfer_id = $1", leg["transfer_id"]
        )
        is None
    )

    # 2. Pre-migration label update
    g_update = await new_id(db, "audg")
    groceries_row = {**leg, "category_id": groceries}
    await db.execute(
        "UPDATE movement_legs SET category_id = $1 WHERE transfer_id = $2",
        groceries,
        leg["transfer_id"],
    )
    await db.execute(
        """
        INSERT INTO audit_log (id, group_id, actor, table_name, row_id, before, after)
        VALUES ($1, $2, 'user', 'transfer_labels', $3, $4::jsonb, $5::jsonb)
        """,
        await new_id(db, "aud"),
        g_update,
        leg["transfer_id"],
        json.dumps(jsonable(food_row)),
        json.dumps(jsonable(groceries_row)),
    )
    await db.execute(audit_stmt)
    await undo_group(db, g_update)
    assert (
        await db.fetchval(
            "SELECT category_id FROM movement_legs WHERE transfer_id = $1", leg["transfer_id"]
        )
        == food
    )

    # 3. Pre-migration label deletion (before=row, after=NULL)
    g_delete = await new_id(db, "audg")
    await db.execute(
        "UPDATE movement_legs SET category_id = NULL WHERE transfer_id = $1",
        leg["transfer_id"],
    )
    await db.execute(
        """
        INSERT INTO audit_log (id, group_id, actor, table_name, row_id, before, after)
        VALUES ($1, $2, 'user', 'transfer_labels', $3, $4::jsonb, NULL)
        """,
        await new_id(db, "aud"),
        g_delete,
        leg["transfer_id"],
        json.dumps(jsonable(food_row)),
    )
    await db.execute(audit_stmt)
    await undo_group(db, g_delete)
    assert (
        await db.fetchval(
            "SELECT category_id FROM movement_legs WHERE transfer_id = $1", leg["transfer_id"]
        )
        == food
    )


async def test_run_query_description_matches_agent_ro_catalog(db):
    from arno.agent.toolset import run_query

    doc = run_query.__doc__
    assert doc is not None
    described = re.findall(r"^\s*-\s+([a-z_][a-z0-9_]*):", doc, flags=re.MULTILINE)
    assert len(described) == len(set(described)), "duplicate object in run_query description"

    granted_rows = await db.fetch(
        """
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND has_table_privilege('agent_ro', c.oid, 'SELECT')
        ORDER BY c.relname
        """
    )
    granted = {str(row["relname"]) for row in granted_rows}
    assert set(described) == granted

    public_rows = await db.fetch(
        """
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
        """
    )
    public_names = {str(row["relname"]) for row in public_rows}
    tokens = set(re.findall(r"\b[a-z_][a-z0-9_]*\b", doc))
    ungranted_mentions = (tokens & public_names) - granted
    assert not ungranted_mentions, (
        f"run_query description names ungranted catalog object(s): {sorted(ungranted_mentions)}"
    )


async def test_v_legs_uses_account_ids_and_positive_paise(db):
    from arno.ledger.accounts import create_account, ensure_system_accounts
    from arno.ledger.audit import write_group
    from arno.ledger.categories import ensure_default_categories
    from arno.ledger.movements import record_expense, record_split, reverse_movement
    from arno.ledger.payees import create_payee

    await ensure_system_accounts(db)
    await ensure_default_categories(db)
    bank = await create_account(db, kind="bank", name="Leg View Bank")
    food = await db.fetchval("SELECT id FROM categories WHERE name = 'Food & Dining'")
    friend = await create_payee(db, display_name="Neha", kind="person")
    merchant = await create_payee(db, display_name="Cafe", kind="merchant")

    exp = await record_expense(
        db,
        account_id=bank["id"],
        amount_paise=15_000,
        description="Lunch",
        category_id=food,
        payee_id=merchant["id"],
    )
    async with write_group(db, actor="user") as group:
        rev = await reverse_movement(group, exp["id"], note="wrong entry")
    split = await record_split(
        db,
        account_id=bank["id"],
        total_paise=20_000,
        my_share_paise=10_000,
        people=[{"payee_id": friend["id"], "amount_paise": 10_000}],
        description="Dinner",
        category_id=food,
    )

    cols = [
        row["column_name"]
        for row in await db.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'v_legs'
            ORDER BY ordinal_position
            """
        )
    ]
    assert cols[cols.index("from_account_kind") + 1] == "from_account_liquid"
    assert cols[cols.index("to_account_kind") + 1] == "to_account_liquid"

    col_type = await db.fetchval(
        """
        SELECT data_type FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'v_legs' AND column_name = 'amount_paise'
        """
    )
    assert col_type == "bigint"

    async with connect(_agent_ro_dsn()) as ro:
        rows = await ro.fetch("SELECT * FROM v_legs ORDER BY movement_id, transfer_id")
    assert len(rows) == 4
    for row in rows:
        assert str(row["from_account_id"]).startswith("acc_")
        assert str(row["to_account_id"]).startswith("acc_")
        assert isinstance(row["amount_paise"], int)
        assert row["amount_paise"] > 0

    by_mov = {}
    for row in rows:
        by_mov.setdefault(str(row["movement_id"]), []).append(row)
    assert by_mov[exp["id"]][0]["is_reversal"] is False
    assert by_mov[exp["id"]][0]["is_reversed"] is True
    assert by_mov[exp["id"]][0]["from_account_liquid"] is True
    assert by_mov[exp["id"]][0]["to_account_liquid"] is False
    assert by_mov[rev.id][0]["is_reversal"] is True
    assert by_mov[rev.id][0]["is_reversed"] is False
    assert by_mov[rev.id][0]["from_account_kind"] == "spending"
    assert by_mov[rev.id][0]["from_account_liquid"] is False
    assert by_mov[rev.id][0]["to_account_id"] == bank["id"]
    assert by_mov[rev.id][0]["to_account_liquid"] is True
    assert {r["to_account_name"] for r in by_mov[split["id"]]} == {"Spending", "Neha"}


async def test_v_owed_and_v_balances_use_ist_today_regardless_of_session_timezone(db):
    from arno.ledger.accounts import create_account, ensure_system_accounts
    from arno.ledger.movements import record_lending
    from arno.ledger.payees import create_payee

    await ensure_system_accounts(db)
    bank = await create_account(db, kind="bank", name="TZ Bank")
    payee = await create_payee(db, display_name="TZ Friend", kind="person")
    ist_today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    yesterday = ist_today - timedelta(days=1)

    await record_lending(
        db,
        account_id=bank["id"],
        payee_id=payee["id"],
        amount_paise=5_000,
        direction="i_lent",
        expected_date=yesterday,
        date=yesterday,
    )
    await record_lending(
        db,
        account_id=bank["id"],
        payee_id=payee["id"],
        amount_paise=7_000,
        direction="i_lent",
        expected_date=ist_today,
        date=ist_today,
    )

    async with connect(_agent_ro_dsn()) as ro:
        for tz in ("Asia/Kolkata", "Pacific/Pago_Pago", "Pacific/Kiritimati"):
            await ro.execute(f"SET TIME ZONE '{tz}'")
            owed_date = await ro.fetchval(
                "SELECT next_expected_date FROM v_owed WHERE payee_id = $1", payee["id"]
            )
            assert owed_date == ist_today, f"v_owed drifted under session TZ {tz}: {owed_date}"

            bal_dates = [
                row["as_of"]
                for row in await ro.fetch(
                    "SELECT as_of FROM v_balances WHERE account_id = $1 ORDER BY as_of",
                    bank["id"],
                )
            ]
            assert bal_dates == [yesterday, ist_today], f"v_balances drifted under {tz}"
