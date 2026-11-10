"""Regression tests for the wave-3 engine findings: prepayment tenure, finite questions, verdicts,
the projection's goal field name, schedule matching in pattern detection, and the demo loan split.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fin_agent.engine.affordability import Hypothetical, afford
from fin_agent.engine.money import amortize
from fin_agent.engine.patterns import Charge, KnownSchedule, detect_recurring
from fin_agent.engine.projection import (
    CardAccount,
    CashEvent,
    LiquidAccount,
    ProjectionInput,
)
from fin_agent.jobs.schedules import enqueue_due_schedules, sync_scheduled_questions
from fin_agent.tools import projection as projection_tools

INDIA = ZoneInfo("Asia/Kolkata")
DEMO_DAY = date(2026, 11, 1)


# --- e3 F1: a "reduce the EMI" prepayment kept the loan's original tenure ----------------------


def _demo_loan(**extra):
    # The demo persona's loan: ₹33,88,000 at 8.5%, ₹31,500 a month, 204 EMIs left of 240.
    return amortize(
        338_800_000,
        8.5,
        240,
        emi_paise=3_150_000,
        start_date=date(2026, 11, 10),
        **extra,
    )


def test_reducing_the_emi_keeps_the_emas_a_loan_actually_has_left():
    base = _demo_loan()
    assert base["months"] == 204

    reduced = _demo_loan(prepay={"amount_paise": 50_000_000, "at_emi": 1, "reduce": "emi"})
    prepay = reduced["prepay"]
    # ₹5,00,000 off ₹33,88,000 with 204 EMIs left: ₹26,809.08 a month, still paid off in 204.
    assert prepay["new_emi"]["paise"] == 2_680_908
    assert prepay["new_months"] == 204
    assert prepay["months_saved"] == 0
    assert prepay["interest_saved"]["paise"] == 43_446_199


def test_no_prepayment_option_ever_reports_a_negative_saving_or_a_longer_loan():
    base = _demo_loan()
    for reduce in ("emi", "tenure"):
        prepay = _demo_loan(prepay={"amount_paise": 50_000_000, "at_emi": 1, "reduce": reduce})[
            "prepay"
        ]
        assert prepay["interest_saved"]["paise"] > 0, reduce
        assert prepay["months_saved"] >= 0, reduce
        assert prepay["new_months"] <= base["months"], reduce


async def test_a_named_loan_prepayment_uses_its_real_remaining_emis(demo_ledger):
    from fin_agent.ledger.accounts import loan_terms

    terms = await loan_terms(demo_ledger, "Home loan")
    base = amortize(
        terms["principal_paise"],
        terms["annual_rate"],
        terms["months"],
        emi_paise=terms["emi_paise"],
        start_date=DEMO_DAY,
    )
    reduced = amortize(
        terms["principal_paise"],
        terms["annual_rate"],
        terms["months"],
        emi_paise=terms["emi_paise"],
        start_date=DEMO_DAY,
        prepay={"amount_paise": 50_000_000, "at_emi": 1, "reduce": "emi"},
    )
    assert reduced["prepay"]["new_emi"]["paise"] < base["emi"]["paise"]
    assert reduced["prepay"]["new_months"] <= base["months"]
    assert reduced["prepay"]["interest_saved"]["paise"] > 0


# --- e4 F1: a finite scheduled question restarted forever -------------------------------------


async def _question(db, rule: str) -> str:
    return str(
        await db.fetchval(
            "INSERT INTO scheduled_questions (prompt, rule) VALUES ($1, $2) RETURNING id",
            "What did I spend on food this week?",
            rule,
        )
    )


async def _question_schedule(db, question_id: str):
    return await db.fetchrow(
        """
        SELECT * FROM job_schedules
        WHERE kind = 'scheduled_question' AND payload ->> 'scheduled_question_id' = $1
        """,
        question_id,
    )


def _at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 11, day, hour, 0, tzinfo=INDIA)


def _local(value: datetime) -> datetime:
    return value.astimezone(INDIA)


async def test_a_count_one_question_runs_once_and_is_never_restarted(db):
    question_id = await _question(db, "FREQ=WEEKLY;BYDAY=MO;COUNT=1")
    await sync_scheduled_questions(db, now=_at(10))
    row = await _question_schedule(db, question_id)
    first = _local(row["dtstart"])
    assert row["active"] is True
    assert first == datetime(2026, 11, 16, 8, 0, tzinfo=INDIA)

    # A laptop that slept through the Monday still fires it once, on the next tick (Friday).
    assert await enqueue_due_schedules(db, now=_at(20)) == 1
    row = await _question_schedule(db, question_id)
    assert row["active"] is False
    assert _local(row["dtstart"]) == first

    # The next ticks used to reactivate the row with a fresh dtstart and recompute the count.
    for now in (_at(20, 10), _at(23), _at(30), _at(30, 10)):
        await sync_scheduled_questions(db, now=now)
        row = await _question_schedule(db, question_id)
        assert row["active"] is False, now
        assert _local(row["dtstart"]) == first, now

    assert await enqueue_due_schedules(db, now=_at(30, 10)) == 0
    assert await db.fetchval("SELECT count(*) FROM jobs WHERE kind = 'scheduled_question'") == 1


async def test_a_count_three_question_runs_three_mondays_and_stops(db):
    question_id = await _question(db, "FREQ=WEEKLY;BYDAY=MO;COUNT=3")
    await sync_scheduled_questions(db, now=_at(10))
    row = await _question_schedule(db, question_id)
    first = _local(row["dtstart"])
    assert first == datetime(2026, 11, 16, 8, 0, tzinfo=INDIA)

    for day in (16, 23, 30):
        assert await enqueue_due_schedules(db, now=_at(day)) == 1, day
        row = await _question_schedule(db, question_id)
        assert _local(row["dtstart"]) == first, day
        await sync_scheduled_questions(db, now=_at(day))

    row = await _question_schedule(db, question_id)
    assert row["active"] is False
    for now in (_at(30, 10), datetime(2026, 12, 7, 9, 0, tzinfo=INDIA)):
        await sync_scheduled_questions(db, now=now)
        row = await _question_schedule(db, question_id)
        assert row["active"] is False, now
        assert _local(row["dtstart"]) == first, now
        assert await enqueue_due_schedules(db, now=now) == 0

    assert await db.fetchval("SELECT count(*) FROM jobs WHERE kind = 'scheduled_question'") == 3


async def test_editing_a_questions_rule_starts_a_new_anchor_and_reactivates_it(db):
    question_id = await _question(db, "FREQ=WEEKLY;BYDAY=MO;COUNT=1")
    await sync_scheduled_questions(db, now=_at(10))
    await enqueue_due_schedules(db, now=_at(16))
    assert (await _question_schedule(db, question_id))["active"] is False

    await db.execute(
        "UPDATE scheduled_questions SET rule = $1 WHERE id = $2",
        "FREQ=WEEKLY;BYDAY=MO;COUNT=2",
        question_id,
    )
    await sync_scheduled_questions(db, now=_at(23))
    row = await _question_schedule(db, question_id)
    assert row["active"] is True
    assert _local(row["dtstart"]) == datetime(2026, 11, 30, 8, 0, tzinfo=INDIA)


# --- e4 F11/F12: a later date alone is a caveat, not a yes-but ---------------------------------


def _inputs(
    *,
    start: int = 1_000_000,
    events: tuple[CashEvent, ...] = (),
    cards: tuple[CardAccount, ...] = (),
) -> ProjectionInput:
    return ProjectionInput(
        as_of=date(2026, 1, 1),
        floor_paise=0,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=start),),
        events=events,
        cards=cards,
        ask_horizon_days=90,
    )


def _salary(when: date, amount: int = 200_000) -> CashEvent:
    return CashEvent(date=when, amount_paise=amount, name="Salary", kind="income_fixed")


def test_a_wait_date_alone_keeps_the_verdict_no_and_is_still_listed():
    result = afford(
        _inputs(start=50_000, events=(_salary(date(2026, 2, 1)),)),
        Hypothetical(amount_paise=100_000, date=date(2026, 1, 1)),
        runs=50,
    )
    assert result["verdict"] == "no"
    assert [trade["kind"] for trade in result["tradeoffs"]] == ["wait"]
    assert result["tradeoffs"][0]["date"] == "2026-02-01"
    assert "of simulated futures" in result["verdict_reason"]


def test_a_wait_date_equal_to_the_asked_date_is_not_a_tradeoff():
    card = CardAccount(
        id="card",
        name="Slice card",
        statement_day=5,
        due_day=25,
        outstanding_paise=0,
        credit_limit_paise=1_000_000,
        known=False,
    )
    result = afford(
        _inputs(start=1_000_000, cards=(card,)),
        Hypothetical(
            amount_paise=100_000, date=date(2026, 1, 1), method="card", account="Slice card"
        ),
        runs=20,
    )
    assert result["hypothetical"]["feasible"] is False
    assert result["verdict"] == "no"
    assert result["tradeoffs"] == []
    assert "unknown" in result["verdict_reason"]


def test_a_same_date_tradeoff_still_makes_it_yes_but():
    card = CardAccount(
        id="card",
        name="Slice card",
        statement_day=5,
        due_day=25,
        outstanding_paise=0,
        credit_limit_paise=1_000_000,
    )
    result = afford(
        _inputs(start=50_000, events=(_salary(date(2026, 1, 20)),), cards=(card,)),
        Hypothetical(amount_paise=100_000, date=date(2026, 1, 1), method="account"),
        runs=50,
    )
    assert result["verdict"] == "yes-but"
    by_kind = {trade["kind"]: trade for trade in result["tradeoffs"]}
    assert by_kind["wait"]["date"] == "2026-01-20"
    assert by_kind["pay_by_card"]["due_date"] == "2026-01-25"


# --- e4 F7: the projection's goal "remaining" is named apart from a goal card's ---------------


async def test_the_projection_names_its_goal_remaining_apart_from_a_goal_card(demo_ledger):
    result = await projection_tools.project(demo_ledger, as_of=DEMO_DAY, runs=50)
    goal = next(row for row in result["goals"] if row["name"] == "Goa trip")
    assert "remaining_paise" not in goal
    assert goal["remaining_after_plan_paise"] >= 0

    compact = projection_tools.summary(result)
    compact_goal = next(row for row in compact["goals"] if row["name"] == "Goa trip")
    assert "remaining" not in compact_goal
    assert compact_goal["remaining_after_plan"]["paise"] == goal["remaining_after_plan_paise"]

    card = projection_tools.card(result)
    card_goal = next(row for row in card["goals"] if row["name"] == "Goa trip")
    assert card_goal["remaining_after_plan"]["paise"] == goal["remaining_after_plan_paise"]
    assert "remaining" not in card_goal

    # The goal card's own figure still means target minus saved.
    from fin_agent.ledger.goals import list_goals

    listed = next(row for row in await list_goals(demo_ledger) if row["name"] == "Goa trip")
    assert listed["remaining_paise"] == 9_000_000


# --- e6 F7: a schedule covers a charge by category, amount, and cadence ------------------------


def _rent_charges(amount_paise: int = 2_800_000) -> list[Charge]:
    return [
        Charge(
            movement_id=f"mov_{index}",
            merchant="Ramesh (landlord)",
            normalized="ramesh landlord",
            payee_id="pay_landlord",
            outside_text=False,
            date=when,
            amount_paise=amount_paise,
            category_id="cat_rent",
        )
        for index, when in enumerate(
            (date(2026, 8, 5), date(2026, 9, 5), date(2026, 10, 5), date(2026, 11, 5))
        )
    ]


def _rent_schedule(*, amount_paise: int = 2_800_000, interval_days: int = 30) -> KnownSchedule:
    return KnownSchedule(
        id="sch_rent",
        name="Rent",
        normalized="rent",
        amount_paise=amount_paise,
        category_id="cat_rent",
        interval_days=interval_days,
    )


def test_a_schedule_covers_a_payee_charge_by_category_amount_and_cadence():
    # "Rent" and "Ramesh (landlord)" share no word, so name matching alone proposed it again.
    assert detect_recurring(_rent_charges(), [_rent_schedule()]) == []


def test_a_different_amount_is_still_a_new_recurring_charge():
    proposals = detect_recurring(_rent_charges(), [_rent_schedule(amount_paise=2_000_000)])
    assert [proposal["merchant"] for proposal in proposals] == ["Ramesh (landlord)"]


def test_a_different_cadence_is_still_a_new_recurring_charge():
    proposals = detect_recurring(_rent_charges(), [_rent_schedule(interval_days=7)])
    assert [proposal["merchant"] for proposal in proposals] == ["Ramesh (landlord)"]


async def test_the_demo_personas_rent_is_not_proposed_as_a_new_charge(demo_ledger):
    from fin_agent.engine import patterns

    proposals = await patterns.detect(demo_ledger, as_of=DEMO_DAY)
    assert not any(
        proposal["kind"] == "new_subscription" and "Ramesh" in proposal["merchant"]
        for proposal in proposals
    )


# --- e3 F5 / e1 F11: the demo persona's own data is internally consistent ---------------------


async def test_the_demo_loan_book_a_months_interest_at_its_rate(demo_ledger):
    rows = await demo_ledger.fetch(
        """
        SELECT m.id, m.date,
               sum(CASE WHEN a.kind = 'spending' THEN e.amount ELSE 0 END)::bigint
                   AS interest_paise,
               sum(CASE WHEN a.kind = 'loan' THEN e.amount ELSE 0 END)::bigint AS principal_paise
        FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        JOIN pgledger_entries e ON e.transfer_id = t.id
        JOIN accounts a ON a.ledger_account_id = e.account_id
        WHERE m.kind = 'loan' AND m.reverses_id IS NULL
        GROUP BY m.id, m.date
        ORDER BY m.date
        """
    )
    assert len(rows) >= 3
    first = rows[0]
    # 8.5% of the seeded ₹35,00,000 over a year is ₹24,791.67 a month, not a flat ₹3,500.
    assert 2_470_000 <= first["interest_paise"] <= 2_490_000
    assert first["interest_paise"] + first["principal_paise"] == 3_150_000
    # Interest falls as the principal does.
    assert all(row["interest_paise"] < first["interest_paise"] for row in rows[1:]), [
        row["interest_paise"] for row in rows
    ]


async def test_no_live_demo_movement_still_says_it_was_entered_wrong(demo_ledger):
    rows = await demo_ledger.fetch("SELECT description FROM movements WHERE reverses_id IS NULL")
    assert not any("wrong" in str(row["description"]).lower() for row in rows)
