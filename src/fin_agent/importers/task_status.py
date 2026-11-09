"""Settle an import's status when its parser task ends.

The parser sets `review`, `needs_input`, and the other in-run states itself. Only the task's end
knows why a run stopped, so the terminal state for an import still `parsing` is set here, from the
task's outcome. Every reason is host-made text.
"""

from __future__ import annotations

import logging
from typing import Any

from fin_agent.db.conn import Connection

logger = logging.getLogger(__name__)

PAUSED_REASON = "The parser ran out of steps. Continue to give it another 120."
REASONS = {
    "timeout": "The parser took too long and was stopped.",
    "cancelled": "The parser was stopped before it finished.",
}


def _reason(outcome: str | None, output: str) -> str:
    if outcome in REASONS:
        return REASONS[outcome]
    last = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "")
    return f"The parser stopped with an error: {last[:200]}" if last else "The parser stopped."


async def settle_imports(
    conn: Connection, *, task_id: str, outcome: str | None, output: str
) -> int:
    """Move this task's still-parsing imports to `paused` or `failed`. Returns rows changed."""
    if outcome == "ok":
        # A finished parse already set each import's state; one left `parsing` was never reached.
        status, reason = "failed", "The parser finished without reading this file."
    elif outcome == "budget":
        status, reason = "paused", PAUSED_REASON
    else:
        status, reason = "failed", _reason(outcome, output)
    # A finished parse keeps the last check's reason when it recorded one.
    result = await conn.execute(
        """
        UPDATE imports
        SET status = $2,
            reason = CASE WHEN $4 THEN coalesce(nullif(reason, ''), $3) ELSE $3 END,
            progress = NULL, updated_at = now()
        WHERE task_id = $1 AND status = 'parsing'
        """,
        task_id,
        status,
        reason,
        outcome == "ok",
    )
    return int(result.split()[-1])


async def reconcile(conn: Connection, records: dict[str, Any]) -> int:
    """At startup: imports left `parsing` by a task that is no longer running."""
    changed = 0
    # An import whose run died before it got a task id would otherwise stay parsing forever.
    untasked = await conn.fetch(
        """
        UPDATE imports
        SET status = 'failed', progress = NULL, updated_at = now(),
            reason = 'The app restarted while this file was being read. Upload it again.'
        WHERE status = 'parsing' AND task_id IS NULL
        RETURNING id
        """
    )
    changed += len(untasked)
    rows = await conn.fetch(
        "SELECT DISTINCT task_id FROM imports WHERE status = 'parsing' AND task_id IS NOT NULL"
    )
    for row in rows:
        record = records.get(row["task_id"])
        if record is not None and record.status == "running":
            continue
        changed += await settle_imports(
            conn,
            task_id=row["task_id"],
            outcome=record.outcome if record is not None else "failed",
            output=record.output if record is not None else "the app restarted",
        )
    return changed


__all__ = ["PAUSED_REASON", "reconcile", "settle_imports"]
