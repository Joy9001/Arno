"""The cost journal: intent, immutable finalization, grants, and cost-only replay."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from arno import observability
from arno.agent.cost_journal import (
    CostAttempt,
    Journal,
    build_replay_span,
    record_final,
    record_intent,
    replay_costs,
)
from arno.agent.pricing import CostObservation
from arno.db.conn import connect, conninfo_to_dict, make_conninfo

EXPECTED_COLUMNS = {
    "attempt_id",
    "trace_id",
    "span_id",
    "role",
    "provider",
    "model",
    "billing_mode",
    "workload",
    "status",
    "started_at",
    "finished_at",
    "usage",
    "usd",
    "source",
    "complete",
    "price_revision",
    "provider_charge",
    "allowance",
    "billing_source",
    "rates",
    "exported_at",
    "session_id",
    "project_name",
}


def _attempt(*, finished_ago: timedelta = timedelta(minutes=6)) -> CostAttempt:
    started = datetime.now(UTC) - finished_ago - timedelta(minutes=1)
    return CostAttempt(
        attempt_id="a" * 32,
        trace_id="b" * 32,
        span_id="c" * 16,
        role="chat",
        provider="openai",
        model="gpt-test",
        billing_mode="metered",
        workload=None,
        started_at=started,
    )


def _observation(usd: Decimal | None = Decimal("0.00123")) -> CostObservation:
    return CostObservation(
        usd=usd,
        source="provider" if usd is not None else "unavailable",
        complete=usd is not None,
        price_revision="genai-prices-0.2.1",
        provider_charge=Decimal("0.002"),
        allowance=None,
        billing_source="wallet",
        usage={"input_tokens": 10, "output_tokens": 4},
        rates={"input": "1", "output": "2"},
    )


async def _clear(db) -> None:
    await db.execute("DELETE FROM llm_calls")


async def test_intent_is_written_before_finalization_and_stays_immutable(db):
    await _clear(db)
    attempt = _attempt()
    await record_intent(db, attempt)

    row = await db.fetchrow_required("SELECT * FROM llm_calls WHERE attempt_id = $1", "a" * 32)
    assert row["status"] == "intent"
    assert row["finished_at"] is None
    assert row["usd"] is None

    finished_at = datetime.now(UTC)
    await record_final(db, attempt, _observation(), finished_at)
    first = await db.fetchrow_required("SELECT * FROM llm_calls WHERE attempt_id = $1", "a" * 32)
    assert first["status"] == "finalized"
    assert first["usd"] == Decimal("0.00123")
    assert first["source"] == "provider"
    assert first["complete"] is True
    assert first["rates"] == {"input": "1", "output": "2"}
    assert first["exported_at"] is None

    # A second finalization (a retry with the same attempt identity) cannot rewrite the cost.
    await record_final(db, attempt, _observation(Decimal("9.99")), finished_at)
    second = await db.fetchrow_required("SELECT * FROM llm_calls WHERE attempt_id = $1", "a" * 32)
    assert second["usd"] == Decimal("0.00123")
    assert second["price_revision"] == "genai-prices-0.2.1"


async def test_finalizing_without_an_intent_is_a_visible_gap(db):
    await _clear(db)
    with pytest.raises(LookupError):
        await record_final(db, _attempt(), _observation(), datetime.now(UTC))


async def test_journal_has_no_content_and_agent_can_only_not_read_it(db):
    await _clear(db)
    await record_intent(db, _attempt())
    await record_final(db, _attempt(), _observation(), datetime.now(UTC))

    columns = {
        row["column_name"]
        for row in await db.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'llm_calls'
            """
        )
    }
    assert columns == EXPECTED_COLUMNS
    assert not {"prompt", "content", "response", "body", "api_key"} & columns

    base = conninfo_to_dict(os.environ["DATABASE_URL"])
    agent_dsn = make_conninfo(
        **{**base, "user": "agent_ro", "password": os.environ["AGENT_RO_PASSWORD"]}
    )
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        async with connect(agent_dsn) as conn:
            await conn.fetch("SELECT * FROM llm_calls")

    backup_dsn = make_conninfo(
        **{**base, "user": "backup", "password": os.environ["BACKUP_PASSWORD"]}
    )
    async with connect(backup_dsn) as conn:
        assert await conn.fetchval("SELECT count(*) FROM llm_calls") == 1

    # The restore drill's own manifest sees the journal, so a restored database can account for it.
    manifest = await db.fetchrow_required("SELECT fin_agent_backup_manifest() AS manifest")
    assert manifest["manifest"]["tables"]["llm_calls"]["rows"] == 1


