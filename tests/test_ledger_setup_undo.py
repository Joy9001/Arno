from __future__ import annotations

from datetime import date

from arno.ledger.accounts import create_account, get_account, set_statement_balance
from arno.ledger.audit import undo_group
from arno.ledger.categories import list_rules, upsert_rule
from arno.ledger.goals import create_goal, get_goal
from arno.ledger.schedules import create_schedule, get_schedule
from arno.ledger.settings import read_settings, update_settings


async def _category(conn, name: str) -> str:
    return (await conn.fetchrow("SELECT id FROM categories WHERE name = $1", name))["id"]


async def test_schedule_creation_is_undoable(ledger):
    bank = await create_account(ledger, kind="bank", name="HDFC", opening_balance_paise=10_000_000)
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        subtype="rent",
        name="Rent",
        amount_paise=2_800_000,
        account_id=bank["id"],
        category_id=await _category(ledger, "Rent"),
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=date(2026, 11, 1),
        holiday_adjust="next",
    )

    await undo_group(ledger, schedule["undo_id"])

    assert (await get_schedule(ledger, schedule["id"]))["active"] is False


async def test_goal_creation_is_undoable(ledger):
    goal = await create_goal(ledger, name="Goa trip", target_paise=12_000_000)

    await undo_group(ledger, goal["undo_id"])

    refreshed = await get_goal(ledger, goal["id"])
    assert refreshed["status"] == "abandoned"
    assert (await get_account(ledger, goal["account_id"]))["active"] is False


async def test_rule_creation_is_undoable(ledger):
    rule = await upsert_rule(
        ledger,
        pattern="Swiggy",
        category_id=await _category(ledger, "Food & Dining"),
        source="user",
    )

    await undo_group(ledger, rule["undo_id"])

    assert not await list_rules(ledger)


async def test_floor_update_is_undoable(ledger):
    before = (await read_settings(ledger))["floor_paise"]
    result = await update_settings(ledger, floor_paise=2_500_000)
    assert result["floor_paise"] == 2_500_000

    await undo_group(ledger, result["undo_id"])

    assert (await read_settings(ledger))["floor_paise"] == before


async def test_statement_balance_adjustment_is_undoable(ledger):
    account = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=1_000_000)
    result = await set_statement_balance(ledger, account["id"], balance_paise=1_400_000)

    await undo_group(ledger, result["undo_id"])

    refreshed = await get_account(ledger, account["id"])
    assert refreshed["balance_paise"] == 1_000_000
    assert refreshed["statement_balance_paise"] == 1_000_000
