"""The user's scheduled questions: the agent, read-only, with one run's request ceiling.

No write tools are built. The pinned financial snapshot is the first message, exactly as in chat.
The answer is written into the inbox, keyed by the question and its scheduled period.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from arno.agent.builder import build_agent
from arno.agent.capabilities import MODEL_SETTINGS, drain_events
from arno.agent.deps import AgentDeps
from arno.agent.history import build_snapshot_history
from arno.agent.number_check import UNTRACED_NOTE, check_messages
from arno.db.conn import Connection, connect
from arno.jobs.schedules import INDIA, period_for
from arno.notifications import create_notification, send_push_for_kind

logger = logging.getLogger("arno.jobs.questions")

REQUEST_LIMIT = 20
TOOL_CALL_LIMIT = 40


def ceiling_answer() -> str:
    """The plain answer one run writes when it stopped at its own ceiling."""
    return (
        f"This scheduled question hit its per-run ceiling ({REQUEST_LIMIT} model requests). It "
        "stopped early; narrow the question."
    )


async def _answer(
    conn: Connection,
    question: dict[str, Any],
    *,
    agent: Any,
) -> tuple[str, bool]:
    """The agent's answer, or the plain ceiling note; and whether a ceiling stopped it."""
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
        # Named on the answer and marked on the inbox item, so it never reads as an answer.
        return ceiling_answer(), True
    answer = str(result.output or "").strip()
    answer = answer or "The question ran, but the model returned no answer."
    # A scheduled answer is plain text in the inbox, so it carries the same note a chat turn
    # shows beside a figure no tool or script printed (architecture rule 1).
    flags = check_messages(answer, result.all_messages(), kinds=("money", "percent"))
    if flags:
        answer = f"{answer}\n\n{UNTRACED_NOTE.format(amounts=', '.join(flags))}"
    return answer, False


async def answer_question(
    conn: Connection,
    question: dict[str, Any],
    *,
    agent: Any,
) -> str:
    """Run the read-only agent over the pinned snapshot and return its answer."""
    answer, _ceiling = await _answer(conn, question, agent=agent)
    return answer


async def record_answer(
    conn: Connection,
    *,
    question: dict[str, Any],
    answer: str,
    period: str,
    ceiling: bool = False,
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
            "ceiling": ceiling,
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
            "skipped": "already answered",
        }
    if agent is None:
        agent = build_agent(read_only=True)
    answer, ceiling = await _answer(conn, dict(question), agent=agent)
    row = await record_answer(
        conn, question=dict(question), answer=answer, period=period, ceiling=ceiling
    )
    return {
        "notification_id": row["id"] if row else None,
        "period": period,
        "ceiling": ceiling,
    }


async def handle_scheduled_question(payload: dict[str, Any]) -> dict[str, Any]:
    question_id = str(payload.get("scheduled_question_id") or "")
    if not question_id:
        raise ValueError("scheduled question job has no scheduled_question_id")
    async with connect() as conn:
        result = await run_scheduled_question(
            conn, question_id=question_id, period=payload.get("period")
        )
        if result.get("notification_id"):
            await send_push_for_kind(conn, "scheduled_question")
        return result


__all__ = [
    "REQUEST_LIMIT",
    "TOOL_CALL_LIMIT",
    "answer_question",
    "ceiling_answer",
    "handle_scheduled_question",
    "record_answer",
    "run_scheduled_question",
]
