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
    Instrumentation,
    ModelFallback,
)
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai_harness.code_mode import CodeMode
from pydantic_ai_harness.compaction import (
    ClearToolResults,
    FallbackCompaction,
    SlidingWindowCompaction,
    SummarizingCompaction,
    TieredCompaction,
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
from fin_agent.db.conn import connect
from fin_agent.ledger.common import today as ledger_today

logger = logging.getLogger("fin_agent.agent")

WRITE_TOOLS = frozenset(
    {"record_movement", "correct_movement", "update_setup", "preference", "import_document", "undo"}
)

SUMMARY_PROMPT = (
    "Summarize this conversation for the agent continuing it. Keep what they asked for, "
    "the decisions made, open questions, and the IDs of rows you acted on. Leave out amounts "
    "and balances, which can be read again from the ledger. Leave out statement and photo text.\n\n"
    "{messages}"
)


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
    tracer_provider: Any = None,
    run_budget_usd: Decimal | None = None,
    context_window: int | None = None,
) -> list[AbstractCapability[Any]]:
    window = int(
        context_window or __import__("os").environ.get("LLM_CONTEXT_WINDOW", DEFAULT_CONTEXT_WINDOW)
    )
    target = min(100_000, window // 2)
    keep = max(target // 5, 1)

    summary_model: Model = FallbackModel(primary, backup) if backup is not None else primary
    capabilities: list[AbstractCapability[Any]] = [
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
        else:
            budgets = [
                Budget(usd=Decimal("5"), window="run"),
                Budget(usd=Decimal("20"), window="day"),
            ]
        capabilities.append(SpendLimits(budgets=budgets, store=spend_store))
    if tracer_provider is not None:
        from pydantic_ai.models.instrumented import InstrumentationSettings

        capabilities.append(
            Instrumentation(
                settings=InstrumentationSettings(
                    tracer_provider=tracer_provider, include_binary_content=False
                )
            )
        )
    return capabilities


RUN_MODEL_SETTINGS = ModelSettings(first_event_timeout=60, request_timeout=180)


def turn_limits() -> Any:
    from pydantic_ai import UsageLimits

    return UsageLimits(request_limit=50, tool_calls_limit=100)


__all__ = [
    "RUN_MODEL_SETTINGS",
    "SUMMARY_PROMPT",
    "ToolErrorsToModel",
    "WRITE_TOOLS",
    "build_capabilities",
    "date_and_changes",
    "redaction_guardrail",
    "turn_limits",
]
