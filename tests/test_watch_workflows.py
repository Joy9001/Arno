"""Built-in workflows: reminders, risk warnings, proposals, and questions."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from arno.agent.builder import build_agent
from arno.jobs.questions import run_scheduled_question
from arno.jobs.reminders import send_reminders
from arno.jobs.risk import risk_warning
from arno.ledger.accounts import create_account
from arno.ledger.common import today as ledger_today
from arno.ledger.movements import record_expense
from arno.scheduled_questions import create_question
from arno.tools.patterns import detect, detect_missed
from tests.agent_helpers import streaming_model


def _prose_model(text: str) -> FunctionModel:
    # Streamed only: the briefing and scheduled-question runs pass an event stream handler.
    return streaming_model(lambda messages, info: ModelResponse(parts=[TextPart(text)]))


async def test_reminders_are_created_once_inside_the_lead_window(db):
    due = ledger_today() + timedelta(days=3)
    await db.execute(
        """
        INSERT INTO schedules (kind, subtype, name, amount_paise, rule, starts)
        VALUES ('obligation', 'rent', 'Rent', 2500000, 'FREQ=DAILY', $1)
        """,
        due,
    )
    first = await send_reminders(db, as_of=ledger_today(), lead_days=3)
    assert len(first) == 1
    assert first[0]["kind"] == "reminder"
    assert "Rent" in first[0]["title"]
    assert "₹25,000" in first[0]["body"]

    second = await send_reminders(db, as_of=ledger_today(), lead_days=3)
    assert second == []
    assert await db.fetchval("SELECT count(*) FROM notifications WHERE kind = 'reminder'") == 1


async def test_risk_warns_once_a_week(db, monkeypatch):
    state: dict[str, Any] = {
        "floor_paise": 100000,
        "floor_probability": 0.55,
        "floor_threshold": 0.9,
        "first_floor_crossing": "2026-11-20",
        "median_low": {"date": "2026-11-20", "paise": 90000},
        "data_quality": {"gaps": []},
        "source_ids": {},
    }

    async def fake_project(conn, **kwargs):
        del conn, kwargs
        return dict(state)

    monkeypatch.setattr("arno.tools.projection.project", fake_project)
    monday = date(2026, 11, 2)
    first = await risk_warning(db, as_of=monday)
    assert first is not None
    assert first["kind"] == "risk_warning"
    assert await risk_warning(db, as_of=monday + timedelta(days=3)) is None

    state["floor_probability"] = 0.95
    assert await risk_warning(db, as_of=monday + timedelta(days=7)) is None

    state["floor_probability"] = 0.5
    assert await risk_warning(db, as_of=monday + timedelta(days=7)) is not None
    assert await db.fetchval("SELECT count(*) FROM notifications WHERE kind = 'risk_warning'") == 2


async def test_risk_stays_quiet_while_setup_makes_the_projection_meaningless(db, monkeypatch):
    state: dict[str, Any] = {
        "floor_paise": 0,
        "floor_probability": 0.0,
        "floor_threshold": 0.9,
        "first_floor_crossing": "2026-11-01",
        "median_low": {"date": "2026-11-01", "paise": -5000},
        "data_quality": {"gaps": [], "setup_gaps": ["no confirmed income schedule"]},
        "source_ids": {},
    }

    async def fake_project(conn, **kwargs):
        del conn, kwargs
        return dict(state)

    monkeypatch.setattr("arno.tools.projection.project", fake_project)

    assert await risk_warning(db) is None
    assert await db.fetchval("SELECT count(*) FROM notifications WHERE kind = 'risk_warning'") == 0

    # Once the income schedule exists the same projection is a real warning.
    state["data_quality"]["setup_gaps"] = []
    warning = await risk_warning(db)
    assert warning is not None and warning["kind"] == "risk_warning"


async def test_recurring_detection_proposes_a_new_charge_and_keeps_outside_text(ledger):
    from arno.ledger.payees import create_payee
    from arno.ledger.schedules import create_schedule

    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    netflix = await create_payee(ledger, display_name="Netflix", kind="merchant", outside_text=True)
    random_shop = await create_payee(ledger, display_name="Corner Store", kind="merchant")

    for month in (6, 7, 8, 9):
        await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=64_900,
            description="Netflix subscription",
            payee_id=netflix["id"],
            date=date(2026, month, 1),
        )

    for when, amt in (
        (date(2026, 6, 3), 12_000),
        (date(2026, 6, 17), 85_000),
        (date(2026, 7, 29), 34_000),
        (date(2026, 9, 2), 199_000),
    ):
        await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=amt,
            description="Corner store purchase",
            payee_id=random_shop["id"],
            date=when,
        )

    as_of = date(2026, 10, 15)
    proposals = await detect(ledger, as_of=as_of)
    sub_proposals = [p for p in proposals if p["kind"] == "new_subscription"]
    assert len(sub_proposals) == 1
    prop = sub_proposals[0]
    assert prop["payee_id"] == netflix["id"]
    assert prop["interval_days"] == 30
    assert prop["outside_text"] is True
    assert prop["confidence"] >= 0.9
    assert prop["rule"]["family"] == "monthly"
    assert prop["rule"]["description"]
    assert prop["next_date"] == "2026-11-01"
    assert not any(p.get("payee_id") == random_shop["id"] for p in proposals)

    await create_schedule(
        ledger,
        kind="obligation",
        subtype="subscription",
        name="Netflix",
        amount_paise=64_900,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=1",
        starts=date(2026, 6, 1),
    )
    covered = await detect(ledger, as_of=as_of)
    assert not any(
        p["kind"] == "new_subscription" and p["payee_id"] == netflix["id"] for p in covered
    )


async def test_a_recurring_notice_names_the_payee_not_the_statement_narration(ledger):
    """A bank's narration token is not the merchant's name; the payee's display name is."""
    from arno.jobs.patterns import run_pattern_detection
    from arno.ledger.payees import create_payee

    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    coffee = await create_payee(
        ledger, display_name="Corner Store Coffee", kind="merchant", outside_text=True
    )
    for month in (6, 7, 8, 9):
        await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=45_000,
            description="UPI/DR/620529146532/Corner S/YESB/cornerst/AT 17565",
            payee_id=coffee["id"],
            date=date(2026, month, 1),
        )

    rows = await run_pattern_detection(ledger, as_of=date(2026, 10, 15))

    notice = next(row for row in rows if row["kind"] == "new_subscription")
    assert notice["title"] == "New recurring charge: Corner Store Coffee"
    assert notice["body"].startswith("Corner Store Coffee charged")
    assert notice["dedupe_key"].startswith("pattern:new:")
    # The key keeps the payee's normalized name, so an already-sent notice is not re-sent.
    assert (
        notice["dedupe_key"] == f"pattern:new:corner store coffee:30:{notice['data']['last_date']}"
    )


