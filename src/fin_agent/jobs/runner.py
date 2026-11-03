"""A `jobs` table and one loop.

One process owns the loop, so a `running` row at boot was interrupted and goes back to
`pending`. A job can be enqueued in the same transaction as its data. Keys are unique, so a
restart cannot send the same job twice. A failing job retries three times with backoff, then
turns `failed` and lands in the inbox.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import closing
from datetime import timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from fin_agent.db.conn import connect

logger = logging.getLogger("fin_agent.jobs")

Handler = Callable[[dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]
MAX_ATTEMPTS = 3
DEFAULT_TIMEOUTS = {
    # A 12-month statement can spend five minutes in the parse run, then a few more
    # in batched categorization before review.
    "import_statement": 1200,
    "recompute_baselines": 120,
    "weekly_briefing": 180,
    "reminders": 60,
    "risk_check": 120,
    "pattern_detection": 120,
    "backup_check": 30,
    "scheduled_question": 300,
}


def default_handlers() -> dict[str, Handler]:
    """Imported lazily, so the job module does not import the whole agent at boot."""
    from fin_agent.importers.baselines import handle_recompute_baselines
    from fin_agent.importers.pipeline import handle_import_job
    from fin_agent.jobs.backup import handle_backup_check
    from fin_agent.jobs.briefing import handle_weekly_briefing
    from fin_agent.jobs.patterns import handle_pattern_detection
    from fin_agent.jobs.questions import handle_scheduled_question
    from fin_agent.jobs.reminders import handle_reminders
    from fin_agent.jobs.risk import handle_risk_check

    return {
        "import_statement": handle_import_job,
        "recompute_baselines": handle_recompute_baselines,
        "weekly_briefing": handle_weekly_briefing,
        "reminders": handle_reminders,
        "risk_check": handle_risk_check,
        "pattern_detection": handle_pattern_detection,
        "backup_check": handle_backup_check,
        "scheduled_question": handle_scheduled_question,
    }


DEFAULT_HANDLERS: dict[str, Handler] = {}


def handlers() -> dict[str, Handler]:
    global DEFAULT_HANDLERS
    if not DEFAULT_HANDLERS:
        DEFAULT_HANDLERS = default_handlers()
    return DEFAULT_HANDLERS


def enqueue_job(
    conn: psycopg.Connection,
    *,
    key: str,
    kind: str,
    payload: dict[str, Any] | None = None,
    run_at: Any = None,
) -> str:
    """Insert one job in the caller's transaction. A duplicate key returns the existing id."""
    row = conn.execute(
        """
        INSERT INTO jobs (key, kind, payload, run_at)
        VALUES (%s, %s, %s, coalesce(%s, now()))
        ON CONFLICT (key) DO NOTHING
        RETURNING id
        """,
        (key, kind, Jsonb(payload or {}), run_at),
    ).fetchone()
    if row is not None:
        return str(row["id"])
    existing = conn.execute("SELECT id FROM jobs WHERE key = %s", (key,)).fetchone()
    if existing is None:  # pragma: no cover - the conflict row vanished
        raise RuntimeError(f"job key {key!r} exists but its row is gone")
    return str(existing["id"])


def recover_interrupted_jobs(conn: psycopg.Connection) -> int:
    """At boot, anything still marked running was interrupted: run it again from the top."""
    rows = conn.execute(
        """
        UPDATE jobs
        SET status = 'pending', started_at = NULL, updated_at = now()
        WHERE status = 'running'
        RETURNING id
        """
    ).fetchall()
    return len(rows)


def enqueue_risk_check(conn: psycopg.Connection, group_id: str) -> str | None:
    """Queue a projection check after a write, collapsed to one per short burst.

    A statement import creates many write groups. Every one asks for a check, but a job that
    started in the last ten seconds already sees the writes that committed before it, so a small
    debounce keeps one import from filling the queue without delaying a normal write.
    """
    recent = conn.execute(
        """
        SELECT 1 FROM jobs
        WHERE kind = 'risk_check'
          AND payload ->> 'reason' = 'write'
          AND created_at > now() - interval '10 seconds'
        LIMIT 1
        """
    ).fetchone()
    if recent is not None:
        return None
    return enqueue_job(
        conn,
        key=f"risk_check:write:{group_id}",
        kind="risk_check",
        payload={"reason": "write"},
    )


