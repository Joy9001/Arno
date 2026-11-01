from __future__ import annotations

from datetime import date

from fin_agent.ledger.accounts import create_account, get_account, set_statement_balance
from fin_agent.ledger.audit import undo_group
from fin_agent.ledger.categories import list_rules, upsert_rule
from fin_agent.ledger.goals import create_goal, get_goal
from fin_agent.ledger.schedules import create_schedule, get_schedule
from fin_agent.ledger.settings import read_settings, update_settings


def _category(conn, name: str) -> str:
    return conn.execute("SELECT id FROM categories WHERE name = %s", (name,)).fetchone()["id"]


def test_schedule_creation_is_undoable(ledger):
    bank = create_account(ledger, kind="bank", name="HDFC", opening_balance_paise=10_000_000)
    schedule = create_schedule(
        ledger,
        kind="obligation",
        subtype="rent",
        name="Rent",
        amount_paise=2_800_000,
        account_id=bank["id"],
        category_id=_category(ledger, "Rent"),
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=date(2026, 11, 1),
        holiday_adjust="next",
    )

    undo_group(ledger, schedule["undo_id"])

    assert get_schedule(ledger, schedule["id"])["active"] is False


def test_goal_creation_is_undoable(ledger):
    goal = create_goal(ledger, name="Goa trip", target_paise=12_000_000)

    undo_group(ledger, goal["undo_id"])

    refreshed = get_goal(ledger, goal["id"])
    assert refreshed["status"] == "abandoned"
    assert get_account(ledger, goal["account_id"])["active"] is False


def test_rule_creation_is_undoable(ledger):
    rule = upsert_rule(
        ledger,
        merchant_pattern="Swiggy",
        category_id=_category(ledger, "Food & Dining"),
        source="user",
    )

    undo_group(ledger, rule["undo_id"])

    assert not list_rules(ledger)


def test_floor_update_is_undoable(ledger):
    before = read_settings(ledger)["floor_paise"]
    result = update_settings(ledger, floor_paise=2_500_000)
    assert result["floor_paise"] == 2_500_000

    undo_group(ledger, result["undo_id"])

    assert read_settings(ledger)["floor_paise"] == before


def test_statement_balance_adjustment_is_undoable(ledger):
    account = create_account(ledger, kind="bank", name="SBI", opening_balance_paise=1_000_000)
    result = set_statement_balance(ledger, account["id"], balance_paise=1_400_000)

    undo_group(ledger, result["undo_id"])

    refreshed = get_account(ledger, account["id"])
    assert refreshed["balance_paise"] == 1_000_000
    assert refreshed["statement_balance_paise"] == 1_000_000
