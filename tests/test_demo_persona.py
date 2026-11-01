from __future__ import annotations

from datetime import date

import psycopg
from psycopg.rows import dict_row

from fin_agent.config import get_settings
from fin_agent.demo.persona import seed_demo
from fin_agent.demo.template import clone_database
from fin_agent.ledger.accounts import list_accounts
from fin_agent.ledger.audit import undo_group
from fin_agent.ledger.goals import list_goals
from fin_agent.ledger.movements import list_movements
from fin_agent.ledger.schedules import list_schedules, upcoming

DEMO_DAY = date(2026, 11, 1)


def test_demo_persona_seeds_a_complete_indian_ledger(db):
    summary = seed_demo(db, today=DEMO_DAY, reset=True)

    assert summary["movements"] >= 50
    assert summary["categories"] == 22
    assert summary["schedules"] == 10
    assert summary["goals"] == 1
    assert summary["people"] == 5

    accounts = {account["name"]: account for account in list_accounts(db)}
    assert accounts["HDFC Savings"]["balance_paise"] > 0
    assert accounts["SBI Salary"]["balance_paise"] > 0
    assert accounts["Slice card"]["card_outstanding_paise"] > 0
    assert accounts["Home loan"]["loan_outstanding_paise"] > 0
    assert accounts["Goa trip goal"]["liquid"] is False
    assert accounts["Goa trip goal"]["balance_paise"] == 3_000_000

    goals = list_goals(db)
    assert goals[0]["name"] == "Goa trip"
    assert goals[0]["saved_paise"] == 3_000_000
    assert goals[0]["progress"] == 0.25

    schedules = list_schedules(db)
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
    events = upcoming(db, days=45, start=DEMO_DAY)["events"]
    assert any(event["kind"] == "card_due" for event in events)

    owed = {row["display_name"]: row for row in db.execute("SELECT * FROM v_owed").fetchall()}
    assert owed["Ravi"]["balance_paise"] > 0
    assert owed["Amit"]["balance_paise"] == 20_000

    rules = db.execute(
        "SELECT merchant_pattern, source FROM category_rules ORDER BY merchant_pattern"
    ).fetchall()
    assert any(
        rule["merchant_pattern"] == "bigbasket" and rule["source"] == "user" for rule in rules
    )

    movements = list_movements(db, limit=200)["items"]
    assert movements
    assert any(movement["kind"] == "split" for movement in movements)
    assert any(movement["kind"] == "loan" for movement in movements)
    assert any(movement["kind"] == "refund" for movement in movements)

    # Every write is in a group. Undo one movement and prove the bank balance moves back.
    bank = accounts["HDFC Savings"]
    before = bank["balance_paise"]
    expense = next(movement for movement in movements if movement["kind"] == "expense")
    undone = undo_group(db, expense["undo_id"])
    assert undone["status"] == "undone"
    refreshed = {account["name"]: account for account in list_accounts(db)}
    assert refreshed["HDFC Savings"]["balance_paise"] != before


def test_demo_seed_is_deterministic_and_can_be_reseeded(db):
    first = seed_demo(db, today=DEMO_DAY, reset=True)
    second = seed_demo(db, today=DEMO_DAY, reset=True)

    assert first["movements"] == second["movements"]
    assert first["accounts"] == second["accounts"]
    assert first["schedules"] == second["schedules"]
    assert db.execute("SELECT count(*) AS n FROM audit_log").fetchone()["n"] > 0


def test_demo_template_can_be_cloned():
    settings = get_settings(refresh=True)
    target = "fin_agent_demo_template_test"
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        seed_demo(conn, today=DEMO_DAY, reset=True)
        source = conn.info.dbname
    try:
        clone_database(settings.database_url, source=source, target=target, terminate=False)
        info = psycopg.conninfo.conninfo_to_dict(settings.database_url)
        info["dbname"] = target
        with psycopg.connect(psycopg.conninfo.make_conninfo(**info), row_factory=dict_row) as clone:
            assert clone.execute("SELECT count(*) AS n FROM movements").fetchone()["n"] > 0
            assert clone.execute("SELECT count(*) AS n FROM goals").fetchone()["n"] == 1
    finally:
        info = psycopg.conninfo.conninfo_to_dict(settings.database_url)
        info["dbname"] = "postgres"
        with psycopg.connect(psycopg.conninfo.make_conninfo(**info), autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)')
