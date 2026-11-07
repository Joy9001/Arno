"""The spend store's asyncpg pool.

The app's chat path gets its bootstrap store. Scheduled and host paths (parse, categorizer,
vision, briefing, photo) open a store for the length of one model run. They all write to the
same Postgres ledger, so the daily ceiling is shared instead of reset per path, and no pool
outlives the loop or the run that opened it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from fin_agent.config import get_settings
from fin_agent.db.conn import conninfo_to_dict

logger = logging.getLogger("fin_agent.spend")

_CEILING_RE = re.compile(r"Budget '([^']+)'")


def ceiling_name(exc: BaseException) -> str | None:
    """The budget name a `SpendLimitExceeded` stopped on, or `None` for another error."""
    if type(exc).__name__ != "SpendLimitExceeded":
        return None
    match = _CEILING_RE.search(str(exc))
    return match.group(1) if match else "budget"


class _SingleConnectionPool:
    """One lazily opened asyncpg connection exposed as a pool for `PostgresSpendStore`.

    A `run_sync` CLI turn can use a fresh event loop per call, and a pooled connection belongs
    to the loop that opened it. One connection per loop keeps the CLI path working without a
    pool per call; spend operations are short and serial, so one connection is enough. The
    owner closes it at CLI teardown; a replaced loop terminates the old connection first.
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._connection: Any = None
        self._loop: Any = None
        self._lock: asyncio.Lock | None = None
        self._lock_loop: Any = None

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        async with self._lock:
            if self._connection is not None and self._loop is not loop:
                # The old connection belongs to a loop that is gone; terminate it rather than
                # leave a leaked socket behind when a CLI process runs several event loops.
                try:
                    self._connection.terminate()
                except Exception:  # noqa: BLE001 - cleanup of an already-dead loop
                    pass
                self._connection = None
            if self._connection is None or self._loop is not loop or self._connection.is_closed():
                self._connection = await asyncpg.connect(self.dsn, timeout=10, command_timeout=10)
                self._loop = loop
            try:
                yield self._connection
            except Exception:
                if self._connection is not None and self._connection.is_closed():
                    self._connection = None
                raise

    def close(self) -> None:
        """Terminate the connection now. `terminate` is sync, so teardown needs no running loop."""
        connection, self._connection = self._connection, None
        self._loop = None
        if connection is not None:
            try:
                connection.terminate()
            except Exception:  # noqa: BLE001 - an already-dead connection is closed enough
                pass


class _UnavailableStore:
    """Fail-open counters: read as zero and writes are dropped while Postgres is unreachable."""

    @staticmethod
    def _spent() -> Any:
        from pydantic_ai_harness.spend import Spent

        return Spent()

    async def get_many(self, keys: Sequence[str]) -> Mapping[str, Any]:
        return {key: self._spent() for key in keys}

    async def add_many(self, entries: Sequence[Any]) -> Mapping[str, Any]:
        return {entry.key: self._spent() for entry in entries}


class SharedSpendStore:
    """The process-wide ledger store used when a caller does not pass one.

    The engine and CLI build an agent without a store; this gives them the same `$20/day`
    counter the API uses. A database that is down yields zero readings, so a host command still
    answers, exactly like the API's fail-open read-only panels.
    """

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn
        self._store: Any = None
        self._pool: _SingleConnectionPool | None = None

    def _address(self) -> str:
        if self._dsn:
            return self._dsn
        dsn = get_settings().database_url
        conninfo_to_dict(dsn)
        return dsn

    async def _resolve(self) -> Any:
        if self._store is None:
            try:
                from pydantic_ai_harness.spend import PostgresSpendStore

                self._pool = _SingleConnectionPool(self._address())
                self._store = PostgresSpendStore(self._pool)
            except Exception:  # noqa: BLE001 - an uncapped command still answers
                logger.warning("shared spend store unavailable; running uncapped")
                self._store = _UnavailableStore()
        return self._store

    def close(self) -> None:
        """Release the lazily opened connection. Later calls reopen, so close is idempotent."""
        pool, self._pool = self._pool, None
        self._store = None
        if pool is not None:
            pool.close()

    async def get_many(self, keys: Sequence[str]) -> Mapping[str, Any]:
        try:
            return await (await self._resolve()).get_many(keys)
        except Exception:  # noqa: BLE001 - telemetry-adjacent, never block a command
            logger.warning("shared spend read failed; running uncapped")
            return await _UnavailableStore().get_many(keys)

    async def add_many(self, entries: Sequence[Any]) -> Mapping[str, Any]:
        try:
            return await (await self._resolve()).add_many(entries)
        except Exception:  # noqa: BLE001 - a failed ledger write must not fail the answer
            logger.warning("shared spend write failed; the counter is understated")
            return await _UnavailableStore().add_many(entries)


async def make_spend_store(dsn: str) -> tuple[Any, Any]:
    """Return `(store, pool)`, or `(None, None)` when Postgres is unreachable at boot."""
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4, command_timeout=10)
    except Exception:  # noqa: BLE001 - the app still serves read-only panels
        logger.exception("spend store pool did not start")
        return None, None
    from pydantic_ai_harness.spend import PostgresSpendStore

    return PostgresSpendStore(pool), pool


@asynccontextmanager
async def spend_store_scope(store: Any = None) -> AsyncIterator[Any]:
    """Yield `store`, or a shared ledger store for this run that is closed afterwards.

    `None` means the database was unreachable: the work continues uncapped rather than
    failing, the same fail-open rule the API uses for its read-only panels.
    """
    if store is not None:
        yield store
        return
    opened, pool = await make_spend_store(get_settings().database_url)
    try:
        yield opened
    finally:
        if pool is not None:
            await pool.close()


__all__ = ["SharedSpendStore", "ceiling_name", "make_spend_store", "spend_store_scope"]
