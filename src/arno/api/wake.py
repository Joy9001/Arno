"""Start a chat turn with no user message, so the agent reads a task report that is waiting.

The harness keeps a settled background task's report for the conversation's next run; the host
starts that run. `TurnManager.schedule_wake` calls this when a task settles and when a turn ends,
and the app calls it once at startup for reports a restart left waiting.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage

from arno.agent import history as history_mod
from arno.agent.deps import AgentDeps
from arno.agent.turns import PreparationBusy, TurnBusy
from arno.agent.wake import WakePolicy, may_retry, waiting_reports
from arno.db.conn import connect

logger = logging.getLogger("arno.chat")


def paused_parser_tasks(tasks: Any, thread_id: str) -> tuple[str, ...]:
    """This thread's parser tasks that stopped on their step budget, so a reply can continue one."""
    if tasks is None:
        return ()
    return tuple(
        record.id
        for record in tasks.records.values()
        if record.conversation_id == thread_id
        and record.agent_name == "parser"
        and record.status == "finished"
        and record.outcome == "budget"
    )


async def wake(app: Any, thread_id: str) -> bool:
    """Start the turn if a report waits and nothing else holds the thread. True when started.

    A running turn or a message being prepared reads the report itself, or wakes the thread
    again when it ends, so either one is a reason to do nothing here.
    """
    tasks = getattr(app.state, "tasks", None)
    turns = app.state.turns
    if tasks is None or turns.closing:
        return False
    reports = waiting_reports(tasks, thread_id)
    if not reports:
        return False
    active = turns.get(thread_id)
    if active is not None and not active.done.is_set():
        return False
    guard = turns.prepare(thread_id)
    try:
        await guard.acquire()
    except PreparationBusy:
        return False
    run_id = ""
    try:
        async with connect() as conn:
            thread = await history_mod.get_thread(conn, thread_id)
            if thread is None:
                return False
            history, _ = await history_mod.ensure_history(conn, thread)
            ttl = int(os.environ.get("CHAT_CACHE_TTL_SECONDS", "300"))
            history, _ = await history_mod.refresh_snapshot(
                conn, thread, history, cache_ttl_seconds=ttl
            )
            run_id = f"run_{uuid.uuid4().hex}"
            await history_mod.mark_run_started(conn, thread_id, run_id)
        retry = may_retry(reports)
        deps = AgentDeps(
            conversation_id=thread_id,
            # The report is model-written from a statement, and no one is watching: lasting
            # writes this turn makes go on a confirm card.
            flagged=True,
            paused_parser_tasks=paused_parser_tasks(tasks, thread_id),
            woken_by=tuple(record.id for record in reports),
            may_retry=retry,
        )
        reply_id = f"msg_{uuid.uuid4().hex}"
        adapter = VercelAIAdapter(
            agent=app.state.agent,
            # No client message: the run continues the stored history, and the waiting reports
            # enter its first request.
            run_input=SubmitMessage(id=thread_id, messages=[], trigger="submit-message"),
            sdk_version=7,
            server_message_id=reply_id,
        )
        await turns.start(
            adapter=adapter,
            thread_id=thread_id,
            message_id=f"wake_{uuid.uuid4().hex}",
            history=history,
            deps=deps,
            title=thread.get("title"),
            run_id=run_id,
            reply_id=reply_id,
            wake=WakePolicy(may_retry=retry),
        )
    except Exception as exc:  # noqa: BLE001 - a failed wake leaves the report for the next message
        if not isinstance(exc, TurnBusy):
            logger.exception("could not start a turn for the waiting report in %s", thread_id)
        if not run_id:
            return False
        async with connect() as conn:
            await conn.execute(
                "UPDATE conversations SET run_started_at = NULL, active_run_id = NULL "
                "WHERE id = $1 AND active_run_id = $2",
                thread_id,
                run_id,
            )
        return False
    finally:
        guard.release()
    return True


__all__ = ["paused_parser_tasks", "wake"]
