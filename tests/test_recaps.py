"""Recaps: periods, stories computed from ledger rows, the rows behind each figure, and the job."""

from __future__ import annotations

from datetime import date

import pytest

from arno.jobs import recaps as recap_job
from arno.ledger.accounts import create_account
from arno.ledger.movements import record_expense, record_income, record_refund
from arno.ledger.payees import create_payee
from arno.tools import recaps

TODAY = date(2026, 11, 9)  # a Monday


async def _category(conn, name: str) -> str:
    return str(await conn.fetchval("SELECT id FROM categories WHERE name = $1", name))


async def _seed(conn) -> dict[str, str]:
    bank = await create_account(
        conn,
        kind="bank",
        name="SBI Savings",
        opening_balance_paise=50_000_000,
        opening_balance_as_of=date(2026, 9, 1),
    )
    food = await _category(conn, "Food & Dining")
    rent = await _category(conn, "Rent")
    cafe = await create_payee(conn, display_name="Blue Tokai", outside_text=True)
    landlord = await create_payee(conn, display_name="Landlord")
    account = bank["id"]
    # September: rent and one coffee. October: rent, three coffees, a refunded order, salary.
    await record_expense(
        conn,
        account_id=account,
        amount_paise=2_000_000,
        category_id=rent,
        payee_id=landlord["id"],
        date=date(2026, 9, 5),
        description="Rent",
    )
    await record_expense(
        conn,
        account_id=account,
        amount_paise=30_000,
        category_id=food,
        payee_id=cafe["id"],
        date=date(2026, 9, 12),
        description="Coffee",
    )
    await record_expense(
        conn,
        account_id=account,
        amount_paise=2_000_000,
        category_id=rent,
        payee_id=landlord["id"],
        date=date(2026, 10, 5),
        description="Rent",
    )
    for day in (3, 10, 17):
        await record_expense(
            conn,
            account_id=account,
            amount_paise=40_000,
            category_id=food,
            payee_id=cafe["id"],
            date=date(2026, 10, day),
            description="Coffee",
        )
    await record_expense(
        conn,
        account_id=account,
        amount_paise=150_000,
        category_id=food,
        date=date(2026, 10, 20),
        description="Dinner order",
    )
    await record_refund(
        conn,
        account_id=account,
        amount_paise=50_000,
        category_id=food,
        date=date(2026, 10, 22),
        description="Dinner order refund",
    )
    await record_income(
        conn,
        account_id=account,
        amount_paise=6_000_000,
        date=date(2026, 10, 1),
        description="Salary",
    )
    return {"account": account, "food": food}


def test_periods_name_themselves_and_compare_like_with_like():
    month = recaps.parse_period("month-2026-10", TODAY)
    assert (month.title, month.complete) == ("October", True)
    assert (month.previous_start, month.previous_end) == (date(2026, 9, 1), date(2026, 9, 30))

    so_far = recaps.parse_period("this_month", TODAY)
    assert so_far.title == "November so far" and not so_far.complete
    assert so_far.previous_end == date(2026, 10, 9)
    assert so_far.previous_label == "the same days of October"

    week = recaps.parse_period("last_week", TODAY)
    assert (week.id, week.start, week.end) == (
        "week-2026-11-02",
        date(2026, 11, 2),
        date(2026, 11, 8),
    )
    assert recaps.parse_period("yesterday", TODAY).title == "Yesterday"
    assert recaps.parse_period("2026-10", TODAY).id == "month-2026-10"
    for bad in ("week-2026-11-03", "day-2026-11-09", "month-2026-12", "soon"):
        with pytest.raises(ValueError):
            recaps.parse_period(bad, TODAY)


