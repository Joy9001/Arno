"""The daily backup (`pg_dump | restic backup`) and its staleness alert: once per outage."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from typing import Any

from arno.backup_restore import DUMP_NAME, find_binary, pg_env
from arno.config import get_settings
from arno.db.conn import Connection, connect
from arno.ledger import settings as settings_mod
from arno.ledger.settings import BACKUP_ALERT_AFTER, BACKUP_DUE_AFTER
from arno.notifications import create_notification, send_push_for_kind

logger = logging.getLogger("arno.jobs.backup")
# The check runs once a day, so a success a few minutes short of 24 hours old must not skip a day.
# A stalled upload must not hold a runner slot forever; the next daily check retries.
RESTIC_TIMEOUT_SECONDS = 3600
RUN_AFTER = BACKUP_DUE_AFTER - timedelta(hours=2)


async def _no_success_clock(conn: Connection) -> datetime | None:
    """The earliest signal that a backup was expected: the install, or its first attempt.

    With no success to age, "48 hours with no success" is measured from when there was first
    something to succeed at, so a fresh install does not alert before its first backup has run.
    """
    first_attempt = await conn.fetchval("SELECT min(started_at) FROM backup_runs")
    installed = await conn.fetchval("SELECT created_at FROM settings WHERE id = 1")
    candidates = [value for value in (first_attempt, installed) if value is not None]
    return min(candidates) if candidates else None


async def backup_alert(conn: Connection, *, now: datetime | None = None) -> dict[str, Any] | None:
    status = await settings_mod.backup_status(conn, now=now)
    if not status["overdue"]:
        return None
    if status["last_success_at"] is None:
        clock = await _no_success_clock(conn)
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        if clock is None or current < clock + BACKUP_ALERT_AFTER:
            return None
    last = status["last_success_at"] or "never"
    key = f"backup_stale:{last}"
    if status["last_success_at"] is None:
        body = "No successful backup is recorded yet. No dump has been shipped."
    else:
        hours = round((status["last_success_age_seconds"] or 0) / 3600)
        body = (
            f"The last successful backup finished {hours} hours ago, past the 48-hour alert "
            "line. Arno retries at its next daily check."
        )
    return await create_notification(
        conn,
        kind="backup_stale",
        title="Backups have stopped",
        body=body,
        data={
            "last_success_at": status["last_success_at"],
            "last_success_age_seconds": status["last_success_age_seconds"],
            "due_at": status["due_at"],
            "alert_at": status["alert_at"],
        },
        dedupe_key=key,
    )


def _ship(dsn: str, repository: str, password: str) -> tuple[int, str]:
    """Stream `pg_dump` into a restic snapshot, prune, and return (dump bytes, snapshot id).

    Errors are generic on purpose: restic's own messages can carry the repository URL's secrets.
    """
    pg_dump, restic = find_binary("PG_DUMP", "pg_dump"), find_binary("RESTIC", "restic")
    env = os.environ | {"RESTIC_REPOSITORY": repository, "RESTIC_PASSWORD": password}

    def run(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [restic, *args],
            capture_output=True,
            env=env,
            check=False,
            timeout=RESTIC_TIMEOUT_SECONDS,
            **kwargs,
        )

    if run("snapshots", "--json").returncode != 0 and run("init").returncode != 0:
        raise RuntimeError("restic init failed")
    # Grants stay in the dump: they are what make a restored database usable.
    # stderr goes to a file, not a pipe: an unread full pipe would block pg_dump forever.
    with (
        tempfile.TemporaryFile() as dump_stderr,
        subprocess.Popen(
            [pg_dump, "--no-owner", "--format=custom"],
            stdout=subprocess.PIPE,
            stderr=dump_stderr,
            env=pg_env(dsn),
        ) as dump,
    ):
        assert dump.stdout  # a PIPE
        try:
            shipped = run(
                *("backup", "--json", "--stdin", "--stdin-filename", DUMP_NAME),
                *("--tag", "arno", "--host", "arno"),
                stdin=dump.stdout,
            )
        except subprocess.TimeoutExpired:  # `subprocess.run` already killed restic
            dump.kill()
            dump.wait()
            raise RuntimeError("restic backup timed out") from None
        dump.stdout.close()
        dump.wait()
        dump_stderr.seek(0)
        dump_error = dump_stderr.read().decode("utf-8", "replace")
    summary: dict[str, Any] = {}
    for line in shipped.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("{") and '"summary"' in line:
            summary = json.loads(line)
    if shipped.returncode != 0 or not summary:  # restic first: pg_dump then dies of SIGPIPE
        raise RuntimeError("restic backup failed")
    if dump.returncode != 0:
        logger.error("pg_dump failed: %s", dump_error.strip()[-1000:])
        run("forget", summary["snapshot_id"], "--prune")  # a truncated dump must never be `latest`
        raise RuntimeError("pg_dump failed")
    run("forget", "--keep-daily", "7", "--keep-weekly", "4", "--group-by", "host", "--prune")
    return summary["total_bytes_processed"], summary["snapshot_id"]


async def run_backup(conn: Connection) -> bool:
    """Take one backup and record it in `backup_runs`; False when it failed."""
    repository, password = os.environ.get("RESTIC_REPOSITORY"), os.environ.get("RESTIC_PASSWORD")
    if not (repository and password):
        logger.warning("RESTIC_REPOSITORY and RESTIC_PASSWORD are not both set; no backup taken")
        return False
    started = datetime.now(UTC)
    size = snapshot = error = None
    try:
        size, snapshot = await asyncio.to_thread(
            _ship, get_settings().database_url, repository, password
        )
    except Exception as exc:
        # Only our own messages are safe to store: anything else may carry the repository URL.
        error = (
            str(exc) if isinstance(exc, RuntimeError) else f"backup failed: {type(exc).__name__}"
        )
        logger.exception("backup failed")
    finished = datetime.now(UTC)
    await conn.execute(
        """
        INSERT INTO backup_runs
            (started_at, finished_at, status, dump_bytes, duration_ms, restic_snapshot, error)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        started,
        finished,
        "failed" if error else "success",
        size,
        int((finished - started).total_seconds() * 1000),
        snapshot,
        error,
    )
    return error is None


async def handle_backup_check(payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    async with connect() as conn:
        age = (await settings_mod.backup_status(conn))["last_success_age_seconds"]
        backed_up = None
        try:
            if age is None or age >= RUN_AFTER.total_seconds():
                backed_up = await run_backup(conn)
        finally:  # the alert never depends on the backup finishing
            row = await backup_alert(conn)
            if row is not None:
                await send_push_for_kind(conn, "backup_stale")
        return {"backed_up": backed_up, "alert_id": row["id"] if row else None}


__all__ = ["backup_alert", "handle_backup_check", "run_backup"]
