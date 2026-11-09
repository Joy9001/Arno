"""The user's scheduled questions: the agent, read-only, with one run's spend and request ceiling.

No write tools are built. The pinned financial snapshot is the first message, exactly as in chat.
The answer is written into the inbox, keyed by the question and its scheduled period.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from fin_agent.agent.builder import build_agent
from fin_agent.agent.capabilities import MODEL_SETTINGS, drain_events
from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.history import build_snapshot_history
from fin_agent.agent.spend import make_spend_store
from fin_agent.config import get_settings
from fin_agent.db.conn import Connection, connect
from fin_agent.jobs.schedules import INDIA, period_for
from fin_agent.notifications import create_notification, send_push_for_kind
from fin_agent.scheduled_questions import DEFAULT_SPEND_LIMIT_USD

logger = logging.getLogger("fin_agent.jobs.questions")

REQUEST_LIMIT = 20
TOOL_CALL_LIMIT = 40


def spend_budget(question: dict[str, Any]) -> Decimal:
    cents = question.get("spend_limit_cents")
    if cents is None:
        return DEFAULT_SPEND_LIMIT_USD
    return (Decimal(int(cents)) / 100).quantize(Decimal("0.01"))


async def answer_question(
    conn: Connection,
    question: dict[str, Any],
    *,
    agent: Any,
) -> str:
    """Run the read-only agent over the pinned snapshot and return its answer."""
    # TODO(L6): a real scheduled-question run against the configured CHAT endpoint is still
    # pending; tests use a scripted model and prove the read-only/ceiling wiring.
    prompt = str(question["prompt"])
    history, outside = await build_snapshot_history(conn)
    deps = AgentDeps(
        conversation_id="",
        flagged=outside,
        user_texts=(prompt,),
    )
    from pydantic_ai import UsageLimits
    from pydantic_ai.exceptions import UsageLimitExceeded

    try:
        result = await agent.run(
            prompt,
            message_history=history,
            deps=deps,
            model_settings=MODEL_SETTINGS,
            event_stream_handler=drain_events,
            usage_limits=UsageLimits(
                request_limit=REQUEST_LIMIT,
                tool_calls_limit=TOOL_CALL_LIMIT,
            ),
        )
    except UsageLimitExceeded:
        return (
            f"This scheduled question hit its per-run ceiling ({REQUEST_LIMIT} model requests "
            f"or {spend_budget(question)} USD). It stopped early; narrow the question or raise "
            "its spend limit in Settings."
        )
    answer = str(result.output or "").strip()
    return answer or "The question ran, but the model returned no answer."


async def record_answer(
    conn: Connection,
    *,
    question: dict[str, Any],
    answer: str,
    period: str,
) -> dict[str, Any] | None:
    row = await create_notification(
        conn,
        kind="scheduled_question",
        title=str(question["prompt"])[:200],
        body=answer,
        data={
            "question_id": question["id"],
            "prompt": question["prompt"],
            "period": period,
            "spend_limit_usd": str(spend_budget(question)),
        },
        dedupe_key=f"scheduled_question:{question['id']}:{period}",
    )
    await conn.execute(
        "UPDATE scheduled_questions SET last_run_at = now(), updated_at = now() WHERE id = $1",
        question["id"],
    )
    return row


async def run_scheduled_question(
    conn: Connection,
    *,
    question_id: str,
    period: str | None = None,
    agent: Any = None,
    spend_store: Any = None,
) -> dict[str, Any]:
    question = await conn.fetchrow(
        "SELECT * FROM scheduled_questions WHERE id = $1 AND active", question_id
    )
    if question is None:
        return {"skipped": "inactive or missing"}
    period = period or period_for(
        "scheduled_question", datetime.now(INDIA), {"scheduled_question_id": question_id}
    )
    dedupe_key = f"scheduled_question:{question_id}:{period}"
    if await conn.fetchval("SELECT 1 FROM notifications WHERE dedupe_key = $1", dedupe_key):
        # A crash-retried job must not spend a second model call on an answer already written.
        await conn.execute(
            "UPDATE scheduled_questions SET last_run_at = coalesce(last_run_at, now()), "
            "updated_at = now() WHERE id = $1",
            question_id,
        )
        return {
            "notification_id": None,
            "period": period,
            "spend_limit_usd": str(spend_budget(dict(question))),
            "skipped": "already answered",
        }
    if agent is None:
        if spend_store is None:
            raise RuntimeError("A scheduled question needs a spend store")
        agent = build_agent(
            read_only=True,
            spend_store=spend_store,
            question_budget_usd=spend_budget(dict(question)),
        )
    answer = await answer_question(conn, dict(question), agent=agent)
    row = await record_answer(conn, question=dict(question), answer=answer, period=period)
    return {
        "notification_id": row["id"] if row else None,
        "period": period,
        "spend_limit_usd": str(spend_budget(dict(question))),
    }


async def handle_scheduled_question(payload: dict[str, Any]) -> dict[str, Any]:
    question_id = str(payload.get("scheduled_question_id") or "")
    if not question_id:
        raise ValueError("scheduled question job has no scheduled_question_id")
    settings = get_settings()
    store, pool = await make_spend_store(settings.database_url)
    try:
        if store is None:
            raise RuntimeError("the spend store is unavailable; refusing an uncapped model run")
        async with connect() as conn:
            result = await run_scheduled_question(
                conn,
                question_id=question_id,
                period=payload.get("period"),
                spend_store=store,
            )
            if result.get("notification_id"):
                await send_push_for_kind(conn, "scheduled_question")
            return result
    finally:
        if pool is not None:
            await pool.close()


__all__ = [
    "REQUEST_LIMIT",
    "TOOL_CALL_LIMIT",
    "answer_question",
    "handle_scheduled_question",
    "record_answer",
    "run_scheduled_question",
    "spend_budget",
]
