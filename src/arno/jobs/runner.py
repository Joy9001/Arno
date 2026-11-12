"""A `jobs` table and one loop.

One process owns the loop, so a `running` row at boot was interrupted and goes back to
`pending`. A job can be enqueued in the same transaction as its data. Keys are unique, so a
restart cannot send the same job twice. A failing job retries three times with backoff, then
turns `failed` and lands in the inbox. A handler that knows another attempt cannot help raises
`JobGaveUp` to fail at once. What a handler returns is kept as the job's `result`.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from typing import Any

from arno.agent.chatgpt_auth import background_inference
from arno.agent.cost_journal import cost_identity
from arno.agent.spend import COST_STOP_ERRORS
from arno.db.conn import Connection, connect

logger = logging.getLogger("arno.jobs")

Handler = Callable[[dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]
MAX_ATTEMPTS = 3
# `None` means no limit: the handler runs until it finishes, so a slow model is never cut off.
DEFAULT_TIMEOUTS: dict[str, float | None] = {
    "recompute_baselines": 120,
    "weekly_briefing": None,
    "reminders": 60,
    "risk_check": 120,
    "pattern_detection": 120,
    "backup_check": 30,
    "scheduled_question": None,
    "categorize_import": None,
    "collect_batch": None,
}


def default_handlers() -> dict[str, Handler]:
    """Imported lazily, so the job module does not import the whole agent at boot."""
    from arno.importers.baselines import handle_recompute_baselines
    from arno.importers.categorize import handle_categorize_import, handle_collect_batch
    from arno.jobs.backup import handle_backup_check
    from arno.jobs.briefing import handle_weekly_briefing
    from arno.jobs.costs import handle_cost_replay
    from arno.jobs.patterns import handle_pattern_detection
    from arno.jobs.questions import handle_scheduled_question
    from arno.jobs.recaps import handle_recaps
    from arno.jobs.reminders import handle_reminders
    from arno.jobs.risk import handle_risk_check

    return {
        "cost_replay": handle_cost_replay,
        "recompute_baselines": handle_recompute_baselines,
        "weekly_briefing": handle_weekly_briefing,
        "reminders": handle_reminders,
        "risk_check": handle_risk_check,
        "pattern_detection": handle_pattern_detection,
        "backup_check": handle_backup_check,
        "recaps": handle_recaps,
        "scheduled_question": handle_scheduled_question,
        "categorize_import": handle_categorize_import,
        "collect_batch": handle_collect_batch,
    }


DEFAULT_HANDLERS: dict[str, Handler] = {}


class JobGaveUp(Exception):  # noqa: N818 - it reads as what happened, like `TimeoutError`
    """A failure another attempt cannot fix (a spend ceiling, say): fail now, without retries."""


def handlers() -> dict[str, Handler]:
    global DEFAULT_HANDLERS
    if not DEFAULT_HANDLERS:
        DEFAULT_HANDLERS = default_handlers()
    return DEFAULT_HANDLERS


async def enqueue_job(
    conn: Connection,
    *,
    key: str,
    kind: str,
    payload: dict[str, Any] | None = None,
    run_at: Any = None,
) -> str:
    """Insert one job in the caller's transaction. A duplicate key returns the existing id.

    `jobs.trace_context` remains nullable for historical rows; new jobs leave it empty and
    each attempt starts its own native Pydantic AI trace.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO jobs (key, kind, payload, run_at)
        VALUES ($1, $2, $3, coalesce($4, now()))
        ON CONFLICT (key) DO NOTHING
        RETURNING id
        """,
        key,
        kind,
        payload or {},
        run_at,
    )
    if row is not None:
        return str(row["id"])
    existing = await conn.fetchrow("SELECT id FROM jobs WHERE key = $1", key)
    if existing is None:  # pragma: no cover - the conflict row vanished
        raise RuntimeError(f"job key {key!r} exists but its row is gone")
    return str(existing["id"])


async def recover_interrupted_jobs(conn: Connection) -> int:
    """At boot, anything still marked running was interrupted: run it again from the top."""
    rows = await conn.fetch(
        """
        UPDATE jobs
        SET status = 'pending', started_at = NULL, updated_at = now()
        WHERE status = 'running'
        RETURNING id
        """
    )
    return len(rows)


async def enqueue_risk_check(conn: Connection, group_id: str) -> str | None:
    """Queue a projection check after a write, collapsed to one per short burst.

    A statement import creates many write groups. Every one asks for a check, but a job that
    started in the last ten seconds already sees the writes that committed before it, so a small
    debounce keeps one import from filling the queue without delaying a normal write.
    """
    recent = await conn.fetchrow(
        """
        SELECT 1 FROM jobs
        WHERE kind = 'risk_check'
          AND payload ->> 'reason' = 'write'
          AND created_at > now() - interval '10 seconds'
        LIMIT 1
        """
    )
    if recent is not None:
        return None
    return await enqueue_job(
        conn,
        key=f"risk_check:write:{group_id}",
        kind="risk_check",
        payload={"reason": "write"},
    )


async def _claim_one(conn: Connection) -> dict[str, Any] | None:
    async with conn.transaction():
        row = await conn.fetchrow(
            """
            SELECT *
            FROM jobs
            WHERE status = 'pending' AND run_at <= now()
            ORDER BY run_at, created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
        if row is None:
            return None
        updated = await conn.fetchrow(
            """
            UPDATE jobs
            SET status = 'running', started_at = now(), updated_at = now()
            WHERE id = $1
            RETURNING *
            """,
            row["id"],
        )
        return updated


