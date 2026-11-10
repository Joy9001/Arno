"""Spend ceilings in Postgres, the backup model, and the window-limited backup."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic_ai.exceptions import ModelAPIError, ModelContextWindowExceededError
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai_harness.spend import SpendEntry

from fin_agent.agent.builder import build_agent
from fin_agent.agent.capabilities import MODEL_SETTINGS, STALL_SECONDS, turn_limits
from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.models import WindowLimited
from fin_agent.agent.spend import _SingleConnectionPool, make_spend_store
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


class _FakeConnection:
    def __init__(self) -> None:
        self.terminated = 0
        self._closed = False

    def is_closed(self) -> bool:
        return self._closed

    def terminate(self) -> None:
        self.terminated += 1
        self._closed = True


def test_the_cli_connection_is_replaced_across_loops_and_closed_explicitly(monkeypatch):
    created: list[_FakeConnection] = []

    async def connect(dsn, **kwargs):
        del dsn, kwargs
        connection = _FakeConnection()
        created.append(connection)
        return connection

    monkeypatch.setattr("fin_agent.agent.spend.asyncpg.connect", connect)
    pool = _SingleConnectionPool("postgresql://unused")

    async def use():
        async with pool.acquire() as connection:
            return connection

    first = asyncio.run(use())
    second = asyncio.run(use())
    assert len(created) == 2
    assert first.terminated == 1
    assert second.terminated == 0

    pool.close()
    assert second.terminated == 1
    pool.close()
    assert second.terminated == 1


async def test_the_backup_model_takes_over_without_sticking(demo_ledger):
    def failing(messages, info: AgentInfo) -> ModelResponse:
        raise ModelAPIError("primary", "provider is down")

    def backup(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("from the backup")])

    agent = build_agent(model=FunctionModel(failing), backup=FunctionModel(backup))
    result = await agent.run("hi", deps=AgentDeps(conversation_id="cnv_backup"))
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
    # No total-time limit: a request that sends nothing for ten minutes is the only cut-off.
    assert MODEL_SETTINGS["timeout"] == STALL_SECONDS
    assert "request_timeout" not in MODEL_SETTINGS
    assert "first_event_timeout" not in MODEL_SETTINGS


async def test_a_backup_reply_carries_a_badge_and_records_the_error(authed_client, demo_ledger):
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
    row = await demo_ledger.fetchrow("SELECT last_model_error_type FROM settings WHERE id = 1")
    assert row["last_model_error_type"]
