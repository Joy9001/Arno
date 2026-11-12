"""A direct paid call (speech, a batch parent) uses the same native counters as an agent run."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic_ai_harness.spend import SpendEntry, SpendLimitExceeded

from arno.agent.pricing import CostObservation
from arno.agent.spend import (
    SHARED_DAILY_BUDGET,
    SharedSpendStore,
    SpendStoreError,
    StrictSpendStore,
    _tokens,
    ceiling_name,
    ensure_shared_day_available,
    make_spend_store,
    post_direct_call,
)
from arno.config import get_settings


class _BrokenStore:
    async def get_many(self, keys):
        raise RuntimeError("the ledger is down")

    async def add_many(self, entries):
        raise RuntimeError("the ledger is down")


async def _clear_spend(db) -> None:
    if await db.fetchval("SELECT to_regclass('public.spend_counters') IS NOT NULL"):
        await db.execute("TRUNCATE spend_counters, spend_dedup")


@pytest.fixture(autouse=True)
async def isolated_spend_day(db):
    """The shared day now outlives a test session, so these tests must not leak a row."""
    await _clear_spend(db)
    yield
    await _clear_spend(db)


def _observation(usd: str) -> CostObservation:
    return CostObservation(
        usd=Decimal(usd),
        source="test",
        complete=True,
        price_revision="test/v1",
        usage={
            "input_tokens": 3,
            "cache_read_tokens": 2,
            "cache_write_tokens": 1,
            "output_tokens": 2,
            "output_reasoning_tokens": 1,
        },
    )


def test_token_count_does_not_double_count_cache_and_reasoning():
    usage = {
        "input_tokens": 10,
        "cache_read_tokens": 4,
        "cache_write_tokens": 2,
        "output_tokens": 5,
        "output_reasoning_tokens": 3,
        "unmeasured_requests": 0,
    }
    assert _tokens(usage) == 15


async def test_a_direct_cost_lands_on_the_shared_day_and_a_token_replays_once(db):
    settings = get_settings(refresh=True)
    store, pool = await make_spend_store(settings.database_url)
    assert pool is not None
    try:
        status = await ensure_shared_day_available(store)
        assert status.budget.name == SHARED_DAILY_BUDGET
        assert status.budget.retain == "forever"

        await post_direct_call(store, _observation("0.25"), token="stt-attempt-1")
        spent = (await store.get_many([status.key]))[status.key]
        assert (spent.usd, spent.tokens, spent.requests, spent.unpriced_requests) == (
            Decimal("0.25"),
            5,
            1,
            0,
        )

        # The same attempt presented again is a replay, not a second charge.
        await post_direct_call(store, _observation("0.25"), token="stt-attempt-1")
        spent = (await store.get_many([status.key]))[status.key]
        assert spent.usd == Decimal("0.25")
        assert spent.requests == 1

        # The shared day never expires, so a later negative correction cannot resurrect it.
        assert await db.fetchval("SELECT bool_and(expires_at IS NULL) FROM spend_counters")
    finally:
        await pool.close()


async def test_an_exhausted_shared_day_refuses_a_direct_call_and_names_the_budget(db):
    settings = get_settings(refresh=True)
    store, pool = await make_spend_store(settings.database_url)
    assert pool is not None
    try:
        status = await ensure_shared_day_available(store)
        await store.add_many([SpendEntry(key=status.key, usd=Decimal("20"))])
        with pytest.raises(SpendLimitExceeded) as raised:
            await ensure_shared_day_available(store)
        assert ceiling_name(raised.value) == SHARED_DAILY_BUDGET
    finally:
        await pool.close()


async def test_an_unreadable_store_fails_paid_inference_instead_of_reading_zero():
    with pytest.raises(SpendStoreError):
        await ensure_shared_day_available(StrictSpendStore(_BrokenStore()))


async def test_a_lazy_shared_store_that_cannot_connect_raises():
    store = SharedSpendStore("postgresql://app:x@127.0.0.1:1/arno_cost_test")
    with pytest.raises(SpendStoreError):
        await store.get_many(["shared daily|day|*|2026-11-12"])
    store.close()


async def test_shared_counters_survive_closing_and_reopening_the_store(db):
    store, pool = await make_spend_store(get_settings(refresh=True).database_url)
    assert pool is not None
    await post_direct_call(store, _observation("0.125"), token="restart-proof")
    key = (await ensure_shared_day_available(store)).key
    await pool.close()
    reopened, new_pool = await make_spend_store(get_settings().database_url)
    assert new_pool is not None
    try:
        assert (await reopened.get_many([key]))[key].usd == Decimal("0.125")
        await post_direct_call(reopened, _observation("0.125"), token="restart-proof")
        assert (await reopened.get_many([key]))[key].requests == 1
    finally:
        await new_pool.close()


async def test_concurrent_native_calls_can_overshoot_but_the_next_call_is_refused():
    import asyncio

    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai_harness.spend import Budget, InMemorySpendStore

    from arno.agent.spend import spend_limits

    admitted = 0
    both_admitted = asyncio.Event()

    async def response(messages, info):
        nonlocal admitted
        admitted += 1
        if admitted == 2:
            both_admitted.set()
        await asyncio.wait_for(both_admitted.wait(), timeout=2)
        return ModelResponse(
            parts=[TextPart("synthetic")],
            provider_details={
                "arno_cost": {"provider": "custom", "billing_mode": "reported"},
                "provider_usage": {"cost": "0.06"},
            },
        )

    store = InMemorySpendStore()
    limits = spend_limits(
        store, budgets=[Budget(usd=Decimal("0.10"), window="total", name="proof")]
    )
    assert limits is not None
    agent = Agent(FunctionModel(response), capabilities=[limits])
    await asyncio.gather(agent.run("one"), agent.run("two"))
    status = (await limits.status())[0]
    assert (await store.get_many([status.key]))[status.key].usd == Decimal("0.12")
    with pytest.raises(SpendLimitExceeded):
        await agent.run("three")
    assert admitted == 2
