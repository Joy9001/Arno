"""The `llm_usage` log and the shared daily cap."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from arno.agent.builder import build_agent
from arno.agent.usage import (
    INDIA,
    DailyBudgetExceeded,
    check_daily_budget,
    record_usage,
    usage_hooks,
)


async def _spend(db, usd: str, *, at: datetime | None = None) -> None:
    await db.execute(
        "INSERT INTO llm_usage (created_at, role, model, input_tokens, output_tokens, usd)"
        " VALUES (coalesce($2, now()), 'chat', 'm', 0, 0, $1)",
        Decimal(usd),
        at,
    )


def _never_called(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    raise AssertionError("the daily limit must stop the model request")


async def test_the_budget_stops_at_the_limit_and_ignores_yesterday(db):
    midnight = datetime.now(INDIA).replace(hour=0, minute=0, second=0, microsecond=0)
    await _spend(db, "50", at=midnight - timedelta(minutes=1))
    await _spend(db, "19.5")
    await check_daily_budget(db)

    await _spend(db, "0.5")
    with pytest.raises(DailyBudgetExceeded, match="daily spend limit"):
        await check_daily_budget(db)
    await check_daily_budget(db, Decimal("21"))


async def test_an_unpriced_response_is_logged_at_zero_with_a_warning(db, caplog):
    with caplog.at_level(logging.WARNING, logger="arno.usage"):
        await record_usage(
            db, "chat", "mystery", RequestUsage(input_tokens=7, output_tokens=3), None
        )

    row = await db.fetchrow_required("SELECT * FROM llm_usage")
    assert (row["model"], row["input_tokens"], row["output_tokens"], row["usd"]) == (
        "mystery",
        7,
        3,
        0,
    )
    assert "mystery" in caplog.text


async def test_each_model_request_writes_one_row(db):
    calls = {"n": 0}

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart("ping", {}, "call_1")])
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(FunctionModel(script), capabilities=[usage_hooks("chat")])

    @agent.tool_plain
    def ping() -> str:
        return "pong"

    assert (await agent.run("hi")).output == "done"

    rows = await db.fetch("SELECT role, input_tokens FROM llm_usage")
    assert [row["role"] for row in rows] == ["chat", "chat"]
    assert all(row["input_tokens"] > 0 for row in rows)


async def test_the_hook_blocks_a_request_once_the_day_is_spent(db):
    await _spend(db, "20")
    agent = Agent(FunctionModel(_never_called), capabilities=[usage_hooks("chat")])

    with pytest.raises(DailyBudgetExceeded):
        await agent.run("hi")
    assert await db.fetchval("SELECT count(*) FROM llm_usage") == 1


async def test_a_chat_turn_at_the_limit_does_not_call_the_model(session_client, db):
    await _spend(db, "20")
    agent = build_agent(model=FunctionModel(_never_called))
    session_client.app.state.agent = agent
    session_client.app.state.turns.agent = agent

    response = session_client.post(
        "/api/chat",
        json={
            "id": "thread-limit",
            "trigger": "submit-message",
            "messages": [
                {"id": "msg-limit", "role": "user", "parts": [{"type": "text", "text": "hi"}]}
            ],
        },
    )

    assert response.status_code == 200


async def test_a_scheduled_question_stops_at_the_limit(db):
    from arno.jobs.questions import answer_question

    await _spend(db, "20")
    agent = build_agent(model=FunctionModel(_never_called), read_only=True)

    with pytest.raises(DailyBudgetExceeded):
        await answer_question(db, {"id": "sq_limit", "prompt": "What did I spend?"}, agent=agent)


async def test_vision_propagates_a_cost_stop(monkeypatch):
    from arno.importers.readers import Doc
    from arno.importers.vision import transcribe_page
    from tests.import_fixtures import minimal_pdf

    async def fail(messages, info):
        raise DailyBudgetExceeded("synthetic")

    monkeypatch.setattr("arno.importers.vision.vision_model", lambda: FunctionModel(fail))
    with pytest.raises(DailyBudgetExceeded):
        await transcribe_page(Doc(minimal_pdf(["synthetic page"])))
