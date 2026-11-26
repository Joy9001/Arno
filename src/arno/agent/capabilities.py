"""Capabilities and the history processor the agent runs with.

Phoenix trace attributes (`RootSpanIO`, `InferenceContext`), tool errors that become a result the
model can explain, a history processor that trims old tool payloads near the context limit, and
the model settings and usage limits every run shares.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterable
from dataclasses import replace
from typing import Any, cast, override

from pydantic import BaseModel
from pydantic_ai import ModelSettings, RunContext, UsageLimits
from pydantic_ai.capabilities import AbstractCapability, WrapRunHandler
from pydantic_ai.messages import (
    AgentStreamEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext

from arno.agent.deps import AgentDeps
from arno.agent.models import DEFAULT_CONTEXT_WINDOW
from arno.agent.usage import COST_STOP_ERRORS

logger = logging.getLogger("arno.agent")

# Tools whose returns record what changed, so trimming never drops them.
WRITE_TOOLS = frozenset(
    {
        "record_movements",
        "correct_movement",
        "update_setup",
        "undo",
    }
)
TRIM_AT = 0.7
KEEP_RECENT = 10
TRIMMED = "[Earlier tool result removed to save space. Read it again if you need it.]"


def trim_old_tool_payloads(
    ctx: RunContext[Any], messages: list[ModelMessage], *, window: int | None = None
) -> list[ModelMessage]:
    """When the last request nears the context window, blank older read-tool returns."""
    limit = window or int(os.environ.get("LLM_CONTEXT_WINDOW", DEFAULT_CONTEXT_WINDOW))
    used = next(
        (
            message.usage.input_tokens
            for message in reversed(messages)
            if isinstance(message, ModelResponse) and message.usage.input_tokens
        ),
        0,
    )
    if used < limit * TRIM_AT:
        return messages
    cutoff = len(messages) - KEEP_RECENT
    trimmed: list[ModelMessage] = []
    for index, message in enumerate(messages):
        if index < cutoff and isinstance(message, ModelRequest):
            parts = [
                replace(cast(Any, part), content=TRIMMED)
                if isinstance(part, ToolReturnPart) and part.tool_name not in WRITE_TOOLS
                else part
                for part in message.parts
            ]
            message = replace(message, parts=parts)
        trimmed.append(message)
    return trimmed


def _content_text(content: Any) -> str:
    """Join the text items of a prompt, skipping files and other non-text content."""
    if isinstance(content, str):
        return content.strip()
    texts: list[str] = []
    for item in content:
        text = item if isinstance(item, str) else getattr(item, "content", None)
        if isinstance(text, str):
            texts.append(text)
    return "\n".join(texts).strip()


def _current_user_text(ctx: RunContext[Any]) -> str | None:
    """The prompt this run is answering."""
    if ctx.prompt is not None:
        text = _content_text(ctx.prompt)
        if text:
            return text
    # The chat adapter passes the new turn in `message_history` instead of `prompt`.
    for message in reversed(ctx.messages):
        if not isinstance(message, ModelRequest):
            continue
        for part in reversed(message.parts):
            if isinstance(part, UserPromptPart):
                text = _content_text(part.content)
                if text:
                    return text
    return None


class RootSpanIO(AbstractCapability[Any]):
    """Put the current message, final reply, and approval counts on the native `invoke_agent` span.

    Phoenix's trace list reads `input.value`/`output.value`; Pydantic AI's root span carries the
    full history and `final_result` but not those two view attributes. This capability writes only
    those attributes, plus the numeric `arno.approvals.offered` and `arno.approvals.approved` tap
    counts, on the existing span, inside the instrumentation's outermost layer. The counts are
    recorded even when content is off.
    """

    @override
    async def wrap_run(self, ctx: RunContext[Any], *, handler: WrapRunHandler) -> Any:
        from opentelemetry.trace import get_current_span

        span = get_current_span()
        if span.is_recording():
            # Identity is attribution, not message content: record it even when content is off.
            # The API passes the opaque authenticated principal; CLI and scheduled runs use the
            # one local actor this single-user installation has.
            span.set_attribute("user.id", getattr(ctx.deps, "user_id", None) or "local-user")
            context = span.get_span_context()
            if isinstance(ctx.deps, AgentDeps) and context.is_valid:
                # The saved reply carries its trace, so Flag can open the run that wrote it.
                ctx.deps.trace_id = format(context.trace_id, "032x")
        if ctx.trace_include_content and span.is_recording():
            text = _current_user_text(ctx)
            if text:
                span.set_attribute("input.value", text)
                span.set_attribute("input.mime_type", "text/plain")
        try:
            result = await handler()
        finally:
            # A tap still counts when the approved write fails and ends the run.
            if isinstance(ctx.deps, AgentDeps) and span.is_recording():
                span.set_attribute("arno.approvals.offered", ctx.deps.approvals_offered)
                span.set_attribute("arno.approvals.approved", ctx.deps.approvals)
        if ctx.trace_include_content and span.is_recording():
            output = result.output
            # A background agent's typed report (a parse or categorize report) reads as its own
            # summary line, so its session shows a last output too.
            text = str(output) if isinstance(output, BaseModel) else output
            if isinstance(text, str) and text.strip():
                span.set_attribute("output.value", text)
                span.set_attribute("output.mime_type", "text/plain")
        return result


class InferenceContext(AbstractCapability[Any]):
    """Stamp the conversation id on the run span, so a summary shares its conversation's session."""

    @override
    async def wrap_run(self, ctx: RunContext[Any], *, handler: WrapRunHandler) -> Any:
        from opentelemetry.trace import get_current_span

        session_id = getattr(ctx.deps, "conversation_id", None) or ctx.conversation_id
        if session_id:
            span = get_current_span()
            span.set_attribute("session.id", session_id)
            span.set_attribute("gen_ai.conversation.id", session_id)
        return await handler()


