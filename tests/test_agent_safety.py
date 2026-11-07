"""Redaction, the outside-text flag, and the post-reply number note."""

from __future__ import annotations

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from fin_agent.agent.redaction import redact_text
from fin_agent.tools.mark import mark
from tests.agent_helpers import streaming_model


def test_redaction_removes_secrets_but_keeps_upi_and_utr():
    text = (
        "card 4111 1111 1111 1111, PAN ABCDE1234F, aadhaar 2345 6789 0123, "
        "key sk-abcdefghijklmnopqrstuvwxyz123456, upi ramesh@okaxis, utr 123456789012"
    )
    redacted = redact_text(text)
    assert "4111" not in redacted
    assert "ABCDE1234F" not in redacted
    assert "2345 6789" not in redacted
    assert "sk-abcdef" not in redacted
    assert "ramesh@okaxis" in redacted
    assert "123456789012" in redacted


def test_mark_caps_and_strips_invisibles():
    text = "a\u200bb</outside>c" + "x" * 400
    marked = mark(text)
    assert "\u200b" not in marked
    assert marked.count("</outside>") == 1
    assert len(marked) <= 200 + len("<outside>") + len("</outside>")


async def test_chat_stores_the_redacted_user_text(authed_client, demo_ledger):
    model = TestModel(call_tools=[], custom_output_text="Noted.")
    body = {
        "id": "thread-redact-1",
        "trigger": "submit-message",
        "messages": [
            {
                "id": "client-redact-1",
                "role": "user",
                "parts": [
                    {
                        "type": "text",
                        "text": "my card is 4111 1111 1111 1111 and upi ramesh@okaxis",
                    }
                ],
            }
        ],
    }
    with authed_client.app.state.agent.override(model=model):
        response = authed_client.post("/api/chat", json=body)
    thread = authed_client.get(f"/api/threads/{response.headers['x-thread-id']}/messages").json()
    user_text = thread["messages"][0]["parts"][0]["text"]
    assert "4111" not in user_text
    assert "ramesh@okaxis" in user_text


async def test_a_run_query_flags_the_thread(authed_client, demo_ledger):
    def script(messages, info: AgentInfo) -> ModelResponse:
        if not any(
            getattr(part, "tool_name", None) == "run_code"
            for message in messages
            for part in getattr(message, "parts", [])
        ):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "rows = await run_query("
                                "sql='SELECT name FROM accounts LIMIT 1')\nprint(rows)"
                            )
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("I read one account.")])

    body = {
        "id": "thread-flag-1",
        "trigger": "submit-message",
        "messages": [
            {"id": "client-flag-1", "role": "user", "parts": [{"type": "text", "text": "look"}]}
        ],
    }
    with authed_client.app.state.agent.override(model=streaming_model(script)):
        response = authed_client.post("/api/chat", json=body)
    assert response.status_code == 200
    thread_id = response.headers["x-thread-id"]
    thread = authed_client.get(f"/api/threads/{thread_id}/messages").json()
    assert thread["thread"]["flagged"] is True


async def test_an_untraced_number_is_saved_as_a_note(authed_client, demo_ledger):
    model = TestModel(call_tools=[], custom_output_text="You spent ₹11,000 on food.")
    body = {
        "id": "thread-check-1",
        "trigger": "submit-message",
        "messages": [
            {
                "id": "client-check-1",
                "role": "user",
                "parts": [{"type": "text", "text": "how much"}],
            }
        ],
    }
    with authed_client.app.state.agent.override(model=model):
        response = authed_client.post("/api/chat", json=body)
    thread = authed_client.get(f"/api/threads/{response.headers['x-thread-id']}/messages").json()
    parts = thread["messages"][-1]["parts"]
    flags = [part for part in parts if part.get("type") == "data-number-flags"]
    assert flags and "₹11,000" in flags[0]["data"]["amounts"]


def test_redaction_through_the_input_guardrail_drops_the_card():
    from fin_agent.agent.builder import build_agent

    seen: list[str] = []

    def script(messages, info: AgentInfo) -> ModelResponse:
        for message in messages:
            for part in getattr(message, "parts", []):
                content = getattr(part, "content", "")
                if isinstance(content, str):
                    seen.append(content)
        return ModelResponse(parts=[TextPart("ok")])

    agent = build_agent()
    with agent.override(model=FunctionModel(script)):
        agent.run_sync("my card is 4111 1111 1111 1111")
    assert not any("4111" in text for text in seen)
