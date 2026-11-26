"""The backup model and the turn ceilings."""

from __future__ import annotations

from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from arno.agent.builder import build_agent
from arno.agent.capabilities import (
    MODEL_SETTINGS,
    STALL_SECONDS,
    TURN_LIMITS,
    trim_old_tool_payloads,
)
from arno.agent.deps import AgentDeps
from tests.agent_helpers import run_context, streaming_model

_CTX = run_context(AgentDeps(conversation_id="cnv_trim"))


async def test_the_backup_model_takes_over_without_sticking(demo_ledger):
    calls = {"primary": 0, "backup": 0}

    def primary(messages, info: AgentInfo) -> ModelResponse:
        calls["primary"] += 1
        if calls["primary"] == 1:
            raise ModelAPIError("primary", "provider is down")
        return ModelResponse(parts=[TextPart("from the primary")])

    def backup(messages, info: AgentInfo) -> ModelResponse:
        calls["backup"] += 1
        # Keep the run to two model requests: the backup calls a tool, and the next request
        # must go back to the primary rather than stay stuck on the backup.
        return ModelResponse(parts=[ToolCallPart("get_financial_context", {}, "call_ctx_backup")])

    agent = build_agent(model=FunctionModel(primary), backup=FunctionModel(backup))
    result = await agent.run("hi", deps=AgentDeps(conversation_id="cnv_backup"))
    assert result.output == "from the primary"
    assert calls == {"primary": 2, "backup": 1}, calls


def test_the_turn_ceilings_are_wide_but_finite():
    assert TURN_LIMITS.request_limit == 25
    # No total-time limit: a request that sends nothing for ten minutes is the only cut-off.
    assert MODEL_SETTINGS["timeout"] == STALL_SECONDS
    assert "request_timeout" not in MODEL_SETTINGS
    assert "first_event_timeout" not in MODEL_SETTINGS


def _thread(input_tokens: int) -> list:
    """A long thread: a read return, a write return, then filler turns."""
    messages: list = [
        ModelRequest(parts=[UserPromptPart("hi")]),
        ModelResponse(parts=[ToolCallPart("get_financial_context", {}, "c1")]),
        ModelRequest(parts=[ToolReturnPart("get_financial_context", {"big": "x" * 50}, "c1")]),
        ModelResponse(parts=[ToolCallPart("record_movements", {}, "c2")]),
        ModelRequest(parts=[ToolReturnPart("record_movements", {"id": "mov_1"}, "c2")]),
    ]
    for _ in range(6):
        messages += [
            ModelResponse(parts=[TextPart("ok")], usage=RequestUsage(input_tokens=input_tokens)),
            ModelRequest(parts=[UserPromptPart("more")]),
        ]
    return messages


def _returns(messages: list) -> dict[str, str]:
    return {
        part.tool_name: str(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }


def test_old_read_returns_are_trimmed_only_near_the_context_limit():
    unchanged = trim_old_tool_payloads(_CTX, _thread(100), window=1_000)
    assert "x" * 50 in _returns(unchanged)["get_financial_context"]

    trimmed = trim_old_tool_payloads(_CTX, _thread(900), window=1_000)
    returns = _returns(trimmed)
    assert "removed" in returns["get_financial_context"]
    # A write's return says what changed, so it stays.
    assert "mov_1" in returns["record_movements"]


async def test_a_backup_reply_carries_a_badge_and_records_the_error(session_client, demo_ledger):
    def failing(messages, info: AgentInfo) -> ModelResponse:
        raise ModelAPIError("primary", "provider is down")

    backup = streaming_model(
        lambda messages, info: ModelResponse(parts=[TextPart("from the backup")])
    )
    agent = build_agent(model=streaming_model(failing), backup=backup)
    session_client.app.state.agent = agent
    session_client.app.state.turns.agent = agent
    response = session_client.post(
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
    thread = session_client.get("/api/threads/thread-backup/messages").json()
    parts = [part for message in thread["messages"] for part in message.get("parts", [])]
    assert any(part.get("type") == "data-model-badge" for part in parts)
    row = await demo_ledger.fetchrow("SELECT last_model_error_type FROM settings WHERE id = 1")
    assert row["last_model_error_type"]
