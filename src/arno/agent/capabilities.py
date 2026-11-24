"""The harness capability list, in the order system-design step 6 settled.

Each value is a starting point. Tests drive them with `FunctionModel`; real token counts and cache
behavior are measured from traces at L2.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterable
from contextlib import nullcontext
from dataclasses import replace
from decimal import Decimal
from typing import Any, override

from pydantic import BaseModel
from pydantic_ai import ModelSettings, RetryModelRequest, RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    Caching,
    ModelFallback,
    WrapRunHandler,
)
from pydantic_ai.messages import (
    AgentStreamEvent,
    ModelRequest,
    ModelResponse,
    ToolReturn,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestContext
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai_harness.code_mode import CodeMode
from pydantic_ai_harness.compaction import (
    ClearToolResults,
    FallbackCompaction,
    SlidingWindowCompaction,
    SummarizingCompaction,
    TieredCompaction,
    is_pinned,
)
from pydantic_ai_harness.guardrails import InputGuardrail
from pydantic_ai_harness.spend import Budget
from pydantic_ai_harness.subagents import is_task_report
from pydantic_ai_harness.system_reminders import SystemReminders
from pydantic_ai_harness.tool_output_limits import (
    Band,
    ToolOutputLimits,
    Truncate,
    TruncationStrategy,
)
from pydantic_ai_harness.warn_on_cache_busts import WarnOnCacheBusts

from arno.agent.chatgpt_auth import background_inference
from arno.agent.cost_journal import cost_identity, current_cost_identity
from arno.agent.deps import AgentDeps
from arno.agent.metering import TRANSIENT_MODEL_ERRORS
from arno.agent.models import DEFAULT_CONTEXT_WINDOW
from arno.agent.redaction import redact_text, redaction_detectors
from arno.agent.spend import (
    COST_STOP_ERRORS,
    DAILY_CEILING_USD,
    SHARED_DAILY_BUDGET,
    shared_daily_budget,
    spend_limits,
)
from arno.db.conn import connect
from arno.ledger.common import today as ledger_today
from arno.tools.mark import contains_outside, flag_outside_text

logger = logging.getLogger("arno.agent")

RUN_CODE_TOOL = "run_code"

WRITE_TOOLS = frozenset(
    {
        "record_movement",
        "correct_movement",
        "update_setup",
        "schedule_payment",
        "preference",
        "undo",
    }
)

SUMMARY_PROMPT = (
    "Summarize this conversation for the agent continuing it. Keep what they asked for, "
    "the decisions made, open questions, and the IDs of rows you acted on. Copy the figures a "
    "decision rests on exactly as they were said (an agreed amount, a floor, a date, a rate, a "
    "rule); a summary that keeps the decision without its figure loses what was decided. Leave "
    "out balances and statement totals, which can be read again from the ledger. Leave out "
    "statement and photo text.\n\n"
    "{messages}"
)


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
    """The prompt this run is answering, never the pinned snapshot or older turns."""
    if ctx.prompt is not None:
        text = _content_text(ctx.prompt)
        if text:
            return text
    # The chat adapter passes the new turn in `message_history` instead of `prompt`.
    for message in reversed(ctx.messages):
        if not isinstance(message, ModelRequest):
            continue
        for part in reversed(message.parts):
            if isinstance(part, UserPromptPart) and not is_pinned(part):
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
                # Same policy as the guardrail and stored history: this runs before the
                # guardrail, so the preview would otherwise show the raw prompt.
                span.set_attribute("input.value", redact_text(text))
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
    """Carry conversation attribution and consent through summaries and delegated work."""

    def __init__(self, *, background: bool = False) -> None:
        self.background = background

    @override
    async def wrap_run(self, ctx: RunContext[Any], *, handler: WrapRunHandler) -> Any:
        session_id = getattr(ctx.deps, "conversation_id", None) or ctx.conversation_id
        consent = background_inference() if self.background else nullcontext()
        with consent, cost_identity(session_id=session_id):
            from opentelemetry.trace import get_current_span

            session_id, workload = current_cost_identity()
            span = get_current_span()
            if session_id:
                span.set_attribute("session.id", session_id)
                span.set_attribute("gen_ai.conversation.id", session_id)
            if workload:
                span.set_attribute("arno.cost.workload", workload)
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
        from arno.jobs.runner import JobGaveUp

        if isinstance(error, (JobGaveUp, *COST_STOP_ERRORS)):
            # Code Mode transports nested exceptions through Monty. Even a snippet that catches
            # one must not dispatch another tool/model or finish this run as successful.
            self._cost_stop = error
            raise error
        logger.error("tool %s failed (%s)", call.tool_name, type(error).__name__)
        return {
            "status": "error",
            "tool": call.tool_name,
            "message": "The tool failed. Tell the user plainly and say what is missing.",
        }


def _has_outside(value: Any) -> bool:
    """Adapt PydanticAI return objects onto `mark.contains_outside`."""
    if isinstance(value, ToolReturn):
        return any(
            _has_outside(item) for item in (value.return_value, value.content, value.metadata)
        )
    if isinstance(value, ToolReturnPart):
        return _has_outside(value.content) or _has_outside(value.metadata)
    if isinstance(value, dict):
        return any(
            (key == "outside_text" and item is True) or _has_outside(key) or _has_outside(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_has_outside(item) for item in value)
    return contains_outside(value)


async def _flag_conversation(ctx: RunContext[AgentDeps]) -> None:
    deps = ctx.deps
    conversation_id = getattr(deps, "conversation_id", None)
    if not conversation_id:
        return
    if not deps.flagged:
        async with connect() as conn:
            await flag_outside_text(conn, conversation_id)
        deps.flagged = True


class OutsideTextTaint(AbstractCapability[AgentDeps]):
    """Persist conversation taint across tool returns, retry errors, and parser task reports."""

    @override
    async def after_tool_execute(
        self, ctx: RunContext[AgentDeps], *, call: Any, tool_def: Any, args: Any, result: Any
    ) -> Any:
        if ctx.deps and not ctx.deps.flagged and _has_outside(result):
            await _flag_conversation(ctx)
        return result

    @override
    async def wrap_tool_execute(
        self,
        ctx: RunContext[AgentDeps],
        *,
        call: Any,
        tool_def: Any,
        args: Any,
        handler: Any,
    ) -> Any:
        from pydantic_ai.exceptions import ModelRetry, ToolRetryError

        try:
            return await handler(args)
        except ModelRetry as exc:
            if ctx.deps and not ctx.deps.flagged and _has_outside(exc.message):
                await _flag_conversation(ctx)
            raise
        except ToolRetryError as exc:
            if ctx.deps and not ctx.deps.flagged and _has_outside(exc.tool_retry):
                await _flag_conversation(ctx)
            raise

    @override
    async def before_model_request(self, ctx: RunContext[AgentDeps], request_context: Any) -> Any:
        deps = ctx.deps
        if deps is None or not getattr(deps, "conversation_id", None) or deps.flagged:
            return request_context
        for message in request_context.messages:
            if not isinstance(message, ModelRequest):
                continue
            if any(is_task_report(part) for part in message.parts):
                await _flag_conversation(ctx)
                break
        return request_context


# What a script can reach. Monty resolves only part of the standard library, so a name it
# cannot import is answered with this list instead of the type checker's search paths.
SANDBOX_MODULES = (
    "json",
    "datetime",
    "math",
    "re",
    "itertools",
    "functools",
    "collections",
    "decimal",
    "random",
    "time",
    "hashlib",
    "base64",
    "bisect",
    "copy",
)
# The read toolset's functions, which `run_code` takes over: a test keeps this list in step.
SANDBOX_HELPERS = (
    "amortize",
    "card_payoff",
    "compare_offers",
    "compound",
    "get_financial_context",
    "present",
    "project",
    "query_movements",
    "recap",
    "rupees",
    "run_query",
    "show_chart",
    "show_table",
    "summarize_spending",
)
SANDBOX_ERRORS = ("Type error in code:", "Runtime error:")


def sandbox_help() -> str:
    """What a failed script could have used: its helpers, and the modules it may import."""
    return (
        "Inside run_code call only its helpers, never a write tool: "
        + ", ".join(SANDBOX_HELPERS)
        + ". The sandbox imports only "
        + ", ".join(SANDBOX_MODULES)
        + "; compute anything else from a tool return."
    )


class CodeModeHints(AbstractCapability[AgentDeps]):
    """A failed `run_code` call names the helpers and modules it could have used."""

    @override
    async def wrap_tool_execute(
        self,
        ctx: RunContext[AgentDeps],
        *,
        call: Any,
        tool_def: Any,
        args: Any,
        handler: Any,
    ) -> Any:
        from pydantic_ai.exceptions import ToolRetryError
        from pydantic_ai.messages import ToolReturnPart

        try:
            return await handler(args)
        except ToolRetryError as exc:
            retry = exc.tool_retry
            if call.tool_name != RUN_CODE_TOOL or not isinstance(retry, ToolReturnPart):
                raise
            message = str(retry.content)
            if not message.startswith(SANDBOX_ERRORS):
                raise
            raise ToolRetryError(
                replace(retry, content=f"{message.rstrip()}\n\n{sandbox_help()}")
            ) from exc


async def date_and_changes(ctx: RunContext[AgentDeps]) -> str:
    """Today's date and how many writes landed since the pinned snapshot."""
    day = ledger_today().isoformat()
    if ctx.deps is None or not getattr(ctx.deps, "conversation_id", None):
        return f"Today is {day}."
    async with connect() as conn:
        row = await conn.fetchrow(
            "SELECT snapshot_at FROM conversations WHERE id = $1", ctx.deps.conversation_id
        )
        if row is None or row["snapshot_at"] is None:
            return f"Today is {day}."
        written = (
            await conn.fetchrow_required(
                "SELECT count(*) AS n FROM audit_log WHERE created_at > $1",
                row["snapshot_at"],
            )
        )["n"]
    if written:
        return (
            f"Today is {day}. Since your snapshot: {written} "
            f"{'write' if written == 1 else 'writes'}; re-read before stating a balance."
        )
    return f"Today is {day}. Nothing has changed since your snapshot."


