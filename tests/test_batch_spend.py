"""Batch spend: pre-accounted estimates, one settlement, and a bounded replay horizon."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness.spend import InMemorySpendStore, SpendEntry, UnpricedModelError

from arno.agent.categorizer import estimate_batch_usd
from arno.agent.spend import SPEND_DEDUP_RETAIN, SpendStoreError
from arno.importers.categorize import _settle
from arno.ledger.audit import new_id

KEY = "shared daily|day|*|2026-11-12"


async def _run(db) -> str:
    import_id = await new_id(db, "imp")
    await db.execute(
        "INSERT INTO imports (id, file_hash, status) VALUES ($1, 'hash', 'review')",
        import_id,
    )
    run_id = await new_id(db, "cat")
    await db.execute(
        "INSERT INTO categorize_runs (id, import_id, mode, accounting) "
        "VALUES ($1, $2, 'batch', '{}'::jsonb)",
        run_id,
        import_id,
    )
    return run_id


def _accounting(*, old: bool = False) -> dict[str, object]:
    posted = datetime.now(UTC) - (
        SPEND_DEDUP_RETAIN + timedelta(seconds=1) if old else timedelta(0)
    )
    return {
        "keys": [KEY],
        "estimate_usd": "0.10",
        "estimate_token": "categorize-batch-estimate:run",
        "settle_token": "categorize-batch-settle:run",
        "estimate_at": posted.isoformat(),
        "estimate_applied": True,
        "settle_applied": False,
    }


async def test_a_collection_correction_is_posted_once_after_a_marker_gap(db):
    run_id = await _run(db)
    store = InMemorySpendStore()
    await store.add_many(
        [SpendEntry(key=KEY, usd=Decimal("0.10"), requests=2, ttl=None, token="estimate")]
    )
    accounting = _accounting()

    await _settle(db, run_id, accounting, store=store, actual_usd=Decimal("0.07"))
    assert (await store.get_many([KEY]))[KEY].usd == Decimal("0.07")

    # The counter write committed but the applied marker did not. The same token must not
    # subtract the correction a second time.
    accounting["settle_applied"] = False
    await _settle(db, run_id, accounting, store=store, actual_usd=Decimal("0.07"))
    assert (await store.get_many([KEY]))[KEY].usd == Decimal("0.07")


async def test_a_correction_outside_the_replay_horizon_pauses_and_keeps_the_estimate(db):
    run_id = await _run(db)
    store = InMemorySpendStore()
    await store.add_many(
        [SpendEntry(key=KEY, usd=Decimal("0.10"), requests=2, ttl=None, token="estimate")]
    )

    with pytest.raises(SpendStoreError):
        await _settle(db, run_id, _accounting(old=True), store=store, actual_usd=Decimal("0.07"))

    assert (await store.get_many([KEY]))[KEY].usd == Decimal("0.10")
    saved = await db.fetchrow("SELECT accounting FROM categorize_runs WHERE id = $1", run_id)
    assert saved["accounting"]["phase"] == "paused_horizon"


def _unused(messages, info) -> ModelResponse:
    return ModelResponse(parts=[])


def test_an_unpriced_batch_identity_is_refused_before_submission(monkeypatch):
    monkeypatch.delenv("BATCH_PROVIDER", raising=False)
    model = FunctionModel(_unused, model_name="unpriced-batch-model")
    with pytest.raises(UnpricedModelError):
        estimate_batch_usd([{"keys": ["a"], "prompt": "one payee"}], model=model)


def test_a_priced_batch_estimate_is_a_conservative_positive_amount(monkeypatch):
    monkeypatch.setenv("BATCH_PROVIDER", "openai")
    model = FunctionModel(_unused, model_name="gpt-4o-mini")
    first = estimate_batch_usd([{"keys": ["a"], "prompt": "x" * 100}], model=model)
    longer = estimate_batch_usd([{"keys": ["a"], "prompt": "x" * 5000}], model=model)
    assert first > 0
    assert longer > first


async def test_collection_retries_only_settlement_from_saved_answers(db):
    from arno.agent.spend import StrictSpendStore
    from arno.importers.categorize import _finish_collection, collect_batch

    run_id = await _run(db)
    row = await db.fetchrow_required("SELECT * FROM categorize_runs WHERE id=$1", run_id)
    store = InMemorySpendStore()
    await store.add_many([SpendEntry(key=KEY, usd=Decimal("0.10"), requests=1, token="estimate")])
    accounting = {**_accounting(), "actual_usd": "0.07", "phase": "settlement_pending"}
    answers = [{"key": "synthetic", "category": "Food"}]
    await db.execute(
        "UPDATE categorize_runs SET batch_answers=$1,accounting=$2 WHERE id=$3",
        answers,
        accounting,
        run_id,
    )

    class Unavailable:
        async def add_many(self, entries):
            raise RuntimeError("transient store outage")

    result = await _finish_collection(
        db,
        row,
        accounting,
        answers=answers,
        actual=Decimal("0.07"),
        store=StrictSpendStore(Unavailable()),
        polls=0,
    )
    assert result["status"] == "settlement_pending"
    saved = await db.fetchrow_required("SELECT * FROM categorize_runs WHERE id=$1", run_id)
    assert saved["batch_answers"] == answers
    assert saved["accounting"]["phase"] == "paused_store"
    assert await db.fetchval("SELECT count(*) FROM jobs WHERE kind='collect_batch'") == 1
    # No model is configured: resuming saved settlement must never touch a provider.
    result = await collect_batch(db, run_id, spend_store=store)
    assert result["status"] == "collected"
    assert (await store.get_many([KEY]))[KEY].usd == Decimal("0.07")
    assert (await collect_batch(db, run_id, spend_store=store))["status"] == "already_collected"
    assert (await store.get_many([KEY]))[KEY].usd == Decimal("0.07")
