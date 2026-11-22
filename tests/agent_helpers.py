"""Test helpers: a scripted model that also streams, as the adapter requires."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any, cast

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
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


def step_code(code: str) -> ModelResponse:
    """A model step that runs `code` in the sandbox."""
    return ModelResponse(parts=[ToolCallPart("run_code", {"code": code})])


def step_output(report: Any) -> Callable[[AgentInfo], ModelResponse]:
    """A model step that returns the agent's structured output `report`."""

    def respond(info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, report.model_dump())])

    return respond


def sequence_model(*steps: Any) -> FunctionModel:
    """A scripted model that plays `steps` in order, repeating the last one."""
    calls = {"n": 0}

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages
        index = min(calls["n"], len(steps) - 1)
        calls["n"] += 1
        step = steps[index]
        return step(info) if callable(step) else step

    return FunctionModel(respond)


def streaming_steps(*steps: Any) -> FunctionModel:
    """A scripted model that also streams, as a subagent run requires."""
    calls = {"n": 0}

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages
        index = min(calls["n"], len(steps) - 1)
        calls["n"] += 1
        step = steps[index]
        return step(info) if callable(step) else step

    return streaming_model(script)


def run_context(deps: Any) -> RunContext[Any]:
    """A RunContext double: these calls read only `ctx.deps`."""
    return cast(RunContext[Any], SimpleNamespace(deps=deps))


def tool_returns(messages: list[ModelMessage]) -> str:
    """Every tool return in `messages`, joined, for asserting on what the model saw."""
    chunks: list[str] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                chunks.append(str(part.content))
    return "\n".join(chunks)


def parser_agent(app: Any) -> Any:
    """The parser subagent the app built, so a test can override its model."""
    from pydantic_ai_harness.subagents import SubAgents

    capability = next(
        cap for cap in app.state.agent.root_capability.capabilities if isinstance(cap, SubAgents)
    )
    return next(sub.agent for sub in capability.agents if sub.resolved_name == "parser")
