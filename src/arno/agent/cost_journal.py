"""The small call journal behind cost tracking, and its cost-only tracing replay.

One row is written before each real model dispatch and finalized with the shared `CostObservation`
once the response (or its failure) is known. The row holds cost and identity metadata only: no
prompt, answer, document, credential, or raw provider body. It is not read to admit requests and
carries no budget windows; `pydantic_ai_harness.spend` remains the only budget authority.

Recovery re-exports finalized observations through the standard OTLP exporter using the original
trace and span IDs. Phoenix keeps one cost per span ID, and replay never touches native counters.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from opentelemetry.sdk.trace import ReadableSpan

from arno import observability
from arno.agent.pricing import CostObservation, cost_attributes
from arno.db.conn import Connection, connect

logger = logging.getLogger("arno.cost_journal")

# Let the normal BatchSpanProcessor flush and export before replacing a delayed full span with a
# metadata-only replay.
# shortcut: a fixed grace, tune if the standard exporter's retry window grows past it.
REPLAY_GRACE_SECONDS = 300
_journal_database: ContextVar[str | None] = ContextVar("cost_journal_database", default=None)
_cost_identity: ContextVar[tuple[str | None, str | None]] = ContextVar(
    "cost_identity", default=(None, None)
)


def use_journal_database(dsn: str) -> Token[str | None]:
    """Keep eval cost observations outside the disposable financial trial database."""
    return _journal_database.set(dsn)


def reset_journal_database(token: Token[str | None]) -> None:
    _journal_database.reset(token)


def current_cost_identity() -> tuple[str | None, str | None]:
    return _cost_identity.get()


@contextmanager
def cost_identity(*, session_id: str | None = None, workload: str | None = None) -> Generator[None]:
    """Inherit opaque conversation and job attribution through child model calls."""
    parent_session, parent_workload = _cost_identity.get()
    token = _cost_identity.set((session_id or parent_session, workload or parent_workload))
    try:
        yield
    finally:
        _cost_identity.reset(token)


@dataclass(frozen=True, slots=True)
class CostAttempt:
    """The identity a dispatch keeps for its journal row, attempt span, and later replay."""

    attempt_id: str
    trace_id: str
    span_id: str
    role: str
    provider: str
    model: str
    billing_mode: str
    workload: str | None
    started_at: datetime
    session_id: str | None = None
    project_name: str | None = None


class JournalSink(Protocol):
    """What `MeteredModel` needs from a journal. Tests inject a fake; production uses `Journal`."""

    async def record_intent(self, attempt: CostAttempt) -> None: ...

    async def record_final(
        self, attempt: CostAttempt, observation: CostObservation, finished_at: datetime
    ) -> None: ...


async def record_intent(conn: Connection, attempt: CostAttempt) -> None:
    """Persist the dispatch before it reaches the network."""
    await conn.execute(
        """
        INSERT INTO llm_calls (
            attempt_id, trace_id, span_id, role, provider, model, billing_mode, workload,
            started_at, session_id, project_name
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        attempt.attempt_id,
        attempt.trace_id,
        attempt.span_id,
        attempt.role,
        attempt.provider,
        attempt.model,
        attempt.billing_mode,
        attempt.workload,
        attempt.started_at,
        attempt.session_id,
        attempt.project_name or observability._project_name(),
    )


async def record_final(
    conn: Connection,
    attempt: CostAttempt,
    observation: CostObservation,
    finished_at: datetime,
) -> None:
    """Attach the immutable observation to its intent.

    A second finalization is a no-op, so an uncertain retry cannot rewrite a recorded cost. A
    missing intent is a real gap and raises rather than disappearing.
    """
    row = await conn.fetchrow(
        """
        UPDATE llm_calls
        SET status = 'finalized',
            finished_at = $2,
            usage = $3,
            usd = $4,
            source = $5,
            complete = $6,
            price_revision = $7,
            provider_charge = $8,
            allowance = $9,
            billing_source = $10,
            rates = $11,
            model = $12
        WHERE attempt_id = $1 AND status = 'intent'
        RETURNING attempt_id
        """,
        attempt.attempt_id,
        finished_at,
        observation.usage,
        observation.usd,
        observation.source,
        observation.complete,
        observation.price_revision,
        observation.provider_charge,
        observation.allowance,
        observation.billing_source,
        observation.rates,
        attempt.model,
    )
    if row is None:
        status = await conn.fetchval(
            "SELECT status FROM llm_calls WHERE attempt_id = $1", attempt.attempt_id
        )
        if status is None:
            raise LookupError(f"no journal intent for attempt {attempt.attempt_id}")


