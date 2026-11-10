"""Regressions found while making the agent, API, and auth modules type-check.

Each test covers a runtime bug the checker pointed at, not the annotation that hid it.
"""

from __future__ import annotations

import base64

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel

from fin_agent.agent.models import WindowLimited
from fin_agent.api.run_view import build_run
from fin_agent.notifications import ensure_vapid_keys


async def test_the_window_limited_model_streams_like_the_model_it_wraps():
    """The backup model streams: an async generator over the wrapped stream raised TypeError."""
    model = WindowLimited(TestModel(custom_output_text="fine"), 1_000)
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart("hello")])]

    async with model.request_stream(messages, None, ModelRequestParameters()) as stream:
        events = [part async for part in stream]

    assert events, "a streamed request yields the wrapped model's stream"


def test_a_retried_script_step_reads_as_failed():
    """A `run_code` retry is a tool return marked retried; its text is the error, not a result."""
    messages = [
        ModelResponse(
            parts=[ToolCallPart("run_code", {"code": "await nothing_here()"}, tool_call_id="c1")]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "run_code",
                    "Type error in code: name 'nothing_here' is not defined",
                    tool_call_id="c1",
                    outcome="retried",
                )
            ]
        ),
    ]
    events = [
        {
            "run_id": "run_a",
            "seq": 1,
            "kind": "tool_call_started",
            "tool_name": "run_code",
            "tool_call_id": "c1",
            "timestamp": "2026-11-08T10:00:01+00:00",
            "error": None,
        }
    ]

    run = build_run(events=events, messages=messages, running=False, outcome="ok", limit=120)

    assert run["steps"][0]["status"] == "failed"
    assert "Type error" in (run["steps"][0]["error"] or "")


async def test_vapid_keys_are_a_matching_pair(ledger):
    """Generating the key ourselves still stores a public key that matches the private one."""
    from cryptography.hazmat.primitives import serialization

    row = await ensure_vapid_keys(ledger)
    private_key = serialization.load_pem_private_key(row["private_key"].encode(), password=None)
    point = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    assert base64.urlsafe_b64encode(point).decode("ascii").rstrip("=") == row["public_key"]
    assert len(point) == 65 and point[0] == 4