async def _finish(
    conn: Connection,
    job_id: str,
    *,
    status: str,
    error: str | None = None,
    timed_out: bool = False,
    result: Any = None,
) -> None:
    await conn.execute(
        """
        UPDATE jobs
        SET status = $1,
            finished_at = now(),
            updated_at = now(),
            last_error = coalesce($2, last_error),
            timed_out_at = CASE WHEN $3 THEN now() ELSE timed_out_at END,
            result = $5
        WHERE id = $4
        """,
        status,
        error,
        timed_out,
        job_id,
        # A handler may return dates or Decimals; the stored result is for display only.
        json.loads(json.dumps(result, default=str)) if isinstance(result, dict) else None,
    )


async def _fail(
    conn: Connection,
    job: dict[str, Any],
    error: str,
    *,
    timed_out: bool,
    final: bool = False,
) -> None:
    attempts = int(job["attempts"]) + 1
    failed_notification = None
    if final or attempts >= MAX_ATTEMPTS:
        await conn.execute(
            """
            UPDATE jobs
            SET attempts = $1, status = 'failed', finished_at = now(), updated_at = now(),
                last_error = $2, timed_out_at = CASE WHEN $3 THEN now() ELSE timed_out_at END
            WHERE id = $4
            """,
            attempts,
            error[:2000],
            timed_out,
            job["id"],
        )
        from arno.notifications import create_notification

        failed_notification = await create_notification(
            conn,
            kind="job_failed",
            title=f"{job['kind']} failed",
            body=(
                "It stopped without retrying. Settings shows the job and why."
                if final and attempts < MAX_ATTEMPTS
                else "It was tried three times. Settings shows the job status and the last error."
            ),
            data={"job_id": job["id"], "kind": job["kind"]},
            dedupe_key=f"job_failed:{job['id']}",
        )
    else:
        delay = min(300, 5 * (2**attempts))
        await conn.execute(
            """
            UPDATE jobs
            SET attempts = $1, status = 'pending', started_at = NULL, updated_at = now(),
                run_at = now() + $2::float8 * interval '1 second',
                last_error = $3,
                timed_out_at = CASE WHEN $4 THEN now() ELSE timed_out_at END
            WHERE id = $5
            """,
            attempts,
            float(delay),
            error[:2000],
            timed_out,
            job["id"],
        )
    if failed_notification is not None:
        from arno.notifications import send_push_for_kind

        await send_push_for_kind(conn, "job_failed")