async def test_a_price_step_notifies_once_and_rearms_on_the_next_step(ledger):
    from arno.jobs.patterns import run_pattern_detection
    from arno.ledger.payees import create_payee

    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    cloud = await create_payee(
        ledger, display_name="Cloud Storage", kind="merchant", outside_text=True
    )
    for month, amount in (
        (3, 49_900),
        (4, 49_900),
        (5, 49_900),
        (6, 59_900),
        (7, 59_900),
        (8, 59_900),
    ):
        await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=amount,
            description="Cloud Storage",
            payee_id=cloud["id"],
            date=date(2026, month, 5),
        )

    from arno.notifications import seed_chat_text

    first = await run_pattern_detection(ledger, as_of=date(2026, 8, 20))
    changes = [row for row in first if row["kind"] == "price_change"]
    assert len(changes) == 1
    row = changes[0]
    assert row["title"] == "Price change: Cloud Storage"
    assert "₹499.00" in row["body"]
    assert "₹599.00" in row["body"]
    assert "2026-06-05" in row["body"]
    assert row["data"]["step_date"] == "2026-06-05"
    assert row["dedupe_key"] == f"pattern:price:{cloud['id']}:2026-06-05"

    text = seed_chat_text(row)
    assert "Cloud Storage" in text

    again = await run_pattern_detection(ledger, as_of=date(2026, 8, 20))
    assert not [item for item in again if item["kind"] == "price_change"]
    assert (
        await ledger.fetchval("SELECT count(*) FROM notifications WHERE kind = 'price_change'") == 1
    )

    # Three payments at the new level, then a second step: it alerts at its own date.
    for when in (date(2026, 9, 5), date(2026, 10, 5)):
        await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=69_900,
            description="Cloud Storage",
            payee_id=cloud["id"],
            date=when,
        )
    later = await run_pattern_detection(ledger, as_of=date(2026, 10, 20))
    changes = [item for item in later if item["kind"] == "price_change"]
    assert len(changes) == 1
    assert changes[0]["data"]["step_date"] == "2026-09-05"
    assert changes[0]["data"]["new_paise"] == 69_900
    assert (
        await ledger.fetchval("SELECT count(*) FROM notifications WHERE kind = 'price_change'") == 2
    )


