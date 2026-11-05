"""The harness capability list, in the order system-design step 6 settled.

Each value is a starting point. Tests drive them with `FunctionModel`; real token counts and cache
behavior are measured from traces at L2.
"""

from __future__ import annotations

import logging
from contextlib import closing
from decimal import Decimal
from typing import Any

from pydantic_ai import ModelSettings, RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    Caching,
    ModelFallback,
    WrapRunHandler,
)
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import Model
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
from pydantic_ai_harness.guardrails.detectors import personal_data, secret_data
from pydantic_ai_harness.spend import Budget, SpendLimits
from pydantic_ai_harness.system_reminders import SystemReminders
from pydantic_ai_harness.tool_output_limits import (
    Band,
    ToolOutputLimits,
    Truncate,
    TruncationStrategy,
)
from pydantic_ai_harness.warn_on_cache_busts import WarnOnCacheBusts

from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.models import DEFAULT_CONTEXT_WINDOW
from fin_agent.agent.redaction import redact_text
from fin_agent.db.conn import connect
from fin_agent.ledger.common import today as ledger_today

logger = logging.getLogger("fin_agent.agent")

DAILY_CEILING_USD = Decimal("20")
SHARED_DAILY_BUDGET = "shared daily"

WRITE_TOOLS = frozenset(
    {"record_movement", "correct_movement", "update_setup", "preference", "import_document", "undo"}
)

SUMMARY_PROMPT = (
    "Summarize this conversation for the agent continuing it. Keep what they asked for, "
    "the decisions made, open questions, and the IDs of rows you acted on. Leave out amounts "
    "and balances, which can be read again from the ledger. Leave out statement and photo text.\n\n"
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
    """Put the current message and final reply on the native `invoke_agent` span.

    Phoenix's trace list reads `input.value`/`output.value`; Pydantic AI's root span carries the
    full history and `final_result` but not those two view attributes. This capability writes only
    those attributes on the existing span, inside the instrumentation's outermost layer.
    """

    async def wrap_run(self, ctx: RunContext[Any], *, handler: WrapRunHandler) -> Any:
        from opentelemetry.trace import get_current_span

        span = get_current_span()
        if span.is_recording():
            # Identity is attribution, not message content: record it even when content is off.
            # The API passes the opaque authenticated principal; CLI and scheduled runs use the
            # one local actor this single-user installation has.
            span.set_attribute("user.id", getattr(ctx.deps, "user_id", None) or "local-user")
        if ctx.trace_include_content and span.is_recording():
            text = _current_user_text(ctx)
            if text:
                # Same policy as the guardrail and stored history: this runs before the
                # guardrail, so the preview would otherwise show the raw prompt.
                span.set_attribute("input.value", redact_text(text))
                span.set_attribute("input.mime_type", "text/plain")
        result = await handler()
        if ctx.trace_include_content and span.is_recording():
            output = result.output
            if isinstance(output, str) and output.strip():
                span.set_attribute("output.value", output)
                span.set_attribute("output.mime_type", "text/plain")
        return result


class ToolErrorsToModel(AbstractCapability[AgentDeps]):
    """A tool that throws something unexpected becomes a failed result the model can explain."""

    async def on_tool_execute_error(
        self,
        ctx: RunContext[AgentDeps],
        *,
        call: Any,
        tool_def: Any,
        args: Any,
        error: Exception,
    ) -> Any:
        logger.error("tool %s failed (%s)", call.tool_name, type(error).__name__)
        return {
            "status": "error",
            "tool": call.tool_name,
            "message": "The tool failed. Tell the user plainly and say what is missing.",
        }


def shared_daily_budget() -> Budget:
    """The `$20/day` ceiling promised across chat, schedules, parse, vision, and briefing.

    All paths use this name and window, so they read and write one Postgres counter instead of
    each keeping a private ceiling.
    """
    return Budget(usd=DAILY_CEILING_USD, window="day", name=SHARED_DAILY_BUDGET)


def spend_limits(store: Any, *, budgets: list[Budget] | None = None) -> SpendLimits | None:
    """`SpendLimits` for a domain agent, or `None` when no store is available."""
    if store is None:
        return None
    return SpendLimits(budgets=budgets or [shared_daily_budget()], store=store)


def date_and_changes(ctx: RunContext[AgentDeps]) -> str:
    """Today's date and how many writes landed since the pinned snapshot."""
    day = ledger_today().isoformat()
    if ctx.deps is None or not getattr(ctx.deps, "conversation_id", None):
        return f"Today is {day}."
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT snapshot_at FROM conversations WHERE id = %s", (ctx.deps.conversation_id,)
        ).fetchone()
        if row is None or row["snapshot_at"] is None:
            return f"Today is {day}."
        written = conn.execute(
            "SELECT count(*) AS n FROM audit_log WHERE created_at > %s",
            (row["snapshot_at"],),
        ).fetchone()["n"]
    if written:
        return (
            f"Today is {day}. Since your snapshot: {written} "
            f"{'write' if written == 1 else 'writes'}; re-read before stating a balance."
        )
    return f"Today is {day}. Nothing has changed since your snapshot."


def redaction_guardrail() -> InputGuardrail[AgentDeps]:
    return InputGuardrail(
        guard=[
            secret_data(),
            personal_data(
                only=["credit_card"],
                extra={
                    "pan": r"\b[A-Z]{5}[0-9]{4}[A-Z]\b",
                    # Spaced only; an unspaced 12-digit number collides with UPI UTRs.
                    "aadhaar": r"\b[2-9]\d{3}[ -]\d{4}[ -]\d{4}\b",
                },
            ),
        ]
    )


def build_capabilities(
    *,
    primary: Model,
    backup: Model | None = None,
    spend_store: Any = None,
    run_budget_usd: Decimal | None = None,
    question_budget_usd: Decimal | None = None,
    context_window: int | None = None,
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
    summarization_capabilities = (
        [SpendLimits(budgets=summary_budgets, store=spend_store)]
        if summary_budgets and spend_store is not None
        else []
    )

    summary_model: Model = FallbackModel(primary, backup) if backup is not None else primary
    capabilities: list[AbstractCapability[Any]] = [
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
    ]
    if backup is not None:
        capabilities.append(ModelFallback(backup, sticky=False))
    if budgets is not None:
        capabilities.append(SpendLimits(budgets=budgets, store=spend_store))
    return capabilities


RUN_MODEL_SETTINGS = ModelSettings(first_event_timeout=60, request_timeout=180)


def turn_limits() -> Any:
    from pydantic_ai import UsageLimits

    return UsageLimits(request_limit=50, tool_calls_limit=100)


__all__ = [
    "DAILY_CEILING_USD",
    "RUN_MODEL_SETTINGS",
    "SHARED_DAILY_BUDGET",
    "SUMMARY_PROMPT",
    "RootSpanIO",
    "ToolErrorsToModel",
    "WRITE_TOOLS",
    "build_capabilities",
    "date_and_changes",
    "redaction_guardrail",
    "shared_daily_budget",
    "spend_limits",
    "turn_limits",
]
