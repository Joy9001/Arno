"""Background work: Postgres is the queue, asyncio only runs the work.

A task's state lives in Postgres, so a restart resumes it from `jobs.loop.recover`. Each task
holds the erase lock shared, so an erase-everything never races it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from arno.config import get_settings
from arno.db.conn import connect

logger = logging.getLogger("arno.jobs")

_tasks: set[asyncio.Task[None]] = set()


async def _run(coro: Coroutine[Any, Any, Any], name: str) -> None:
    from arno.ledger.deletion import ERASE_LOCK_KEY

    try:
        # A dedicated connection: closing it releases the session lock, even when cancelled.
        async with connect(get_settings().database_url) as lock:
            await lock.execute("SELECT pg_advisory_lock_shared($1)", ERASE_LOCK_KEY)
            await coro
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - one task's failure never reaches another
        logger.exception("background task %s failed", name)
    finally:
        coro.close()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task[None] | None:
    """Run `coro` in the background, or drop it when jobs are off (boot recovery resumes it)."""
    if not get_settings().jobs_enabled:
        coro.close()
        return None
    task = asyncio.create_task(_run(coro, name), name=name)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def drain(timeout: float) -> None:
    """Wait for running tasks on shutdown, then cancel what is left."""
    pending = set(_tasks)
    if not pending:
        return
    _, unfinished = await asyncio.wait(pending, timeout=timeout)
    for task in unfinished:
        task.cancel()
    await asyncio.gather(*unfinished, return_exceptions=True)


__all__ = ["drain", "spawn"]