async def test_a_price_step_alerts_only_inside_the_alert_window(ledger):
    from arno.ledger.payees import create_payee

    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    cloud = await create_payee(ledger, display_name="Cloud Storage", kind="merchant")
    for month, amount in (
        (5, 49_900),
        (6, 49_900),
        (7, 49_900),
        (8, 59_900),
        (9, 59_900),
        (10, 59_900),
    ):
        await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=amount,
            description="Cloud Storage",
            payee_id=cloud["id"],
            date=date(2026, month, 5),
        )

    # The step is 90 days old: still news.
    inside = await detect(ledger, as_of=date(2026, 11, 3))
    assert [p["step_date"] for p in inside if p["kind"] == "price_change"] == ["2026-08-05"]

    # One day later the same step is history, so a backfill cannot raise it.
    outside = await detect(ledger, as_of=date(2026, 11, 4))
    assert not [p for p in outside if p["kind"] == "price_change"]


async def test_an_income_price_step_never_raises_a_price_change(ledger):
    from arno.jobs.patterns import run_pattern_detection
    from arno.ledger.movements import record_income
    from arno.ledger.payees import create_payee

    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=1_000_000)
    employer = await create_payee(ledger, display_name="Acme Corp", kind="organization")
    for month, amount in (
        (3, 5_000_000),
        (4, 5_000_000),
        (5, 5_000_000),
        (6, 6_000_000),
        (7, 6_000_000),
        (8, 6_000_000),
    ):
        await record_income(
            ledger,
            account_id=bank["id"],
            amount_paise=amount,
            description="Salary",
            payee_id=employer["id"],
            date=date(2026, month, 1),
        )

    rows = await run_pattern_detection(ledger, as_of=date(2026, 8, 20))
    assert not [row for row in rows if row["kind"] == "price_change"]


async def test_a_linked_schedule_with_another_name_and_no_category_is_not_reported_twice(ledger):
    from arno.ledger.payees import create_payee
    from arno.ledger.schedule_payments import link_payment
    from arno.ledger.schedules import create_schedule

    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    insurer = await create_payee(ledger, display_name="Star Health", kind="merchant")
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        subtype="insurance",
        name="Medical Cover",
        amount_paise=100_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=date(2026, 6, 5),
    )
    for month, amount in ((6, 100_000), (7, 100_000), (8, 100_000)):
        payment = await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=amount,
            description="Star Health premium",
            payee_id=insurer["id"],
            date=date(2026, month, 5),
        )
        await link_payment(
            ledger,
            schedule_id=schedule["id"],
            due_date=date(2026, month, 5),
            movement_id=payment["id"],
        )

    # Split one payment across two consecutive occurrences so movement_ids must be deduplicated.
    split_payment = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=240_000,
        description="Star Health two months",
        payee_id=insurer["id"],
        date=date(2026, 9, 5),
    )
    await link_payment(
        ledger,
        schedule_id=schedule["id"],
        due_date=date(2026, 9, 5),
        movement_id=split_payment["id"],
        amount_paise=120_000,
    )
    await link_payment(
        ledger,
        schedule_id=schedule["id"],
        due_date=date(2026, 10, 5),
        movement_id=split_payment["id"],
        amount_paise=120_000,
    )

    proposals = await detect(ledger, as_of=date(2026, 10, 20))
    changes = [p for p in proposals if p["kind"] == "price_change"]
    assert len(changes) == 1
    assert changes[0]["schedule_id"] == schedule["id"]
    assert changes[0]["movement_ids"] == [split_payment["id"]]
    assert not any(p["kind"] == "new_subscription" for p in proposals)


async def test_a_reversed_schedule_payment_is_not_counted_as_a_price_change(ledger):
    from arno.ledger.movements import correct_movement
    from arno.ledger.payees import create_payee
    from arno.ledger.schedule_payments import link_payment
    from arno.ledger.schedules import create_schedule

    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    insurer = await create_payee(ledger, display_name="Star Health", kind="merchant")
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        subtype="insurance",
        name="Star Health",
        amount_paise=100_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=date(2026, 6, 5),
    )
    july_real = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="Star Health",
        payee_id=insurer["id"],
        date=date(2026, 7, 5),
    )
    july_dup = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=50_000,
        description="Star Health duplicate",
        payee_id=insurer["id"],
        date=date(2026, 7, 5),
    )
    await link_payment(
        ledger,
        schedule_id=schedule["id"],
        due_date=date(2026, 7, 5),
        movement_id=july_real["id"],
    )
    await link_payment(
        ledger,
        schedule_id=schedule["id"],
        due_date=date(2026, 7, 5),
        movement_id=july_dup["id"],
    )
    await correct_movement(ledger, july_dup["id"], changes={"delete": True})

    proposals = await detect(ledger, as_of=date(2026, 7, 20))
    assert not [p for p in proposals if p["kind"] == "price_change"]


