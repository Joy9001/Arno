"""Test helpers: a scripted model that also streams, as the adapter requires."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel


def streaming_model(
    script: Callable[[list[Any], AgentInfo], ModelResponse],
    *,
    text_chunks: int = 1,
    delay: float = 0.0,
) -> FunctionModel:
    async def stream(
        messages: list[Any], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        response = script(messages, info)
        for index, part in enumerate(response.parts):
            if isinstance(part, TextPart):
                size = max(1, len(part.content) // max(text_chunks, 1)) or 1
                for start in range(0, len(part.content), size):
                    if delay:
                        await asyncio.sleep(delay)
                    yield part.content[start : start + size]
            elif isinstance(part, ToolCallPart):
                yield {
                    index: DeltaToolCall(
                        name=part.tool_name,
                        json_args=json.dumps(part.args),
                        tool_call_id=part.tool_call_id or f"call_{index}",
                    )
                }

    return FunctionModel(stream_function=stream)
