"""The daily ceiling stops model work and is recorded as an actual event, not a guess."""

from __future__ import annotations

import asyncio
import warnings
from decimal import Decimal

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.compaction import compact_now
from pydantic_ai_harness.spend import Spent

from fin_agent.agent.builder import build_agent
from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.parser import build_parser_agent


class ExhaustedStore:
    """Every window already spent its ceiling."""

    async def get_many(self, keys):
        return {key: Spent(usd=Decimal("999"), tokens=0, requests=1) for key in keys}

    async def add_many(self, entries):
        return {entry.key: Spent(usd=Decimal("999"), tokens=0, requests=1) for entry in entries}


def _never_called(messages, info: AgentInfo):
    raise AssertionError("the ceiling must stop the model request")


async def test_a_chat_turn_records_the_ceiling_and_does_not_call_the_model(authed_client):
    calls = {"n": 0}

    def script(messages, info: AgentInfo):
        calls["n"] += 1
        return ModelResponse(parts=[TextPart("should not run")])

    agent = build_agent(model=FunctionModel(script), spend_store=ExhaustedStore())
    authed_client.app.state.agent = agent
    authed_client.app.state.turns.agent = agent
    response = authed_client.post(
        "/api/chat",
        json={
            "id": "thread-ceiling",
            "trigger": "submit-message",
            "messages": [
                {"id": "msg-ceiling", "role": "user", "parts": [{"type": "text", "text": "hi"}]}
            ],
        },
    )

    assert response.status_code == 200
    assert calls["n"] == 0


def test_a_parser_run_refuses_at_the_ceiling_and_says_so():
    agent = build_parser_agent(model=FunctionModel(_never_called), spend_store=ExhaustedStore())
    with pytest.raises(Exception) as raised:
        asyncio.run(agent.run("parse", deps=AgentDeps(conversation_id="ceiling")))
    assert type(raised.value).__name__ == "SpendLimitExceeded"


def test_the_cli_production_path_uses_the_shared_daily_ledger(monkeypatch):
    from pydantic_ai.models.test import TestModel

    monkeypatch.setattr("fin_agent.agent.spend.SharedSpendStore", lambda: ExhaustedStore())
    agent = build_agent()
    with agent.override(model=TestModel(call_tools=[], custom_output_text="no")):
        with pytest.raises(Exception) as raised:
            agent.run_sync("hi")
    assert type(raised.value).__name__ == "SpendLimitExceeded"


class RecordingStore:
    """Zeros for every read, and every accrual kept for inspection."""

    def __init__(self) -> None:
        self.entries: list[object] = []

    async def get_many(self, keys):
        return {key: Spent() for key in keys}

    async def add_many(self, entries):
        self.entries.extend(entries)
        return {
            entry.key: Spent(usd=entry.usd, tokens=entry.tokens, requests=entry.requests)
            for entry in entries
        }


def _summarizer(*, model, store):
    """The summarizer the production capability list actually builds."""
    from pydantic_ai_harness.compaction import (
        FallbackCompaction,
        SummarizingCompaction,
        TieredCompaction,
    )

    from fin_agent.agent.capabilities import build_capabilities

    capabilities = build_capabilities(primary=model, spend_store=store, context_window=1_000)
    tiered = next(item for item in capabilities if isinstance(item, TieredCompaction))
    fallback = next(item for item in tiered.tiers if isinstance(item, FallbackCompaction))
    return next(item for item in fallback.fallback_chain if isinstance(item, SummarizingCompaction))


def _long_history():
    history = []
    for index in range(8):
        history += [
            ModelRequest(parts=[UserPromptPart(f"question {index} " + "x" * 200)]),
            ModelResponse(parts=[TextPart(f"answer {index} " + "y" * 200)]),
        ]
    return history


def test_the_summarizer_refuses_at_the_shared_daily_ceiling():
    calls = {"n": 0}

    def script(messages, info: AgentInfo):
        calls["n"] += 1
        raise AssertionError("the ceiling must stop the summary model request")

    model = FunctionModel(script)
    summarizer = _summarizer(model=model, store=ExhaustedStore())

    with pytest.raises(Exception) as raised:
        asyncio.run(compact_now(summarizer, _long_history(), model=model))

    assert type(raised.value).__name__ == "SpendLimitExceeded"
    assert "shared daily" in str(raised.value)
    assert calls["n"] == 0


def test_the_summary_attempt_is_charged_once_to_the_shared_ledger():
    store = RecordingStore()
    model = TestModel(custom_output_text="summary")
    summarizer = _summarizer(model=model, store=store)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        compacted = asyncio.run(compact_now(summarizer, _long_history(), model=model))

    assert compacted
    assert len(store.entries) == 1
    entry = store.entries[0]
    assert entry.key.startswith("shared daily")
    assert entry.requests == 1
    assert entry.tokens > 0


async def test_a_scheduled_question_refuses_at_the_ceiling(db):
    from fin_agent.jobs.questions import answer_question

    question = {"id": "sq_ceiling", "prompt": "What did I spend?", "spend_limit_cents": 250}
    agent = build_agent(
        model=FunctionModel(_never_called),
        read_only=True,
        spend_store=ExhaustedStore(),
        question_budget_usd=Decimal("2.50"),
    )
    answer = await answer_question(db, question, agent=agent)

    assert "ceiling" in answer
