"""The one spend ledger: a strict Postgres counter store and its pricing policy.

Every paid path writes to the same `spend_counters` tables, so the daily ceiling is shared
instead of reset per path. The store is lazy: a process can boot with Postgres unreachable and
still serve read-only work, but the first paid inference that needs a counter fails with
`SpendStoreError` rather than running uncapped. A store read and the increment after it are
separate round trips, so a budget is a runaway brake, not a reservation.

`make_spend_store` and `SharedSpendStore` are the only constructors. Both wrap the native
`PostgresSpendStore` with the batch recovery horizon and the same price callback, so every
construction site agrees on what a call costs and how long a replay token is remembered. Direct
calls (speech, batch settlement) use `SpendLimits.status`/`exhausted`, `BudgetStatus.key`, and
`BatchSpendStore.add_many` with a `SpendEntry`; no private key format is reconstructed here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

import asyncpg
from pydantic_ai.exceptions import UsageLimitExceeded, UserError
from pydantic_ai.messages import ModelResponse
from pydantic_ai_harness.spend import (
    Budget,
    BudgetStatus,
    PostgresSpendStore,
    SpendEntry,
    SpendLimitExceeded,
    SpendLimits,
)

from arno.agent.pricing import CostObservation, price_response
from arno.config import get_settings
from arno.db.conn import conninfo_to_dict

logger = logging.getLogger("arno.spend")

DAILY_CEILING_USD = Decimal("20")
SHARED_DAILY_BUDGET = "shared daily"
# Batch submission pre-accounts an estimate and collection settles the delta. The native replay
# marker defaults to one hour and a day counter normally expires after 48 hours; neither covers a
# delayed batch, so the shared day never expires and a token is remembered for this long.
# shortcut: a fixed week, widen it only if a supported batch can stay recoverable longer.
SPEND_DEDUP_RETAIN = timedelta(days=7)


class SpendStoreError(RuntimeError):
    """The budget ledger could not be read or written, so paid inference must stop.

    A committed but unacknowledged write has an uncertain outcome. Callers must not retry the
    model call; the job runner maps this to a nonretryable outcome.
    """


# These terminate inference rather than invite a tool/model/job to spend again.
COST_STOP_ERRORS = (SpendStoreError, SpendLimitExceeded, UserError, UsageLimitExceeded)


def ceiling_name(exc: BaseException) -> str | None:
    """The budget name a `SpendLimitExceeded` stopped on, or `None` for another error."""
    if type(exc).__name__ != "SpendLimitExceeded":
        return None
    message = str(exc)
    marker = "Budget '"
    start = message.find(marker)
    if start < 0:
        return "budget"
    start += len(marker)
    end = message.find("'", start)
    return message[start:end] if end > start else "budget"


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
    async def acquire(self) -> AsyncGenerator[Any]:
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


class StrictSpendStore:
    """Translate every backend failure into `SpendStoreError`.

    A read that fails must stop a paid request, and a write that fails must not disappear. The
    native store already refuses to fail open; this wrapper gives callers one exception type to
    classify as nonretryable after a dispatch.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def get_many(self, keys: Sequence[str]) -> Mapping[str, Any]:
        try:
            return cast(Mapping[str, Any], await self._inner.get_many(keys))
        except SpendStoreError:
            raise
        except Exception as exc:  # noqa: BLE001 - the boundary is the point
            raise SpendStoreError("The spend ledger could not be read.") from exc

    async def add_many(self, entries: Sequence[SpendEntry]) -> Mapping[str, Any]:
        try:
            return cast(Mapping[str, Any], await self._inner.add_many(entries))
        except SpendStoreError:
            raise
        except Exception as exc:  # noqa: BLE001 - the boundary is the point
            raise SpendStoreError("The spend ledger could not be written.") from exc


class SharedSpendStore:
    """The process-wide ledger used when a caller does not pass one.

    The engine and CLI build an agent without a store; this gives them the same `$20/day`
    counter the API uses. It opens its connection lazily, so read-only pages and a process that
    only serves them do not depend on Postgres at boot. A paid request that cannot read or write
    the counter gets `SpendStoreError`, never an uncapped run.
    """

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn
        self._store: StrictSpendStore | None = None
        self._pool: _SingleConnectionPool | None = None

    def _address(self) -> str:
        if self._dsn:
            return self._dsn
        dsn = get_settings().database_url
        conninfo_to_dict(dsn)
        return dsn

    async def _resolve(self) -> StrictSpendStore:
        if self._store is None:
            self._pool = _SingleConnectionPool(self._address())
            self._store = StrictSpendStore(
                PostgresSpendStore(self._pool, dedup_retain=SPEND_DEDUP_RETAIN)
            )
        return self._store

    def close(self) -> None:
        """Release the lazily opened connection. Later calls reopen, so close is idempotent."""
        pool, self._pool = self._pool, None
        self._store = None
        if pool is not None:
            pool.close()

    async def get_many(self, keys: Sequence[str]) -> Mapping[str, Any]:
        try:
            store = await self._resolve()
        except SpendStoreError:
            raise
        except Exception as exc:  # noqa: BLE001 - a store that cannot open is unavailable
            raise SpendStoreError("The spend ledger connection could not be opened.") from exc
        return await store.get_many(keys)

    async def add_many(self, entries: Sequence[SpendEntry]) -> Mapping[str, Any]:
        try:
            store = await self._resolve()
        except SpendStoreError:
            raise
        except Exception as exc:  # noqa: BLE001 - a store that cannot open is unavailable
            raise SpendStoreError("The spend ledger connection could not be opened.") from exc
        return await store.add_many(entries)