class JobRunner:
    """The one loop. `concurrency` bounds a long parse run beside a reminder."""

    def __init__(
        self,
        *,
        concurrency: int = 2,
        poll_seconds: float = 1.0,
        registry: Mapping[str, Handler] | None = None,
        timeouts: Mapping[str, float | None] | None = None,
    ) -> None:
        self.concurrency = max(1, concurrency)
        self.poll_seconds = poll_seconds
        self.registry = dict(registry or handlers())
        self.timeouts = {**DEFAULT_TIMEOUTS, **(dict(timeouts or {}))}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._running: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._loop(), name="arno-jobs")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError, Exception:  # noqa: BLE001 - shutdown
                pass
            self._task = None
        for task in list(self._running):
            task.cancel()
        if self._running:
            await asyncio.gather(*self._running, return_exceptions=True)
        self._running.clear()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive a bad job row
                logger.exception("job loop tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def _prepare_schedules(self) -> int:
        """Seed and sync the recurring schedules, then turn due ones into jobs."""
        async with connect() as conn:
            from arno.jobs.schedules import (
                enqueue_due_schedules,
                ensure_builtin_schedules,
                sync_scheduled_questions,
            )

            await ensure_builtin_schedules(conn)
            await sync_scheduled_questions(conn)
            return await enqueue_due_schedules(conn)

    async def tick(self) -> int:
        """Claim and run as many jobs as the concurrency limit allows."""
        await self._prepare_schedules()
        async with connect() as conn:
            free = self.concurrency - len(self._running)
            jobs: list[dict[str, Any]] = []
            if free > 0:
                for _ in range(free):
                    job = await _claim_one(conn)
                    if job is None:
                        break
                    jobs.append(job)
        for job in jobs:
            task = asyncio.create_task(self._run_job(job), name=f"job-{job['key']}")
            self._running.add(task)
            task.add_done_callback(self._running.discard)
        return len(jobs)

    async def run_once(self) -> int:
        """Claim every currently due job and await it. Tests use this instead of the loop."""
        claimed = 0
        await self._prepare_schedules()
        while True:
            async with connect() as conn:
                job = await _claim_one(conn)
            if job is None:
                return claimed
            await self._run_job(job)
            claimed += 1

    async def _run_job(self, job: dict[str, Any]) -> None:
        await self._execute_job(job)

    async def _execute_job(self, job: dict[str, Any]) -> None:
        kind = str(job["kind"])
        job_id = str(job["id"])
        handler = self.registry.get(kind)
        if handler is None:
            async with connect() as conn:
                await _finish(conn, job_id, status="failed", error=f"no handler for {kind}")
            return
        timeout = self.timeouts.get(kind, 120)
        try:
            payload = dict(job.get("payload") or {})
            with background_inference(), cost_identity(workload=f"job:{job_id}"):
                if inspect.iscoroutinefunction(handler):
                    result = await asyncio.wait_for(handler(payload), timeout=timeout)
                else:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(handler, payload), timeout=timeout
                    )
            async with connect() as conn:
                await _finish(conn, job_id, status="done", result=result)
        except asyncio.CancelledError:
            raise
        except JobGaveUp as exc:
            logger.warning("job %s gave up: %s", job["key"], exc)
            async with connect() as conn:
                await _fail(conn, job, str(exc), timed_out=False, final=True)
        except COST_STOP_ERRORS as exc:
            logger.warning(
                "job %s stopped by inference accounting (%s)", job["key"], type(exc).__name__
            )
            async with connect() as conn:
                await _fail(conn, job, type(exc).__name__, timed_out=False, final=True)
        except TimeoutError:
            after = f" after {float(timeout):.0f}s" if timeout is not None else ""
            logger.warning("job %s timed out%s", job["key"], after)
            async with connect() as conn:
                await _fail(conn, job, f"timed out{after}", timed_out=True)
        except Exception as exc:  # noqa: BLE001 - every handler failure is retried
            logger.exception("job %s failed", job["key"])
            async with connect() as conn:
                await _fail(conn, job, f"{type(exc).__name__}: {exc}", timed_out=False)


def next_backoff(attempts: int) -> timedelta:
    """Visible for tests: 10s, then 20s, then the job is failed."""
    return timedelta(seconds=min(300, 5 * (2 ** max(1, attempts))))
