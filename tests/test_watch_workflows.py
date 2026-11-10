"""Built-in workflows: briefing fallback, reminders, risk episodes, proposals, and questions."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from fin_agent.engine.patterns import Charge, KnownSchedule, detect_missed, detect_recurring
from fin_agent.jobs.briefing import compose_briefing, render_template, send_weekly_briefing
from fin_agent.jobs.questions import run_scheduled_question, spend_budget
from fin_agent.jobs.reminders import send_reminders
from fin_agent.jobs.risk import risk_warning
from fin_agent.ledger.common import today as ledger_today
from fin_agent.scheduled_questions import create_question
from tests.agent_helpers import streaming_model


def _prose_model(text: str) -> FunctionModel:
    # Streamed only: the briefing and scheduled-question runs pass an event stream handler.
    return streaming_model(lambda messages, info: ModelResponse(parts=[TextPart(text)]))


async def test_briefing_numbers_and_template_use_the_ledger_and_project(ledger):
    from fin_agent.ledger import goals as goals_mod

    await goals_mod.create_goal(
        ledger, name="Trip", target_paise=5000000, deadline=date(2027, 3, 1)
    )
    payload = await compose_briefing(ledger, as_of=date(2026, 11, 1))
    assert payload["numbers"]["week"]["from"] == "2026-10-26"
    assert "Your week" in payload["template"]
    assert "90-day chance" in payload["template"] or "crosses your floor" in payload["template"]
    assert render_template(payload["numbers"]) == payload["template"]
    import json

    json.dumps(payload["numbers"])

    sent = await send_weekly_briefing(
        ledger,
        as_of=date(2026, 11, 1),
        period="goal-test",
        model=_prose_model("A goal-aware brief."),
    )
    assert sent is not None
    assert sent["data"]["numbers"]["goals"][0]["deadline"] == "2027-03-01"


async def test_briefing_does_not_report_an_unknown_liquid_balance_as_zero(ledger):
    from fin_agent.ledger.accounts import create_account

    await create_account(ledger, kind="bank", name="SBI Savings")

    payload = await compose_briefing(ledger, as_of=date(2026, 11, 1))

    assert payload["numbers"]["liquid"]["paise"] is None
    assert "Liquid balance: unknown" in payload["template"]


async def test_briefing_keeps_a_movement_only_account_unknown(ledger):
    from fin_agent.ledger.accounts import create_account
    from fin_agent.ledger.movements import record_expense

    account = await create_account(ledger, kind="bank", name="SBI Savings")
    await record_expense(ledger, account_id=account["id"], amount_paise=25_000, description="Lunch")

    payload = await compose_briefing(ledger, as_of=date(2026, 11, 1))

    assert payload["numbers"]["liquid"]["paise"] is None
    assert "Liquid balance: unknown" in payload["template"]


async def test_briefing_does_not_report_an_unknown_card_owed_as_zero(ledger):
    from fin_agent.ledger.accounts import create_account

    await create_account(
        ledger,
        kind="card",
        name="Slice card",
        credit_limit_paise=500_000,
        statement_day=5,
        due_day=25,
    )

    payload = await compose_briefing(ledger, as_of=date(2026, 11, 1))

    assert payload["numbers"]["liquid"]["card_owed_paise"] is None
    assert payload["numbers"]["liquid"]["card_owed"] is None
    assert "Your week" in payload["template"]


async def test_briefing_uses_one_model_call_and_falls_back_to_the_template(db):
    good = await send_weekly_briefing(
        db,
        as_of=date(2026, 11, 1),
        period="2099-W01",
        model=_prose_model("A short brief written from the numbers."),
    )
    assert good is not None
    assert good["body"] == "A short brief written from the numbers."
    assert good["data"]["prose"] == "model"

    async def down(messages: list, info: AgentInfo):
        del messages, info
        raise RuntimeError("endpoint down")
        yield ""  # pragma: no cover

    fallback = await send_weekly_briefing(
        db,
        as_of=date(2026, 11, 1),
        period="2099-W02",
        model=FunctionModel(stream_function=down),
    )
    assert fallback is not None
    assert fallback["data"]["prose"] == "template"
    assert fallback["body"] == fallback["data"]["template"]
    assert "Your week" in fallback["body"]


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


async def test_risk_warns_once_then_rearms_after_recovery(db, monkeypatch):
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

    monkeypatch.setattr("fin_agent.tools.projection.project", fake_project)
    first = await risk_warning(db)
    assert first is not None
    assert first["kind"] == "risk_warning"

    assert await risk_warning(db) is None

    state["floor_probability"] = 0.95
    state["first_floor_crossing"] = None
    assert await risk_warning(db) is None

    state["floor_probability"] = 0.5
    state["first_floor_crossing"] = "2026-11-18"
    third = await risk_warning(db)
    assert third is not None

    rows = await db.fetch("SELECT * FROM notifications WHERE kind = 'risk_warning'")
    assert len(rows) == 2


def test_recurring_detection_proposes_a_new_charge_and_keeps_outside_text():
    charges = [
        Charge(
            movement_id=f"mov_{index}",
            merchant="Netflix",
            normalized="netflix",
            payee_id="pay_1",
            outside_text=True,
            date=date(2026, 8, 1) + timedelta(days=30 * index),
            amount_paise=64900 + (index * 100),
        )
        for index in range(3)
    ]
    proposals = detect_recurring(charges, [])
    assert len(proposals) == 1
    assert proposals[0]["kind"] == "new_subscription"
    assert proposals[0]["interval_days"] == 30
    assert proposals[0]["outside_text"] is True

    matched = detect_recurring(
        charges,
        [KnownSchedule(id="sch_1", name="Netflix", normalized="netflix", amount_paise=64900)],
    )
    assert matched == []


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


async def test_a_scheduled_question_runs_read_only_and_writes_the_inbox(db):
    question = await create_question(
        db,
        prompt="What did I spend on food this week?",
        rule="FREQ=WEEKLY;BYDAY=SU",
        spend_limit_usd="2.50",
    )
    assert spend_budget({"spend_limit_cents": 250}) == 2.5
    assert str(spend_budget({"spend_limit_cents": None})) == "1.00"

    from fin_agent.agent.builder import build_agent

    agent = build_agent(
        model=_prose_model("You spent a small amount on food; the ledger has the detail."),
        read_only=True,
    )
    result = await run_scheduled_question(
        db,
        question_id=question["id"],
        period="2099-01-01T08:00",
        agent=agent,
    )
    assert result["notification_id"]
    assert result["spend_limit_usd"] == "2.50"
    row = await db.fetchrow("SELECT * FROM notifications WHERE id = $1", result["notification_id"])
    assert row["kind"] == "scheduled_question"
    assert row["title"] == question["prompt"]
    assert "small amount" in row["body"]
    answer = await db.fetchrow(
        "SELECT last_run_at FROM scheduled_questions WHERE id = $1", question["id"]
    )
    assert answer["last_run_at"] is not None


def test_question_budget_defaults_and_ceiling_constants():
    from fin_agent.jobs.questions import REQUEST_LIMIT

    assert REQUEST_LIMIT == 20