def redaction_guardrail() -> InputGuardrail[AgentDeps]:
    # The one detector list `redact_text` uses, so the model sees exactly what storage keeps.
    return InputGuardrail(guard=redaction_detectors())


def build_capabilities(
    *,
    primary: Model,
    backup: Model | None = None,
    spend_store: Any = None,
    run_budget_usd: Decimal | None = None,
    question_budget_usd: Decimal | None = None,
    context_window: int | None = None,
    subagents: AbstractCapability[Any] | None = None,
) -> list[AbstractCapability[Any]]:
    window = int(
        context_window or __import__("os").environ.get("LLM_CONTEXT_WINDOW", DEFAULT_CONTEXT_WINDOW)
    )
    target = min(100_000, window // 2)
    keep = max(target // 5, 1)

    budgets: list[Budget] | None = None
    if spend_store is not None:
        if run_budget_usd is not None:
            # An eval trial is one conversation and may have several turns. A conversation
            # budget is the only window that caps the whole trial, not each turn in it.
            budgets = [
                Budget(
                    usd=run_budget_usd,
                    tokens=1_000_000,
                    window="conversation",
                    name="trial",
                )
            ]
        elif question_budget_usd is not None:
            # A scheduled question has its own small run ceiling, while the daily ceiling is
            # shared with chat and every other agent run.
            budgets = [
                Budget(usd=question_budget_usd, window="run", name="scheduled question"),
                shared_daily_budget(),
            ]
        else:
            budgets = [
                Budget(usd=Decimal("5"), window="run", name="turn"),
                shared_daily_budget(),
            ]

    # The summary runs on its own Agent, so it needs its own SpendLimits. Only windows keyed by
    # the day or the conversation share the outer ledger; a `run` window would key a fresh
    # summarizer run and charge a second budget instead of the outer one.
    summary_budgets = [budget for budget in budgets or [] if budget.window != "run"]
    summary_limits = spend_limits(spend_store, budgets=summary_budgets)
    summarization_capabilities: list[AbstractCapability[Any]] = [InferenceContext()]
    if summary_limits is not None:
        summarization_capabilities.append(summary_limits)

    summary_model: Model = FallbackModel(primary, backup) if backup is not None else primary
    capabilities: list[AbstractCapability[Any]] = [
        InferenceContext(),
        RootSpanIO(),
        redaction_guardrail(),
        CodeMode(tools={"code_mode": True}),
        Caching(),
        WarnOnCacheBusts(),
        SystemReminders(dynamic_reminders=[date_and_changes]),
        ToolOutputLimits(
            bands=[
                Band(
                    over=10_000,
                    action=Truncate(max_chars=10_000, strategy=TruncationStrategy.head),
                )
            ],
            tool_filter=["run_code"],
        ),
        TieredCompaction(
            tiers=[
                ClearToolResults(max_tokens=1, keep_pairs=5, exclude_tools=WRITE_TOOLS),
                FallbackCompaction(
                    fallback_chain=[
                        SummarizingCompaction(
                            model=summary_model,
                            max_messages=1,
                            keep_tokens=keep,
                            keep_messages=20,
                            keep_user_messages=True,
                            receipts=True,
                            summary_prompt=SUMMARY_PROMPT,
                            summarization_capabilities=summarization_capabilities,
                        ),
                        SlidingWindowCompaction(max_messages=1, keep_tokens=keep, receipts=True),
                    ]
                ),
            ],
            target_tokens=target,
        ),
        ToolErrorsToModel(),
        OutsideTextTaint(),
        CodeModeHints(),
    ]
    if subagents is not None:
        capabilities.append(subagents)
    # Same model first, then the backup: the retry hands its last error on to the fallback.
    capabilities.append(RetryTransientModelErrors())
    if backup is not None:
        capabilities.append(ModelFallback(backup, sticky=False))
    main_limits = spend_limits(spend_store, budgets=budgets)
    if main_limits is not None:
        capabilities.append(main_limits)
    return capabilities


TRANSIENT_ATTEMPTS = 3
"""Attempts per request step on a transient provider error: the first try and two retries."""


class RetryTransientModelErrors(AbstractCapability[Any]):
    """Ask the same model again when the provider fails for a reason a second try can fix.

    The SDK clients run with `max_retries=0` so every attempt is metered, and a free provider's
    5xx or a stream that drops before its first event would otherwise end the whole turn or a
    long parser run. Each retry waits a little longer; after the last one the error goes on to
    `ModelFallback`, when a backup model is configured, and then to the run.
    """

    def __init__(self, attempts: int = TRANSIENT_ATTEMPTS, *, base_delay: float = 2.0) -> None:
        self.attempts = attempts
        self.base_delay = base_delay
        self.id = "retry_transient_model_errors"

    @override
    async def on_model_request_error(
        self,
        ctx: RunContext[Any],
        *,
        request_context: ModelRequestContext,
        error: Exception,
    ) -> ModelResponse:
        transient = isinstance(error, TRANSIENT_MODEL_ERRORS)
        if not transient or request_context.attempt >= self.attempts:
            raise error
        logger.warning(
            "model request failed (%s), retrying: attempt %d of %d",
            type(error).__name__,
            request_context.attempt + 1,
            self.attempts,
        )
        await asyncio.sleep(self.base_delay * request_context.attempt)
        raise RetryModelRequest()


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


def turn_limits() -> Any:
    from pydantic_ai import UsageLimits

    return UsageLimits(request_limit=50, tool_calls_limit=100)


__all__ = [
    "DAILY_CEILING_USD",
    "MODEL_SETTINGS",
    "SANDBOX_ERRORS",
    "SANDBOX_HELPERS",
    "SANDBOX_MODULES",
    "SHARED_DAILY_BUDGET",
    "STALL_SECONDS",
    "SUMMARY_PROMPT",
    "CodeModeHints",
    "OutsideTextTaint",
    "RetryTransientModelErrors",
    "RootSpanIO",
    "ToolErrorsToModel",
    "WRITE_TOOLS",
    "build_capabilities",
    "date_and_changes",
    "drain_events",
    "redaction_guardrail",
    "sandbox_help",
    "shared_daily_budget",
    "spend_limits",
    "turn_limits",
]
