"""A scripted model for the categorizer: answers every payee key in its prompt."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from tests.agent_helpers import streaming_model

MARK = "Payees (JSON data): "


def prompt_payees(messages: list[Any]) -> list[dict[str, Any]]:
    prompt = ""
    for message in messages:
        for part in message.parts:
            content = getattr(part, "content", None)
            if isinstance(content, str) and MARK in content:
                prompt = content
    return json.loads(prompt.split(MARK, 1)[1])


def answering(
    category: str = "Shopping",
    confidence: str = "high",
    *,
    per_key: dict[str, tuple[str, str]] | None = None,
    chunks: list[int] | None = None,
    fail_when: Callable[[list[str]], bool] | None = None,
) -> FunctionModel:
    """Answer each asked key with `(category, confidence)`, or its `per_key` entry.

    `chunks` collects each call's payee count; `fail_when(keys)` makes that call raise.
    """

    def respond(messages: list[Any], info: AgentInfo) -> ModelResponse:
        keys = [item["key"] for item in prompt_payees(messages)]
        if chunks is not None:
            chunks.append(len(keys))
        if fail_when is not None and fail_when(keys):
            raise RuntimeError("provider timeout")
        answers = []
        for key in keys:
            chosen, sure = (per_key or {}).get(key, (category, confidence))
            answers.append({"key": key, "category": chosen, "confidence": sure})
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"response": answers})])

    return streaming_model(respond)