async def test_a_schedule_price_step_notifies_once_and_dismissing_writes_nothing(ledger):
    from arno.jobs.patterns import run_pattern_detection
    from arno.ledger.payees import create_payee
    from arno.ledger.schedule_payments import link_payment
    from arno.ledger.schedules import create_schedule
    from arno.notifications import mark_read, seed_chat_text

    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    insurer = await create_payee(
        ledger, display_name="Star Health", kind="merchant", outside_text=True
    )
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        subtype="insurance",
        name="Star Health",
        amount_paise=1_200_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=date(2026, 6, 5),
        outside_text=True,
    )
    payments = []
    for month, amount in ((6, 1_200_000), (7, 1_200_000), (8, 1_500_000), (9, 1_500_000)):
        payment = await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=amount,
            description="Star Health premium",
            payee_id=insurer["id"],
            date=date(2026, month, 5),
        )
        await link_payment(
            ledger,
            schedule_id=schedule["id"],
            due_date=date(2026, month, 5),
            movement_id=payment["id"],
        )
        payments.append(payment)

    first = await run_pattern_detection(ledger, as_of=date(2026, 9, 20))
    # The schedule owns the charge, so nothing else is raised for it.
    assert [item["kind"] for item in first] == ["price_change"]
    row = first[0]
    assert row["title"] == "Price change: Star Health"
    assert "₹12,000.00" in row["body"]
    assert "₹15,000.00" in row["body"]
    assert row["data"]["schedule_id"] == schedule["id"]
    assert row["dedupe_key"] == f"pattern:price:{schedule['id']}:2026-08-05"

    text = seed_chat_text(row)
    assert "Star Health" in text
    assert payments[2]["id"] in text

    again = await run_pattern_detection(ledger, as_of=date(2026, 9, 20))
    assert not [item for item in again if item["kind"] == "price_change"]

    audit_rows = await ledger.fetchval("SELECT count(*) FROM audit_log")
    before = await ledger.fetchrow(
        "SELECT amount_paise, updated_at FROM schedules WHERE id = $1", schedule["id"]
    )
    await mark_read(ledger, row["id"])
    assert await ledger.fetchval("SELECT count(*) FROM audit_log") == audit_rows
    after = await ledger.fetchrow(
        "SELECT amount_paise, updated_at FROM schedules WHERE id = $1", schedule["id"]
    )
    assert after == before


async def test_a_schedule_price_step_alerts_only_inside_the_alert_window(ledger):
    from arno.ledger.payees import create_payee
    from arno.ledger.schedule_payments import link_payment
    from arno.ledger.schedules import create_schedule

    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    insurer = await create_payee(ledger, display_name="Star Health", kind="merchant")
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        subtype="insurance",
        name="Star Health",
        amount_paise=100_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=date(2026, 5, 5),
    )
    for month, amount in (
        (5, 100_000),
        (6, 100_000),
        (7, 100_000),
        (8, 120_000),
        (9, 120_000),
        (10, 120_000),
    ):
        payment = await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=amount,
            description="Star Health premium",
            payee_id=insurer["id"],
            date=date(2026, month, 5),
        )
        await link_payment(
            ledger,
            schedule_id=schedule["id"],
            due_date=date(2026, month, 5),
            movement_id=payment["id"],
        )

    # The step is 90 days old: still news.
    inside = await detect(ledger, as_of=date(2026, 11, 3))
    changes = [p for p in inside if p["kind"] == "price_change"]
    assert [p["step_date"] for p in changes] == ["2026-08-05"]

    # One day later the same step is history, so a backfill cannot raise it.
    outside = await detect(ledger, as_of=date(2026, 11, 4))
    assert not [p for p in outside if p["kind"] == "price_change"]


async def test_a_series_that_has_stopped_is_not_proposed(ledger):
    from arno.ledger.payees import create_payee

    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    gym = await create_payee(ledger, display_name="Iron Gym", kind="merchant")
    # Five weekly charges that stopped four months before as_of: the pattern is certain, but it
    # is over.
    for index in range(5):
        await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=49_900,
            description="Iron Gym membership",
            payee_id=gym["id"],
            date=date(2026, 5, 18) + timedelta(days=7 * index),
        )

    proposals = await detect(ledger, as_of=date(2026, 10, 15))
    assert not any(
        proposal["kind"] == "new_subscription" and proposal["payee_id"] == gym["id"]
        for proposal in proposals
    )
    stored = await ledger.fetchrow(
        "SELECT alive, confidence FROM series_detections WHERE payee_id = $1", gym["id"]
    )
    assert stored is not None
    assert stored["confidence"] >= 0.9
    assert stored["alive"] < 0.5