class ToolErrorsToModel(AbstractCapability[AgentDeps]):
    """A tool that throws something unexpected becomes a failed result the model can explain."""

    def __init__(self) -> None:
        self._cost_stop: Exception | None = None

    @override
    async def for_run(self, ctx: RunContext[AgentDeps]) -> ToolErrorsToModel:
        return ToolErrorsToModel()

    @override
    async def wrap_run(self, ctx: RunContext[AgentDeps], *, handler: WrapRunHandler) -> Any:
        try:
            result = await handler()
        except Exception:
            if self._cost_stop is not None:
                raise self._cost_stop from None
            raise
        if self._cost_stop is not None:
            raise self._cost_stop
        return result

    @override
    async def before_model_request(
        self, ctx: RunContext[AgentDeps], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        if self._cost_stop is not None:
            raise self._cost_stop
        return request_context

    @override
    async def before_tool_execute(
        self, ctx: RunContext[AgentDeps], *, call: Any, tool_def: Any, args: Any
    ) -> Any:
        if self._cost_stop is not None:
            raise self._cost_stop
        return args

    @override
    async def on_tool_execute_error(
        self,
        ctx: RunContext[AgentDeps],
        *,
        call: Any,
        tool_def: Any,
        args: Any,
        error: Exception,
    ) -> Any:
        # A budget or accounting stop is not a tool hiccup: retrying it or describing it back to
        # the model would spend again on a call that already failed, so it ends the run.
        if isinstance(error, COST_STOP_ERRORS):
            self._cost_stop = error
            raise error
        logger.error("tool %s failed (%s)", call.tool_name, type(error).__name__)
        return {
            "status": "error",
            "tool": call.tool_name,
            "message": "The tool failed. Tell the user plainly and say what is missing.",
        }


STALL_SECONDS = 600
# Every model request streams, so this catches a request that sends nothing for ten minutes and
# never cuts off a slow model that is still producing output. It is large because a local server
# that runs one or two requests at a time queues the other chunks with no bytes coming back, which
# looks the same as a stall. No run has a total-time limit: Stop, usage limits, and this stall
# guard are the bounds.
MODEL_SETTINGS = ModelSettings(timeout=STALL_SECONDS)


async def drain_events(ctx: RunContext[Any], events: AsyncIterable[AgentStreamEvent]) -> None:
    """A no-op event stream handler; passing it makes a run's model requests streamed."""
    async for _ in events:
        pass


# One request ceiling for every chat, import, and scheduled run.
TURN_LIMITS = UsageLimits(request_limit=25)


__all__ = [
    "MODEL_SETTINGS",
    "STALL_SECONDS",
    "TURN_LIMITS",
    "WRITE_TOOLS",
    "InferenceContext",
    "RootSpanIO",
    "ToolErrorsToModel",
    "drain_events",
    "trim_old_tool_payloads",
]