class Journal:
    """Use the current run's database without retaining a connection across eval trials."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn

    async def record_intent(self, attempt: CostAttempt) -> None:
        from arno.agent.spend import SpendStoreError

        try:
            async with connect(self._dsn or _journal_database.get()) as conn:
                await record_intent(conn, attempt)
        except Exception as exc:
            raise SpendStoreError("The cost journal could not record dispatch intent.") from exc

    async def record_final(
        self, attempt: CostAttempt, observation: CostObservation, finished_at: datetime
    ) -> None:
        from arno.agent.spend import SpendStoreError

        try:
            async with connect(self._dsn or _journal_database.get()) as conn:
                await record_final(conn, attempt, observation, finished_at)
        except Exception as exc:
            raise SpendStoreError("The cost journal could not record a completed call.") from exc

    async def close(self) -> None:
        """No retained connection; each operation closes its own."""


def _observation(row: dict[str, Any]) -> CostObservation:
    return CostObservation(
        usd=row["usd"],
        source=row["source"],
        complete=row["complete"],
        price_revision=row["price_revision"],
        provider_charge=row["provider_charge"],
        allowance=row["allowance"],
        billing_source=row["billing_source"],
        usage=row["usage"],
        rates=row["rates"],
    )


def _nanos(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000_000)


def build_replay_span(row: dict[str, Any]) -> ReadableSpan:
    """Build the metadata-only span for one finalized row, keeping its retained IDs."""
    attributes = cost_attributes(_observation(row))
    attributes.update(
        {
            "arno.cost.role": "attempt",
            "gen_ai.request.model": row["model"],
            "gen_ai.provider.name": row["provider"],
            "openinference.span.kind": "LLM",
            "llm.model_name": row["model"],
            "llm.provider": row["provider"],
            "arno.cost.attempt_id": row["attempt_id"],
            "arno.cost.workload": row["workload"] or row["role"],
            "arno.cost.modality": "audio" if row["role"] == "stt" else "text",
            "arno.cost.billing_mode": row["billing_mode"],
            "arno.trace.recovered": True,
            "arno.trace.content_unavailable": True,
        }
    )
    if row.get("session_id"):
        attributes["session.id"] = row["session_id"]
        attributes["gen_ai.conversation.id"] = row["session_id"]
    return observability.build_span(
        name=f"{row['role']} {row['model']}",
        trace_id=row["trace_id"],
        span_id=row["span_id"],
        attributes=attributes,
        start_time_ns=_nanos(row["started_at"]),
        end_time_ns=_nanos(row["finished_at"]),
        project_name=row.get("project_name"),
    )


async def replay_costs(limit: int = 100, *, attempt_id: str | None = None) -> dict[str, int]:
    """Re-export finalized observations whose native span has not been accepted yet.

    An explicit attempt ID also retries a previously accepted export. Counts are `exported`,
    `failed`, and `pending` (unfinished or not yet accepted after this pass). Transport acceptance
    marks the row; a failure leaves it for the next pass.
    `OTEL_SDK_DISABLED` and a missing endpoint make `exported` zero without touching the rows.
    """
    async with connect() as conn:
        rows = await conn.fetch(
            f"""
            SELECT * FROM llm_calls
            WHERE status = 'finalized'
              AND (exported_at IS NULL OR attempt_id = $2)
              AND ($2::text IS NULL OR attempt_id = $2)
              AND finished_at < now() - interval '{REPLAY_GRACE_SECONDS} seconds'
            ORDER BY finished_at
            LIMIT $1
            """,
            limit,
            attempt_id,
        )
        if not rows:
            pending = await conn.fetchval(
                "SELECT count(*) FROM llm_calls WHERE exported_at IS NULL"
            )
            return {"exported": 0, "failed": 0, "pending": int(pending or 0)}
        spans = [build_replay_span(row) for row in rows]
        exported = await asyncio.to_thread(observability.export_replay, spans)
        if exported:
            await conn.execute(
                "UPDATE llm_calls SET exported_at = now() WHERE attempt_id = ANY($1::text[])",
                [row["attempt_id"] for row in rows],
            )
        pending = await conn.fetchval("SELECT count(*) FROM llm_calls WHERE exported_at IS NULL")
        if not exported:
            logger.warning("cost replay exported none of %d recovery row(s)", len(rows))
        return {
            "exported": exported,
            "failed": 0 if exported else len(rows),
            "pending": int(pending or 0),
        }


async def finalize_interrupted_calls() -> int:
    """After inference stops, expose interrupted ordinary calls; batches still need collection."""
    async with connect() as conn:
        rows = await conn.fetch(
            """
            UPDATE llm_calls SET status = 'finalized', finished_at = now(),
                source = 'interrupted', complete = false, price_revision = 'interrupted/v1'
            WHERE status = 'intent' AND role <> 'batch_item'
            RETURNING attempt_id
            """
        )
    return len(rows)


__all__ = [
    "REPLAY_GRACE_SECONDS",
    "CostAttempt",
    "Journal",
    "JournalSink",
    "build_replay_span",
    "record_final",
    "record_intent",
    "replay_costs",
]