def _claim_one(conn: psycopg.Connection) -> dict[str, Any] | None:
    with conn.transaction():
        row = conn.execute(
            """
            SELECT *
            FROM jobs
            WHERE status = 'pending' AND run_at <= now()
            ORDER BY run_at, created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        updated = conn.execute(
            """
            UPDATE jobs
            SET status = 'running', started_at = now(), updated_at = now()
            WHERE id = %s
            RETURNING *
            """,
            (row["id"],),
        ).fetchone()
        return dict(updated)


def _finish(
    conn: psycopg.Connection,
    job_id: str,
    *,
    status: str,
    error: str | None = None,
    timed_out: bool = False,
) -> None:
    conn.execute(
        """
        UPDATE jobs
        SET status = %s,
            finished_at = now(),
            updated_at = now(),
            last_error = coalesce(%s, last_error),
            timed_out_at = CASE WHEN %s THEN now() ELSE timed_out_at END
        WHERE id = %s
        """,
        (status, error, timed_out, job_id),
    )
    conn.commit()


def _fail(conn: psycopg.Connection, job: dict[str, Any], error: str, *, timed_out: bool) -> None:
    attempts = int(job["attempts"]) + 1
    failed_notification = None
    if attempts >= MAX_ATTEMPTS:
        conn.execute(
            """
            UPDATE jobs
            SET attempts = %s, status = 'failed', finished_at = now(), updated_at = now(),
                last_error = %s, timed_out_at = CASE WHEN %s THEN now() ELSE timed_out_at END
            WHERE id = %s
            """,
            (attempts, error[:2000], timed_out, job["id"]),
        )
        from fin_agent.notifications import create_notification

        failed_notification = create_notification(
            conn,
            kind="job_failed",
            title=f"{job['kind']} failed",
            body="It was tried three times. Settings shows the job status and the last error.",
            data={"job_id": job["id"], "kind": job["kind"]},
            dedupe_key=f"job_failed:{job['id']}",
        )
        import_id = (job.get("payload") or {}).get("import_id")
        if job["kind"] == "import_statement" and import_id:
            conn.execute(
                """
                UPDATE imports
                SET status = 'failed', reason = %s, updated_at = now()
                WHERE id = %s AND status = 'parsing'
                """,
                (f"The import failed three times: {error[:300]}", import_id),
            )
    else:
        delay = min(300, 5 * (2**attempts))
        conn.execute(
            """
            UPDATE jobs
            SET attempts = %s, status = 'pending', started_at = NULL, updated_at = now(),
                run_at = now() + %s * interval '1 second',
                last_error = %s,
                timed_out_at = CASE WHEN %s THEN now() ELSE timed_out_at END
            WHERE id = %s
            """,
            (attempts, delay, error[:2000], timed_out, job["id"]),
        )
    conn.commit()
    if failed_notification is not None:
        from fin_agent.notifications import send_push_for_kind

        send_push_for_kind(conn, "job_failed")
        conn.commit()


class JobRunner:
    """The one loop. `concurrency` bounds a long parse run beside a reminder."""

    def __init__(
        self,
        *,
        concurrency: int = 2,
        poll_seconds: float = 1.0,
        registry: Mapping[str, Handler] | None = None,
        timeouts: Mapping[str, float] | None = None,
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
            self._task = asyncio.create_task(self._loop(), name="fin-agent-jobs")

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

    def _prepare_schedules(self) -> int:
        """Seed and sync the recurring schedules, then turn due ones into jobs."""
        with closing(connect()) as conn:
            from fin_agent.jobs.schedules import (
                enqueue_due_schedules,
                ensure_builtin_schedules,
                sync_scheduled_questions,
            )

            ensure_builtin_schedules(conn)
            sync_scheduled_questions(conn)
            enqueued = enqueue_due_schedules(conn)
            conn.commit()
        return enqueued

    async def tick(self) -> int:
        """Claim and run as many jobs as the concurrency limit allows."""
        self._prepare_schedules()
        free = self.concurrency - len(self._running)
        if free <= 0:
            return 0
        jobs: list[dict[str, Any]] = []
        with closing(connect()) as conn:
            for _ in range(free):
                job = _claim_one(conn)
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
        self._prepare_schedules()
        claimed = 0
        while True:
            with closing(connect()) as conn:
                job = _claim_one(conn)
            if job is None:
                return claimed
            await self._run_job(job)
            claimed += 1

    async def _run_job(self, job: dict[str, Any]) -> None:
        kind = str(job["kind"])
        handler = self.registry.get(kind)
        if handler is None:
            with closing(connect()) as conn:
                _finish(conn, str(job["id"]), status="failed", error=f"no handler for {kind}")
            return
        timeout = float(self.timeouts.get(kind, 120))
        try:
            payload = dict(job.get("payload") or {})
            if inspect.iscoroutinefunction(handler):
                await asyncio.wait_for(handler(payload), timeout=timeout)
            else:
                await asyncio.wait_for(asyncio.to_thread(handler, payload), timeout=timeout)
            with closing(connect()) as conn:
                _finish(conn, str(job["id"]), status="done")
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning("job %s timed out after %ss", job["key"], timeout)
            with closing(connect()) as conn:
                _fail(conn, job, f"timed out after {timeout:.0f}s", timed_out=True)
        except Exception as exc:  # noqa: BLE001 - every handler failure is retried
            logger.exception("job %s failed", job["key"])
            with closing(connect()) as conn:
                _fail(conn, job, f"{type(exc).__name__}: {exc}", timed_out=False)


def next_backoff(attempts: int) -> timedelta:
    """Visible for tests: 10s, then 20s, then the job is failed."""
    return timedelta(seconds=min(300, 5 * (2 ** max(1, attempts))))
