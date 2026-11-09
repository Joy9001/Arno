"""Audit regressions, wave 2: the ceiling mark, a photo retried once, a spaced UTR, brief coverage.

The photo tests swap in a typed extraction; model requests stay blocked by `conftest.py`.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, TextPart

from fin_agent.agent.builder import build_agent
from fin_agent.api.capture import _clean_upi_ref
from fin_agent.capture.photo import PaymentPhoto
from fin_agent.jobs.briefing import compose_briefing, send_weekly_briefing
from fin_agent.jobs.questions import run_scheduled_question
from fin_agent.ledger import movements as movements_mod
from fin_agent.ledger.accounts import create_account
from fin_agent.scheduled_questions import create_question
from tests.agent_helpers import streaming_model

PNG = b"\x89PNG\r\n\x1a\n" + b"audit2-image"


# Scheduled-question ceiling -----------------------------------------------------------------


async def test_a_scheduled_question_ceiling_is_marked_on_its_inbox_item(ledger):
    question = await create_question(
        ledger, prompt="What did I spend?", rule="FREQ=WEEKLY;BYDAY=SU", spend_limit_usd="2"
    )

    class CeilingAgent:
        async def run(self, *args, **kwargs):
            del args, kwargs
            raise UsageLimitExceeded("request limit")

    period = "watch2-ceiling"
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


async def test_an_ordinary_scheduled_answer_is_not_marked_as_a_ceiling(db):
    question = await create_question(db, prompt="What did I spend?", rule="FREQ=WEEKLY;BYDAY=SU")

    def answers(messages, info):
        del messages, info
        return ModelResponse(parts=[TextPart("An answer.")])

    period = "watch2-answer"
    result = await run_scheduled_question(
        db,
        question_id=question["id"],
        period=period,
        agent=build_agent(model=streaming_model(answers), read_only=True),
    )
    row = await db.fetchrow(
        "SELECT data FROM notifications WHERE dedupe_key = $1",
        f"scheduled_question:{question['id']}:{period}",
    )
    assert result["ceiling"] is False
    assert row["data"]["ceiling"] is False


# A UPI reference printed with spaces ---------------------------------------------------------


def test_a_upi_reference_printed_with_spaces_is_accepted_and_cleaned():
    photo = PaymentPhoto(
        merchant="Swiggy",
        amount_paise=45_000,
        method="upi",
        upi_ref="4145 1234 5678",
    )
    assert _clean_upi_ref(photo.upi_ref) == "414512345678"
    # The pattern still rejects what a reference can never contain.
    with pytest.raises(ValidationError):
        PaymentPhoto(upi_ref="4145;1234")


async def test_retrying_a_logged_photo_appends_the_note_once(
    authed_client, demo_ledger, monkeypatch
):
    async def extraction(*_args, **_kwargs) -> PaymentPhoto:
        return PaymentPhoto(
            merchant="Watch2 Cafe",
            amount_paise=45_000,
            method="upi",
            upi_ref="4145 1234 5678",
        )

    monkeypatch.setattr("fin_agent.api.capture.extract_payment_photo", extraction)
    first = authed_client.post(
        "/api/chat/cnv_watch2_retry/photo",
        files={"file": ("receipt.png", PNG, "image/png")},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "logged"
    assert body["card"]["upi_ref"] == "414512345678"

    movements_before = await demo_ledger.fetchval("SELECT count(*) FROM movements")
    again = authed_client.post(f"/api/chat/cnv_watch2_retry/photo/{body['attachment_id']}")
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "logged"
    assert again.json()["movement"]["id"] == body["movement"]["id"]

    # One photo, one movement, one note: a retry adds neither money nor model context.
    assert await demo_ledger.fetchval("SELECT count(*) FROM movements") == movements_before
    assert await demo_ledger.fetchval("SELECT count(*) FROM captures") == 1
    assert (
        await demo_ledger.fetchval(
            "SELECT count(*) FROM movements WHERE description = 'Watch2 Cafe'"
        )
        == 1
    )
    movement = await movements_mod.get_movement(demo_ledger, body["movement"]["id"])
    assert movement["amount_paise"] == 45_000
    history = json.dumps(
        await demo_ledger.fetchval(
            "SELECT model_history FROM conversations WHERE id = 'cnv_watch2_retry'"
        )
    )
    assert history.count("The server extracted") == 1
    assert await demo_ledger.fetchval(
        "SELECT history_at IS NOT NULL FROM captures WHERE ref = $1", body["attachment_id"]
    )


# The briefing says when statement coverage is incomplete -------------------------------------


async def test_briefing_says_the_spend_total_is_incomplete_without_a_statement(ledger):
    account = await create_account(ledger, kind="bank", name="Watch2 Bank")
    await movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=120_050,
        description="Groceries",
        date=date(2026, 10, 28),
    )
    payload = await compose_briefing(ledger, as_of=date(2026, 11, 1))
    spent = payload["numbers"]["spent"]
    assert spent["coverage"]["complete"] is False
    assert spent["coverage"]["note"]
    assert spent["coverage"]["note"] in payload["template"]
    assert "₹1,200.50" in payload["template"]

    def honest(messages, info):
        del messages, info
        return ModelResponse(
            parts=[
                TextPart(
                    "You spent ₹1,200.50 this week. Statement coverage is incomplete for this "
                    "week, so that is the total of the entries recorded, not of every payment."
                )
            ]
        )

    sent = await send_weekly_briefing(
        ledger,
        as_of=date(2026, 11, 1),
        period="watch2-incomplete",
        model=streaming_model(honest),
    )
    assert sent is not None
    assert sent["data"]["prose"] == "model"
    assert sent["data"]["numbers"]["spent"]["coverage"]["complete"] is False


async def test_briefing_carries_complete_statement_coverage(ledger):
    account = await create_account(ledger, kind="bank", name="Watch2 Bank")
    await ledger.execute(
        """
        INSERT INTO imports (file_hash, status, account_id, period_start, period_end)
        VALUES ('watch2-hash', 'committed', $1, $2, $3)
        """,
        account["id"],
        date(2026, 10, 1),
        date(2026, 10, 31),
    )
    await movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=120_050,
        description="Groceries",
        date=date(2026, 10, 16),
    )
    payload = await compose_briefing(ledger, as_of=date(2026, 10, 20))
    spent = payload["numbers"]["spent"]
    assert spent["coverage"]["complete"] is True
    assert spent["coverage"]["note"] is None
    assert "Statement coverage is incomplete" not in payload["template"]
