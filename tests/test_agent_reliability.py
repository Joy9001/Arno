"""Spend ceilings in Postgres, the backup model, and the window-limited backup."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic_ai.exceptions import ModelAPIError, ModelContextWindowExceededError
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai_harness.spend import SpendEntry

from fin_agent.agent.builder import build_agent
from fin_agent.agent.capabilities import RUN_MODEL_SETTINGS, turn_limits
from fin_agent.agent.models import WindowLimited
from fin_agent.agent.spend import make_spend_store
from fin_agent.config import get_settings
from tests.agent_helpers import streaming_model


async def test_the_daily_ceiling_survives_a_new_pool(db):
    settings = get_settings(refresh=True)
    store, pool = await make_spend_store(settings.database_url)
    assert store is not None
    key = f"day:{uuid.uuid4()}"
    await store.add_many(
        [
            SpendEntry(
                key=key,
                usd=Decimal("3.25"),
                tokens=100,
                requests=1,
                unpriced=0,
                ttl=timedelta(days=1),
            )
        ]
    )
    await pool.close()

    store2, pool2 = await make_spend_store(settings.database_url)
    assert store2 is not None
    spent = (await store2.get_many([key]))[key]
    assert spent.usd == Decimal("3.25")
    await pool2.close()


def test_the_backup_model_takes_over_without_sticking(demo_ledger):
    def failing(messages, info: AgentInfo) -> ModelResponse:
        raise ModelAPIError("primary", "provider is down")

    def backup(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("from the backup")])

    agent = build_agent(model=FunctionModel(failing), backup=FunctionModel(backup))
    result = agent.run_sync("hi")
    assert result.output == "from the backup"


def test_a_backup_too_small_refuses_before_sending():
    from pydantic_ai.models.function import FunctionModel as FM

    def never(messages, info):  # pragma: no cover - should be refused first
        raise AssertionError("the wrapped model was called")

    limited = WindowLimited(FM(never), 10)
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart("word " * 1000)])]
    with pytest.raises(ModelContextWindowExceededError):
        import asyncio

        asyncio.run(
            limited.request(
                messages,
                None,
                __import__(
                    "pydantic_ai.models", fromlist=["ModelRequestParameters"]
                ).ModelRequestParameters(),
            )
        )


def test_the_turn_ceilings_are_wide_but_finite():
    limits = turn_limits()
    assert limits.request_limit == 50
    assert limits.tool_calls_limit == 100
    assert RUN_MODEL_SETTINGS["first_event_timeout"] == 60
    assert RUN_MODEL_SETTINGS["request_timeout"] == 180


def test_a_backup_reply_carries_a_badge_and_records_the_error(authed_client, demo_ledger):
    def failing(messages, info: AgentInfo) -> ModelResponse:
        raise ModelAPIError("primary", "provider is down")

    backup = streaming_model(
        lambda messages, info: ModelResponse(parts=[TextPart("from the backup")])
    )
    agent = build_agent(model=streaming_model(failing), backup=backup)
    authed_client.app.state.agent = agent
    authed_client.app.state.turns.agent = agent
    response = authed_client.post(
        "/api/chat",
        json={
            "id": "thread-backup",
            "trigger": "submit-message",
            "messages": [
                {"id": "client-backup", "role": "user", "parts": [{"type": "text", "text": "hi"}]}
            ],
        },
    )
    assert response.status_code == 200
    thread = authed_client.get("/api/threads/thread-backup/messages").json()
    parts = [part for message in thread["messages"] for part in message.get("parts", [])]
    assert any(part.get("type") == "data-model-badge" for part in parts)
    row = demo_ledger.execute("SELECT last_model_error_type FROM settings WHERE id = 1").fetchone()
    assert row["last_model_error_type"]