async def test_a_recap_tells_every_story_from_the_rows(ledger):
    await _seed(ledger)
    result = await recaps.recap(ledger, "month-2026-10", today=TODAY)
    stories = {story["key"]: story for story in result["stories"]}
    assert list(stories) == ["total", "in_out", "where", "change", "biggest", "frequent"]

    # 20,000 rent + 1,200 coffee + 1,500 dinner - 500 refund, refunds netted.
    assert stories["total"]["figure"]["paise"] == 2_220_000
    assert stories["total"]["line"] == "9% more than September."
    assert stories["in_out"]["figure"]["paise"] == 6_000_000 - 2_220_000
    assert stories["where"]["figure"]["name"] == "Rent"
    assert [bar["name"] for bar in stories["where"]["bars"]] == ["Rent", "Food & Dining"]
    assert stories["change"]["figure"]["name"] == "Food & Dining"
    assert stories["change"]["figure"]["paise"] == 220_000 - 30_000
    assert stories["biggest"]["figure"]["paise"] == 2_000_000
    assert stories["frequent"]["figure"]["count"] == 3
    # The cafe came from a statement: its name is marked, and the result says so.
    assert "<outside>" in stories["frequent"]["figure"]["name"]
    assert result["outside_text"] is True


async def test_every_figure_opens_rows_that_sum_to_it(ledger):
    await _seed(ledger)
    result = await recaps.recap(ledger, "month-2026-10", today=TODAY)
    for story in result["stories"]:
        reference = story["rows"]
        if reference is None:
            continue
        rows = await recaps.recap_rows(
            ledger, "month-2026-10", story=reference["story"], key=reference["key"], today=TODAY
        )
        expected = story["in"]["paise"] if story["key"] == "in_out" else story["figure"]["paise"]
        if story["key"] == "change":
            expected = 220_000  # the category's spend this period; the figure is the difference
        assert rows["sum"]["paise"] == expected, story["key"]


async def test_an_empty_period_says_so_and_the_list_starts_at_the_first_row(ledger):
    await _seed(ledger)
    empty = await recaps.recap(ledger, "month-2026-08", today=TODAY)
    assert [story["key"] for story in empty["stories"]] == ["total"]
    assert empty["stories"][0]["figure"]["paise"] == 0

    listed = await recaps.recap_list(ledger, today=TODAY)
    ids = [item["id"] for item in listed["periods"]]
    assert "month-2026-09" in ids and "month-2026-08" not in ids
    assert ids[0].startswith("week-") or ids[0].startswith("day-")
    october = next(item for item in listed["periods"] if item["id"] == "month-2026-10")
    assert october["new"] is True and october["spent"]["paise"] == 2_220_000
    assert listed["unseen"] == 1  # last week has no spending, so only October is new

    await recaps.mark_seen(ledger, "month-2026-10", today=TODAY)
    assert (await recaps.recap_list(ledger, today=TODAY))["unseen"] == 0


async def test_the_job_announces_a_month_once_and_skips_a_quiet_week(ledger):
    await _seed(ledger)
    first = await recap_job.announce_recaps(ledger, today=TODAY)
    assert [row["data"]["period_id"] for row in first] == ["month-2026-10"]
    assert first[0]["title"] == "Your October recap is ready"
    assert await recap_job.announce_recaps(ledger, today=TODAY) == []


def test_recap_routes_need_a_session(client):
    for path in ("/api/recaps", "/api/recaps/month-2026-10", "/api/recaps/month-2026-10/rows"):
        assert client.get(path).status_code == 401


async def test_recap_routes_answer(authed_client, ledger):
    await _seed(ledger)
    listed = authed_client.get("/api/recaps")
    assert listed.status_code == 200, listed.text
    assert authed_client.get("/api/recaps/month-2026-10").json()["stories"][0]["key"] == "total"
    assert authed_client.get("/api/recaps/month-2099-01").status_code == 404
    bad = authed_client.get("/api/recaps/month-2026-10/rows", params={"story": "nope"})
    assert bad.status_code == 422
    assert authed_client.post("/api/recaps/month-2026-10/seen").json()["seen"] is True


async def test_the_badge_counts_new_recaps_cheaply(ledger):
    await _seed(ledger)
    assert await recaps.recap_unseen(ledger, today=TODAY) == 1
    await recaps.mark_seen(ledger, "month-2026-10", today=TODAY)
    assert await recaps.recap_unseen(ledger, today=TODAY) == 0
