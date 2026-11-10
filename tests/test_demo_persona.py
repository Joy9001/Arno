from __future__ import annotations

from datetime import date

import pytest

from arno.config import get_settings
from arno.db.conn import connect, conninfo_to_dict, make_conninfo
from arno.demo.persona import seed_demo
from arno.demo.template import clone_database, drop_database
from arno.ledger.accounts import list_accounts
from arno.ledger.audit import undo_group
from arno.ledger.goals import list_goals
from arno.ledger.movements import list_movements
from arno.ledger.schedules import list_schedules, upcoming
from arno.tools import read as read_tools

DEMO_DAY = date(2026, 11, 1)


async def test_demo_persona_seeds_a_complete_indian_ledger(db):
    summary = await seed_demo(db, today=DEMO_DAY, reset=True)

    assert summary["movements"] >= 50
    assert summary["categories"] == 22
    assert summary["schedules"] == 10
    assert summary["goals"] == 1
    assert summary["people"] == 5

    accounts = {account["name"]: account for account in await list_accounts(db)}
    assert accounts["HDFC Savings"]["balance_paise"] > 0
    assert accounts["SBI Salary"]["balance_paise"] > 0
    # The persona's card has spending but no stated balance, so its debt stays unknown.
    assert accounts["Slice card"]["balance_paise"] < 0
    assert accounts["Slice card"]["card_outstanding_paise"] is None
    assert accounts["Home loan"]["loan_outstanding_paise"] > 0
    assert accounts["Goa trip goal"]["liquid"] is False
    assert accounts["Goa trip goal"]["balance_paise"] == 3_000_000

    goals = await list_goals(db)
    assert goals[0]["name"] == "Goa trip"
    assert goals[0]["saved_paise"] == 3_000_000
    assert goals[0]["progress"] == 0.25

    schedules = await list_schedules(db)
    kinds = {schedule["kind"] for schedule in schedules}
    assert {
        "income_fixed",
        "income_variable",
        "income_oneoff",
        "obligation",
        "instalment",
        "goal_contribution",
    } <= kinds
    assert any(schedule["subtype"] == "family_transfer" for schedule in schedules)
    events = (await upcoming(db, days=45, start=DEMO_DAY))["events"]
    assert any(event["kind"] == "card_due" for event in events)

    owed = {row["display_name"]: row for row in await db.fetch("SELECT * FROM v_owed")}
    assert owed["Ravi"]["balance_paise"] > 0
    assert owed["Amit"]["balance_paise"] == 20_000

    rules = await db.fetch("SELECT pattern, source FROM category_rules ORDER BY pattern")
    assert any(rule["pattern"] == "bigbasket" and rule["source"] == "user" for rule in rules)

    movements = (await list_movements(db, limit=200))["items"]
    assert movements
    assert any(movement["kind"] == "split" for movement in movements)
    assert any(movement["kind"] == "loan" for movement in movements)
    assert any(movement["kind"] == "refund" for movement in movements)

    # Every write is in a group. Undo one movement and prove the bank balance moves back.
    bank = accounts["HDFC Savings"]
    before = bank["balance_paise"]
    expense = next(movement for movement in movements if movement["kind"] == "expense")
    undone = await undo_group(db, expense["undo_id"])
    assert undone["status"] == "undone"
    refreshed = {account["name"]: account for account in await list_accounts(db)}
    assert refreshed["HDFC Savings"]["balance_paise"] != before


async def test_demo_persona_has_recent_statement_coverage(demo_ledger):
    context = await read_tools.get_financial_context(demo_ledger)
    hdfc = next(account for account in context["accounts"] if account["name"] == "HDFC Savings")

    october = await read_tools.coverage(
        demo_ledger,
        account_id=hdfc["id"],
        start=date(2026, 10, 1),
        end=date(2026, 10, 31),
    )
    assert october == {
        "complete": True,
        "gaps": [],
        "from": "2026-10-01",
        "to": "2026-10-31",
    }

    july = await read_tools.coverage(
        demo_ledger,
        account_id=hdfc["id"],
        start=date(2026, 7, 1),
        end=date(2026, 7, 31),
    )
    assert july["complete"] is False
    assert july["gaps"] == ["2026-07"]
    assert july["from"] is None
    assert july["to"] is None

    # The 13-month snapshot still reports older gaps, but the covered range is the recent
    # three months, not the whole window.
    assert hdfc["covered"]["from"] == "2026-08-01"
    assert hdfc["covered"]["to"] == "2026-10-31"
    assert "2026-10" not in hdfc["covered"]["gaps"]