async def test_missed_detection_finds_a_late_schedule_and_preserves_the_flag(db):
    await db.execute(
        """
        INSERT INTO schedules (kind, subtype, name, amount_paise, rule, starts, outside_text)
        VALUES ('obligation', 'rent', 'Rent', 2500000, 'FREQ=MONTHLY;BYMONTHDAY=1', $1, true)
        """,
        ledger_today() - timedelta(days=75),
    )
    proposals = await detect_missed(db, as_of=ledger_today())
    assert proposals
    assert proposals[0]["kind"] == "missed_recurring"
    assert proposals[0]["outside_text"] is True
    assert proposals[0]["days_late"] >= 3


async def test_handle_scheduled_question_builds_a_read_only_agent_that_logs_its_usage(
    db, monkeypatch
):
    """The real job handler builds the agent; only the model is substituted."""
    from arno.agent import builder as builder_mod
    from arno.agent.capabilities import WRITE_TOOLS
    from arno.jobs.questions import handle_scheduled_question

    question = await create_question(
        db,
        prompt="What did I spend on food this week?",
        rule="FREQ=WEEKLY;BYDAY=SU",
    )

    seen_tools: set[str] = set()

    def script(messages, info):
        del messages
        seen_tools.update(tool.name for tool in info.function_tools)
        return ModelResponse(parts=[TextPart("You spent a small amount on food.")])

    model = streaming_model(script)
    # The only substitution is the model factory: build_agent and the whole
    # handle_scheduled_question path stay untouched.
    monkeypatch.setattr(builder_mod, "primary_model", lambda: model)
    monkeypatch.setattr(builder_mod, "backup_model", lambda: None)

    await handle_scheduled_question(question["id"], "impl-2026-11-22")

    assert seen_tools, "the model must see the read toolset"
    assert seen_tools & WRITE_TOOLS == set()
    row = await db.fetchrow(
        "SELECT * FROM notifications WHERE dedupe_key = $1",
        f"scheduled_question:{question['id']}:impl-2026-11-22",
    )
    assert row["kind"] == "scheduled_question"
    assert row["title"] == question["prompt"]
    assert "small amount" in row["body"]
    answer = await db.fetchrow(
        "SELECT last_run_at FROM scheduled_questions WHERE id = $1", question["id"]
    )
    assert answer["last_run_at"] is not None
    assert await db.fetchval("SELECT count(*) FROM llm_usage WHERE role = 'chat'") == 1


def test_question_budget_defaults_and_ceiling_constants():
    from arno.jobs.questions import REQUEST_LIMIT

    assert REQUEST_LIMIT == 20


async def test_a_scheduled_question_ceiling_is_plain_and_marked(ledger):
    question = await create_question(
        ledger, prompt="What did I spend?", rule="FREQ=WEEKLY;BYDAY=SU"
    )

    class CeilingAgent:
        async def run(self, *args, **kwargs):
            del args, kwargs
            raise UsageLimitExceeded("request limit")

    period = "ceiling-2026-11-22"
    result = await run_scheduled_question(
        ledger, question_id=question["id"], period=period, agent=CeilingAgent()
    )
    row = await ledger.fetchrow(
        "SELECT body, data FROM notifications WHERE dedupe_key = $1",
        f"scheduled_question:{question['id']}:{period}",
    )
    assert result["ceiling"] is True
    assert row["data"]["ceiling"] is True
    assert "per-run ceiling" in row["body"]


async def test_scheduled_question_runs_once_per_period(db):
    question = await create_question(db, prompt="What did I spend?", rule="FREQ=WEEKLY;BYDAY=SU")
    calls = {"n": 0}

    def answer(messages, info):
        del messages, info
        calls["n"] += 1
        return ModelResponse(parts=[TextPart("An answer.")])

    agent = build_agent(model=streaming_model(answer), read_only=True)
    first = await run_scheduled_question(
        db, question_id=question["id"], period="once-2026-11-22", agent=agent
    )
    second = await run_scheduled_question(
        db, question_id=question["id"], period="once-2026-11-22", agent=agent
    )
    assert first["notification_id"]
    assert first["ceiling"] is False
    assert second["notification_id"] is None
    assert second["skipped"] == "already answered"
    assert calls["n"] == 1
    assert await db.fetchval("SELECT count(*) FROM notifications") == 1
    row = await db.fetchrow(
        "SELECT data FROM notifications WHERE id = $1", first["notification_id"]
    )
    assert row["data"]["ceiling"] is False