async def test_replay_keeps_ids_exports_metadata_only_and_touches_no_budget(db, monkeypatch):
    await _clear(db)
    attempt = _attempt()
    await record_intent(db, attempt)
    await record_final(db, attempt, _observation(), datetime.now(UTC) - timedelta(minutes=6))

    spend_before = 0
    if await db.fetchval("SELECT to_regclass('public.spend_counters')"):
        spend_before = await db.fetchval("SELECT count(*) FROM spend_counters")

    captured = []
    real_export = observability.export_replay

    def fake_export(spans, exporter=None):
        del exporter
        captured.extend(spans)
        return len(spans)

    monkeypatch.setattr(observability, "export_replay", fake_export)

    counts = await replay_costs(limit=10)
    assert counts == {"exported": 1, "failed": 0, "pending": 0}
    assert len(captured) == 1
    span = captured[0]
    assert span.context.trace_id == int("b" * 32, 16)
    assert span.context.span_id == int("c" * 16, 16)
    assert span.attributes["arno.cost.role"] == "attempt"
    assert span.attributes["arno.cost.completeness"] == "known"
    assert "llm.cost.total" in span.attributes
    assert "gen_ai.input.messages" not in span.attributes
    assert "input.value" not in span.attributes

    row = await db.fetchrow_required("SELECT * FROM llm_calls WHERE attempt_id = $1", "a" * 32)
    assert row["exported_at"] is not None

    # The same row is not exported again on a later pass.
    assert await replay_costs(limit=10) == {"exported": 0, "failed": 0, "pending": 0}

    if await db.fetchval("SELECT to_regclass('public.spend_counters')"):
        assert await db.fetchval("SELECT count(*) FROM spend_counters") == spend_before

    # The standard in-memory exporter receives the exact retained identity.
    in_memory = InMemorySpanExporter()
    assert real_export([build_replay_span(row)], in_memory) == 1
    exported = in_memory.get_finished_spans()[0]
    assert exported.context is not None
    assert exported.context.trace_id == int("b" * 32, 16)
    assert exported.context.span_id == int("c" * 16, 16)
    assert exported.parent is None


async def test_unfinished_intents_are_never_replayed(db, monkeypatch):
    await _clear(db)
    await record_intent(db, _attempt())

    def fail(*_args, **_kwargs):
        raise AssertionError("an unfinished intent must not be exported")

    monkeypatch.setattr(observability, "export_replay", fail)
    assert await replay_costs(limit=10) == {"exported": 0, "failed": 0, "pending": 1}


async def test_database_journal_is_the_default_sink(db):
    await _clear(db)
    journal = Journal(os.environ["DATABASE_URL"])
    try:
        attempt = _attempt()
        await journal.record_intent(attempt)
        await journal.record_final(attempt, _observation(), datetime.now(UTC))
    finally:
        await journal.close()
        await journal.close()

    row = await db.fetchrow_required("SELECT * FROM llm_calls WHERE attempt_id = $1", "a" * 32)
    assert row["status"] == "finalized"
    assert row["usd"] == Decimal("0.00123")


async def test_a_failed_replay_keeps_the_row_for_the_next_pass(db, monkeypatch):
    await _clear(db)
    attempt = _attempt()
    await record_intent(db, attempt)
    await record_final(db, attempt, _observation(), datetime.now(UTC) - timedelta(minutes=6))

    monkeypatch.setattr(observability, "export_replay", lambda spans: 0)
    assert await replay_costs(limit=10) == {"exported": 0, "failed": 1, "pending": 1}
    row = await db.fetchrow_required("SELECT * FROM llm_calls WHERE attempt_id = $1", "a" * 32)
    assert row["exported_at"] is None


def test_replay_export_respects_the_sdk_kill_switch(monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    span = build_replay_span(
        {
            "role": "chat",
            "provider": "openai",
            "model": "gpt-test",
            "trace_id": "b" * 32,
            "span_id": "c" * 16,
            "attempt_id": "a" * 32,
            "billing_mode": "metered",
            "workload": None,
            "started_at": datetime.now(UTC) - timedelta(minutes=7),
            "finished_at": datetime.now(UTC) - timedelta(minutes=6),
            "usd": Decimal("0.00123"),
            "source": "provider",
            "complete": True,
            "price_revision": "test",
            "provider_charge": None,
            "allowance": None,
            "billing_source": None,
            "usage": None,
            "rates": None,
        }
    )
    assert observability.export_replay([span]) == 0


async def test_only_explicit_owner_recovery_finalizes_interrupted_intents(db):
    from arno.agent.cost_journal import finalize_interrupted_calls

    await record_intent(db, _attempt())
    await record_intent(db, replace(_attempt(), attempt_id="batch-intent", role="batch_item"))
    assert await replay_costs() == {"exported": 0, "failed": 0, "pending": 2}
    assert await finalize_interrupted_calls() == 1
    row = await db.fetchrow_required("SELECT * FROM llm_calls WHERE attempt_id=$1", "a" * 32)
    assert row["usd"] is None
    assert row["complete"] is False
    assert row["source"] == "interrupted"
    assert row["price_revision"] == "interrupted/v1"
    span = build_replay_span(row)
    assert span.attributes is not None
    assert span.attributes["arno.cost.completeness"] == "unknown"
    assert "llm.cost.total" not in span.attributes
    assert (
        await db.fetchval("SELECT status FROM llm_calls WHERE attempt_id='batch-intent'")
        == "intent"
    )
    assert await finalize_interrupted_calls() == 0


async def test_an_explicit_attempt_replays_after_transport_acceptance_without_rebilling(
    db, monkeypatch
):
    await record_intent(db, _attempt())
    await record_final(db, _attempt(), _observation(), datetime.now(UTC) - timedelta(minutes=6))
    exports = []

    def accept(spans):
        exports.extend(spans)
        return len(spans)

    monkeypatch.setattr(observability, "export_replay", accept)
    assert (await replay_costs())["exported"] == 1
    assert (await replay_costs())["exported"] == 0
    assert (await replay_costs(attempt_id="a" * 32))["exported"] == 1
    assert len(exports) == 2
    assert exports[0].context == exports[1].context
    assert await db.fetchval("SELECT usd FROM llm_calls") == _observation().usd
