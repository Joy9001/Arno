"""The minute loop: due scheduled questions, then the daily tasks.

A question is claimed by advancing its `next_run_at` in one `UPDATE ... RETURNING`, so a restart
never fires it twice. Each daily task is idempotent on its own rows (notification dedupe keys,
`backup_runs`), so a restart that repeats one is harmless.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import date, datetime, time
from typing import Any

from arno.agent.extractor import handle_extract_import
from arno.db.conn import Connection, connect
from arno.importers.baselines import handle_recompute_baselines
from arno.importers.categorize import handle_categorize_import
from arno.jobs.background import spawn
from arno.jobs.backup import handle_backup_check
from arno.jobs.patterns import handle_pattern_detection
from arno.jobs.questions import handle_scheduled_question
from arno.jobs.reminders import handle_reminders
from arno.jobs.risk import handle_risk_check
from arno.ledger.errors import InvalidLedgerError
from arno.scheduled_questions import INDIA, next_occurrence, notification_time

logger = logging.getLogger("arno.jobs.loop")

# (name, task, India time of day); `None` means the notification-time setting.
DAILY: tuple[tuple[str, Callable[[], Coroutine[Any, Any, None]], time | None], ...] = (
    ("reminders", handle_reminders, None),
    ("risk_check", handle_risk_check, time(2, 0)),
    ("pattern_detection", handle_pattern_detection, time(2, 15)),
    ("recompute_baselines", handle_recompute_baselines, time(2, 30)),
    ("backup_check", handle_backup_check, time(3, 0)),
)
_last_day: dict[str, date] = {}


async def claim_due_questions(conn: Connection, now: datetime) -> list[tuple[str, str]]:
    """Advance every due question and return (id, period) for the ones that fire now."""
    at = await notification_time(conn)
    claimed: list[tuple[str, str]] = []
    for question in await conn.fetch(
        """
        SELECT id, rule, created_at, next_run_at FROM scheduled_questions
        WHERE active AND (next_run_at IS NULL OR next_run_at <= $1)
        """,
        now,
    ):
        try:
            following = next_occurrence(
                question["rule"], after=now, created_at=question["created_at"], at=at
            )
        except InvalidLedgerError:
            following = None
        # Compare-and-set on the old value: only one claimer advances the row and fires it.
        row = await conn.fetchrow(
            """
            UPDATE scheduled_questions
            SET next_run_at = $1::timestamptz, active = ($1::timestamptz IS NOT NULL),
                updated_at = now()
            WHERE id = $2 AND next_run_at IS NOT DISTINCT FROM $3::timestamptz
            RETURNING id
            """,
            following,
            question["id"],
            question["next_run_at"],
        )
        # A NULL next run is only being filled in, not due.
        if row is not None and question["next_run_at"] is not None:
            period = question["next_run_at"].astimezone(INDIA).strftime("%Y-%m-%dT%H:%M")
            claimed.append((str(question["id"]), period))
    return claimed


async def tick(now: datetime | None = None) -> None:
    now = now or datetime.now(INDIA)
    async with connect() as conn:
        due = await claim_due_questions(conn, now)
        notify_at = await notification_time(conn)
    for question_id, period in due:
        spawn(handle_scheduled_question(question_id, period), name=f"question-{question_id}")
    for name, task, at in DAILY:
        if now.time() >= (at or notify_at) and _last_day.get(name) != now.date():
            _last_day[name] = now.date()
            spawn(task(), name=name)


async def run() -> None:
    while True:
        try:
            await tick()
        except Exception:  # noqa: BLE001 - the loop must survive a bad tick
            logger.exception("minute loop tick failed")
        await asyncio.sleep(60)


async def recover() -> int:
    """At boot, resume work a restart interrupted: categorizing and reading imports."""
    async with connect() as conn:
        categorizing = await conn.fetch(
            """
            SELECT id FROM imports
            WHERE status = 'committed' AND categorize ->> 'state' IN ('queued', 'running')
            """
        )
        # An import waiting on a password is `needs_password`, so `parsing` is always resumable.
        reading = await conn.fetch("SELECT id FROM imports WHERE status = 'parsing'")
    for row in categorizing:
        spawn(handle_categorize_import(str(row["id"])), name=f"categorize-{row['id']}")
    for row in reading:
        spawn(handle_extract_import(str(row["id"])), name=f"extract-{row['id']}")
    return len(categorizing) + len(reading)


__all__ = ["claim_due_questions", "recover", "run", "tick"]