async def make_spend_store(dsn: str) -> tuple[Any, Any]:
    """Return `(store, pool)` for one process, or a lazy strict store if the pool cannot start.

    A pool that cannot start must not make the app refuse its read-only pages, so the lazy store
    is returned instead of `None`. Its first paid request fails closed with `SpendStoreError`.
    """
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4, command_timeout=10)
    except Exception:  # noqa: BLE001 - read-only pages stay available
        logger.exception("spend store pool did not start; paid work will be refused")
        return SharedSpendStore(dsn), None
    store = StrictSpendStore(PostgresSpendStore(pool, dedup_retain=SPEND_DEDUP_RETAIN))
    return store, pool


@asynccontextmanager
async def spend_store_scope(store: Any = None) -> AsyncGenerator[Any]:
    """Yield `store`, or one strict shared store for this run that is closed afterwards.

    `None` never means uncapped: it opens the shared ledger lazily. A host command that reaches
    a paid call with Postgres down gets `SpendStoreError` from the store, not a free request.
    """
    if store is not None:
        yield store
        return
    opened = SharedSpendStore()
    try:
        yield opened
    finally:
        opened.close()


def shared_daily_budget() -> Budget:
    """The `$20/day` ceiling promised across chat, schedules, parse, vision, and briefing.

    All paths use this name, window, and retention, so they read and write one Postgres counter.
    `forever` keeps a delayed batch correction from resurrecting an expired daily counter.
    """
    return Budget(
        usd=DAILY_CEILING_USD,
        window="day",
        name=SHARED_DAILY_BUDGET,
        retain="forever",
    )


def _shared_price(response: ModelResponse) -> Decimal | None:
    """Price a configured response; an explicit offline test double is a zero.

    Every production model is wrapped with its configured provider and billing identity, so it
    always carries the `arno_cost` marker. An unmarked response from `TestModel`/`FunctionModel`
    is an explicitly offline synthetic call and must not be forced through a paid catalog
    lookup; a genuine unmarked provider response still returns `None` and raises.
    """
    details = response.provider_details or {}
    if not isinstance(details.get("arno_cost"), dict):
        provider = (response.provider_name or "").strip().lower()
        if provider in {"function", "test"} or (not provider and not response.provider_url):
            return Decimal(0)
    return price_response(response)


def spend_limits(store: Any, *, budgets: list[Budget] | None = None) -> SpendLimits | None:
    """`SpendLimits` with the one price callback and unknown-price policy.

    `store=None` is only for an explicitly constructed offline test agent; production callers
    always supply a store. All real construction sites (main, summary, and every domain agent)
    come through this factory.
    """
    if store is None:
        return None
    return SpendLimits(
        budgets=list(budgets or [shared_daily_budget()]),
        store=store,
        price=_shared_price,
        on_unpriced="raise",
    )


async def shared_day_status(store: Any) -> BudgetStatus:
    """The shared day's status, raising rather than inspecting nothing if it cannot be read."""
    limits = spend_limits(store, budgets=[shared_daily_budget()])
    if limits is None:
        raise SpendStoreError("A paid direct call needs the shared spend store.")
    statuses = await limits.status()
    for status in statuses:
        if status.budget.name == SHARED_DAILY_BUDGET:
            return status
    raise SpendStoreError("The shared daily budget could not be resolved.")


async def ensure_shared_day_available(store: Any) -> BudgetStatus:
    """Refuse a paid direct call when the shared day is exhausted or unreadable."""
    status = await shared_day_status(store)
    if status.exhausted:
        raise SpendLimitExceeded(
            f"Budget {SHARED_DAILY_BUDGET!r} exhausted for this day: "
            f"spent ${status.spent.usd} of ${status.budget.usd}."
        )
    return status


def _tokens(usage: Mapping[str, int | float] | None) -> int:
    """Billable language tokens: cache and reasoning are already inside these two buckets."""
    if not usage:
        return 0
    return int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))


async def post_direct_call(store: Any, observation: CostObservation, *, token: str) -> BudgetStatus:
    """Post one priced direct call (speech, a batch item's parent) to the shared day.

    `token` makes an uncertain retry of the same call at most once. An unknown amount still
    counts the request and its tokens as unpriced; it is never silently free.
    """
    status = await shared_day_status(store)
    priced = observation.usd is not None and observation.complete
    await store.add_many(
        [
            SpendEntry(
                key=status.key,
                usd=observation.usd if observation.usd is not None else Decimal(0),
                tokens=_tokens(observation.usage),
                requests=1,
                unpriced=0 if priced else 1,
                ttl=status.budget.ttl,
                token=token,
            )
        ]
    )
    return status


__all__ = [
    "DAILY_CEILING_USD",
    "SHARED_DAILY_BUDGET",
    "SPEND_DEDUP_RETAIN",
    "SharedSpendStore",
    "SpendStoreError",
    "StrictSpendStore",
    "ceiling_name",
    "ensure_shared_day_available",
    "make_spend_store",
    "post_direct_call",
    "shared_daily_budget",
    "shared_day_status",
    "spend_limits",
    "spend_store_scope",
]