async def test_summarize_and_query_report_covered_and_gap_periods(demo_ledger):
    october = await read_tools.summarize_spending(demo_ledger, group_by="none", period="2026-10")
    assert october["coverage"]["complete"] is True
    assert october["coverage"]["gaps"] == []
    assert october["coverage"]["from"] == "2026-10-01"
    assert october["coverage"]["to"] == "2026-10-31"
    assert october["total"] is not None

    july = await read_tools.summarize_spending(demo_ledger, group_by="none", period="2026-07")
    assert july["coverage"]["complete"] is False
    assert july["coverage"]["gaps"] == ["2026-07"]
    assert july["total"] is None

    october_rows = await read_tools.query_movements(demo_ledger, filters={"period": "2026-10"})
    assert october_rows["coverage"]["complete"] is True
    assert october_rows["coverage"]["from"] == "2026-10-01"
    assert october_rows["coverage"]["to"] == "2026-10-31"
    assert october_rows["rows"]

    july_rows = await read_tools.query_movements(demo_ledger, filters={"period": "2026-07"})
    assert july_rows["coverage"]["complete"] is False
    assert july_rows["coverage"]["gaps"] == ["2026-07"]
    assert july_rows["rows"] == []


async def test_demo_coverage_switch_leaves_the_oldest_month_open(db):
    summary = await seed_demo(db, today=DEMO_DAY, reset=True, coverage_gap=True)
    assert summary["statement_coverage"]["gap"] == "2026-08"
    assert summary["statement_coverage"]["months"] == ["2026-09", "2026-10"]

    hdfc = (await db.fetchrow("SELECT id FROM accounts WHERE name = 'HDFC Savings'"))["id"]
    august = await read_tools.coverage(
        db, account_id=hdfc, start=date(2026, 8, 1), end=date(2026, 8, 31)
    )
    september = await read_tools.coverage(
        db, account_id=hdfc, start=date(2026, 9, 1), end=date(2026, 9, 30)
    )
    assert august["complete"] is False
    assert august["gaps"] == ["2026-08"]
    assert september["complete"] is True


async def test_demo_statement_coverage_can_be_disabled(db):
    summary = await seed_demo(db, today=DEMO_DAY, reset=True, statement_coverage=False)
    assert summary["imports"] == 0
    assert summary["statement_coverage"]["months"] == []

    hdfc = (await db.fetchrow("SELECT id FROM accounts WHERE name = 'HDFC Savings'"))["id"]
    october = await read_tools.coverage(
        db, account_id=hdfc, start=date(2026, 10, 1), end=date(2026, 10, 31)
    )
    assert october["complete"] is False
    assert october["gaps"] == ["2026-10"]


async def test_demo_seed_is_deterministic_and_can_be_reseeded(db):
    first = await seed_demo(db, today=DEMO_DAY, reset=True)
    second = await seed_demo(db, today=DEMO_DAY, reset=True)

    assert first["movements"] == second["movements"]
    assert first["accounts"] == second["accounts"]
    assert first["schedules"] == second["schedules"]
    assert first["imports"] == second["imports"]
    assert first["statement_coverage"] == second["statement_coverage"]
    assert (await db.fetchrow("SELECT count(*) AS n FROM audit_log"))["n"] > 0


@pytest.mark.xdist_group("cluster")
async def test_demo_template_can_be_cloned():
    settings = get_settings(refresh=True)
    target = "arno_demo_template_test"
    source = conninfo_to_dict(settings.database_url)["dbname"]
    async with connect(settings.database_url) as conn:
        await seed_demo(conn, today=DEMO_DAY, reset=True)
    try:
        await clone_database(settings.database_url, source=source, target=target, terminate=True)
        info = conninfo_to_dict(settings.database_url)
        info["dbname"] = target
        async with connect(make_conninfo(**info)) as clone:
            assert (await clone.fetchrow_required("SELECT count(*) AS n FROM movements"))["n"] > 0
            assert (await clone.fetchrow_required("SELECT count(*) AS n FROM goals"))["n"] == 1
            assert (await clone.fetchrow_required("SELECT count(*) AS n FROM imports"))["n"] > 0
    finally:
        await drop_database(settings.database_url, target)
