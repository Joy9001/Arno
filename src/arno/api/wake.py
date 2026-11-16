"""Start a chat turn no user message opened: a waiting task report, or an import retry.

The harness keeps a settled background task's report for the conversation's next run; the host
starts that run. `TurnManager.schedule_wake` calls `wake` when a task settles and when a turn
ends, and the app calls it once at startup for reports a restart left waiting. An import retry
starts the same kind of turn, so both go through `start_server_turn` and its guards.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage

from arno.agent import history as history_mod
from arno.agent.chatgpt_auth import background_inference
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


async def start_server_turn(
    app: Any,
    conn: Any,
    *,
    thread: dict[str, Any],
    deps: AgentDeps,
    policy: WakePolicy | None = None,
    flagged: bool = False,
) -> str:
    """Start the turn no user message opened, under the same guards as the chat route.

    The thread must have no live turn, and the preparation guard is held until `turns.start`
    registers this one, so a server-started turn cannot overwrite a running turn's run marker.
    Raises `TurnBusy` or `PreparationBusy` before anything is written.
    """
    turns = app.state.turns
    thread_id = str(thread["id"])
    active = turns.get(thread_id)
    if active is not None and not active.done.is_set():
        raise TurnBusy("A reply is already running in this thread.")
    guard = turns.prepare(thread_id)
    await guard.acquire()
    run_id: str | None = None
    try:
        history, outside = await history_mod.ensure_history(conn, thread)
        ttl = int(os.environ.get("CHAT_CACHE_TTL_SECONDS", "300"))
        history, outside_swap = await history_mod.refresh_snapshot(
            conn, thread, history, cache_ttl_seconds=ttl
        )
        deps.flagged = (
            deps.flagged
            or flagged
            or thread.get("outside_text_at") is not None
            or outside
            or outside_swap
        )
        run_id = f"run_{uuid.uuid4().hex}"
        await history_mod.mark_run_started(conn, thread_id, run_id)
        reply_id = f"msg_{uuid.uuid4().hex}"
        adapter = VercelAIAdapter(
            agent=app.state.agent,
            # No client message: the run continues the stored history.
            run_input=SubmitMessage(id=thread_id, messages=[], trigger="submit-message"),
            sdk_version=7,
            server_message_id=reply_id,
        )
        with background_inference():
            await turns.start(
                adapter=adapter,
                thread_id=thread_id,
                message_id=f"server_{uuid.uuid4().hex}",
                history=history,
                deps=deps,
                title=thread.get("title"),
                run_id=run_id,
                reply_id=reply_id,
                wake=policy,
            )
    except BaseException:
        # The run never started, so the thread must not look busy until the next boot.
        if run_id is not None:
            await conn.execute(
                "UPDATE conversations SET run_started_at = NULL, active_run_id = NULL "
                "WHERE id = $1 AND active_run_id = $2",
                thread_id,
                run_id,
            )
        raise
    finally:
        guard.release()
    return thread_id


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
    retry = may_retry(reports)
    async with connect() as conn:
        thread = await history_mod.get_thread(conn, thread_id)
        if thread is None:
            return False
        deps = AgentDeps(
            conversation_id=thread_id,
            # The report is model-written from a statement, and no one is watching: lasting
            # writes this turn makes go on a confirm card.
            paused_parser_tasks=paused_parser_tasks(tasks, thread_id),
            woken_by=tuple(record.id for record in reports),
            may_retry=retry,
        )
        try:
            await start_server_turn(
                app,
                conn,
                thread=thread,
                deps=deps,
                policy=WakePolicy(may_retry=retry),
                flagged=True,
            )
        except Exception as exc:  # noqa: BLE001 - a failed wake leaves the report for the next
            if not isinstance(exc, (TurnBusy, PreparationBusy)):
                logger.exception("could not start a turn for the waiting report in %s", thread_id)
            return False
    return True


__all__ = ["paused_parser_tasks", "start_server_turn", "wake"]
