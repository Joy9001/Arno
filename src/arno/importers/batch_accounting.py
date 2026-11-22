"""The batch accounting lifecycle: one owner for `categorize_runs.accounting`.

The record is the whole state. A batch prices its estimate before submitting, posts it through a
stable token, and later posts only the correction through a second stable token. Older rows have
fewer fields: `BatchAccounting` gives every one a default, so a batch saved before attempts or
saved keys existed still settles to the shared day's key rather than charging twice.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, get_args

from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai.messages import ModelResponse
from pydantic_ai.usage import RequestUsage
from pydantic_ai_harness.spend import SpendEntry, UnpricedModelError

from arno.agent.cost_journal import CostAttempt
from arno.agent.pricing import LOCAL_BILLING_MODES, observe_cost, validate_pricing
from arno.agent.spend import SPEND_DEDUP_RETAIN, SpendStoreError, shared_day_status
from arno.db.conn import Connection
from arno.jobs.runner import JobGaveUp, enqueue_job

# A bounded reply gives the estimate a real output ceiling; the prompt framing is counted too.
BATCH_MAX_OUTPUT_TOKENS = 2048
BATCH_PROMPT_OVERHEAD_TOKENS = 4096
COLLECT_POLL_MINUTES = 2
COLLECT_POLL_MAX_MINUTES = 30

Phase = Literal[
    "estimate_pending",
    "estimate_applied",
    "submitted",
    "submit_ambiguous",
    "paused_ambiguous",
    "paused_horizon",
    "paused_store",
    "settlement_pending",
    "collected_provisional",
    "settled",
]
PHASES = frozenset(get_args(Phase))


class BatchAccounting(BaseModel):
    """A parsed, round-trippable accounting JSON, including legacy rows.

    `exclude_unset` keeps a never-written `{}` empty, so it cannot look like a posting intent.
    """

    model_config = ConfigDict(extra="allow")

    phase: Phase | None = None
    keys: list[str] = Field(default_factory=list)
    items: int = 0
    provider: str = ""
    model: str = ""
    billing_mode: str = "metered"
    estimate_usd: str = "0"
    actual_usd: str = "0"
    estimate_token: str = ""
    settle_token: str = ""
    estimate_at: datetime | None = None
    estimate_applied: bool = False
    estimate_applied_at: datetime | None = None
    settle_applied: bool = False
    settle_applied_at: datetime | None = None
    provisional: bool = False
    attempt: dict[str, Any] | None = None

    @field_validator("estimate_at", mode="before")
    @classmethod
    def _soft_datetime(cls, value: Any) -> Any:
        """An unreadable timestamp is no timestamp: it lands outside the settlement horizon."""
        if not value or isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    @field_validator("estimate_usd", "actual_usd", mode="before")
    @classmethod
    def _as_text(cls, value: Any) -> Any:
        """Older JSON may hold a number where a decimal string is stored today."""
        return str(value) if value is not None and not isinstance(value, str) else value

    @classmethod
    def from_record(cls, raw: dict[str, Any] | None) -> BatchAccounting:
        """Parse a stored record, normalizing the provider without inventing missing fields."""
        accounting = cls.model_validate(raw or {})
        provider = normalize_provider(accounting.provider)
        if provider != accounting.provider:
            accounting.provider = provider
        return accounting

    def to_record(self) -> dict[str, Any]:
        """The JSON-ready form for a jsonb column; fields never set stay absent."""
        return self.model_dump(mode="json", exclude_unset=True)


def normalize_provider(provider: str) -> str:
    """One name for the provider that priced the batch; Gemini is billed as Google."""
    value = str(provider or "").strip().lower()
    return "google" if value == "gemini" else value


def batch_identity(model: Any) -> tuple[str, str]:
    """The batch model's pricing identity and billing mode, without inventing either."""
    try:
        provider = str(model.provider.name).strip().lower()
    except Exception:  # noqa: BLE001 - a test double has no provider
        provider = ""
    if not provider:
        provider = os.environ.get("BATCH_PROVIDER", "").strip().lower()
    billing = os.environ.get("BATCH_BILLING_MODE", "").strip().lower() or "metered"
    return normalize_provider(provider), billing


def batch_model_name(model: Any) -> str:
    return str(getattr(model, "model_name", "") or "")


def estimate_token(batch_id: str) -> str:
    return f"categorize-batch-estimate:{batch_id}"


def settle_token(batch_id: str) -> str:
    return f"categorize-batch-settle:{batch_id}"


def estimate_batch_usd(payloads: list[dict[str, Any]], *, model: Any) -> Decimal:
    """A conservative priced bound for the whole batch, before anything is submitted.

    Every item is priced with its prompt's byte length (an upper bound on tokens) plus the fixed
    prompt/tool-schema overhead, and the configured output cap. A model whose identity cannot be
    priced rejects the batch rather than submitting work the budget cannot see.
    """
    provider, billing = batch_identity(model)
    if billing in LOCAL_BILLING_MODES:
        return Decimal(0)
    model_name = batch_model_name(model)
    try:
        validate_pricing(
            provider=provider,
            model=model_name,
            provider_url=None,
            billing_mode=billing,
        )
    except ValueError as exc:
        raise UnpricedModelError(
            f"No verified price for the batch model {model_name!r}; refusing to submit."
        ) from exc
    total = Decimal(0)
    for payload in payloads:
        prompt = str(payload["prompt"])
        usage = RequestUsage(
            input_tokens=max(1, len(prompt.encode("utf-8")) + BATCH_PROMPT_OVERHEAD_TOKENS),
            output_tokens=BATCH_MAX_OUTPUT_TOKENS,
        )
        probe = ModelResponse(parts=[], usage=usage, model_name=model_name, provider_name=provider)
        observation = observe_cost(probe, provider=provider, billing_mode=billing)
        if observation.usd is None or not observation.complete:
            raise UnpricedModelError(
                f"No verified price for the batch model {model_name!r}; refusing to submit."
            )
        total += observation.usd
    return total


def trace_id() -> str:
    """Keep the submission's real trace when tracing is on; a replay still needs an ID."""
    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        return trace.format_trace_id(context.trace_id)
    return secrets.token_hex(16)


def attempt_to_record(attempt: CostAttempt) -> dict[str, str]:
    return {
        "attempt_id": attempt.attempt_id,
        "trace_id": attempt.trace_id,
        "span_id": attempt.span_id,
        "role": attempt.role,
        "provider": attempt.provider,
        "model": attempt.model,
        "billing_mode": attempt.billing_mode,
        "workload": attempt.workload or "",
        "session_id": attempt.session_id or "",
        "started_at": attempt.started_at.isoformat(),
    }


def attempt_from_record(entry: dict[str, Any]) -> CostAttempt | None:
    """The item's original journal identity, or `None` for a batch submitted before it existed."""
    raw = entry.get("attempt")
    if not isinstance(raw, dict):
        return None
    try:
        return CostAttempt(
            attempt_id=str(raw["attempt_id"]),
            trace_id=str(raw["trace_id"]),
            span_id=str(raw["span_id"]),
            role=str(raw["role"]),
            provider=str(raw["provider"]),
            model=str(raw["model"]),
            billing_mode=str(raw["billing_mode"]),
            workload=str(raw.get("workload") or "") or None,
            session_id=str(raw.get("session_id") or "") or None,
            started_at=datetime.fromisoformat(str(raw["started_at"])),
        )
    except KeyError, ValueError:
        return None


async def ensure_no_pending_batch(conn: Connection, import_id: str) -> None:
    """An uncollected or uncertain estimate must not cause another paid submission."""
    pending = await conn.fetchval(
        """
        SELECT id FROM categorize_runs WHERE import_id=$1 AND accounting <> '{}'::jsonb
          AND NOT coalesce((accounting->>'settle_applied')::boolean, false)
        ORDER BY started_at LIMIT 1
        """,
        import_id,
    )
    if pending:
        raise JobGaveUp(
            f"Batch {pending} is pending or has uncertain accounting. Inspect that batch before "
            "starting another categorization; no new submission was made."
        )


async def save_accounting(conn: Connection, run_id: str, accounting: BatchAccounting) -> None:
    await conn.execute(
        "UPDATE categorize_runs SET accounting = $1, updated_at = now() WHERE id = $2",
        accounting.to_record(),
        run_id,
    )


def within_settlement_horizon(accounting: BatchAccounting) -> bool:
    """Whether a stable token can still be replayed without risking a second charge."""
    posted = accounting.estimate_at
    if posted is None:
        return False
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=UTC)
    return datetime.now(UTC) - posted <= SPEND_DEDUP_RETAIN


async def settle(
    conn: Connection,
    run_id: str,
    accounting: BatchAccounting,
    *,
    store: Any,
    actual_usd: Decimal,
) -> None:
    """Post `actual - estimate` once to the saved keys, using the stable settlement token."""
    if accounting.settle_applied:
        return
    if not within_settlement_horizon(accounting):
        accounting.phase = "paused_horizon"
        await save_accounting(conn, run_id, accounting)
        raise SpendStoreError(
            "The batch estimate is older than the spend deduplication horizon; "
            "settlement paused rather than risking a second charge."
        )
    keys = [str(key) for key in accounting.keys if key]
    if not keys:
        # A batch submitted before accounting existed was never estimated, so its full actual is
        # the honest settlement. It is still posted once through a stable token.
        status = await shared_day_status(store)
        keys = [status.key]
    estimate = Decimal(accounting.estimate_usd or "0")
    delta = actual_usd - estimate
    token = accounting.settle_token or settle_token(run_id)
    await store.add_many(
        [SpendEntry(key=key, usd=delta, requests=0, ttl=None, token=token) for key in keys]
    )
    accounting.settle_applied = True
    accounting.settle_applied_at = datetime.now(UTC)
    accounting.actual_usd = str(actual_usd)


def poll_delay(polls: int) -> timedelta:
    minutes = min(COLLECT_POLL_MAX_MINUTES, COLLECT_POLL_MINUTES * (2 ** max(0, polls)))
    return timedelta(minutes=minutes)


async def finish_collection(
    conn: Connection,
    row: dict[str, Any],
    accounting: BatchAccounting,
    *,
    answers: list[dict[str, Any]],
    actual: Decimal,
    store: Any,
    polls: int,
) -> dict[str, Any]:
    """Settle saved results without requesting or resubmitting provider work."""
    run_id = str(row["id"])
    if accounting.provisional:
        accounting.phase = "collected_provisional"
    else:
        try:
            await settle(conn, run_id, accounting, store=store, actual_usd=actual)
        except SpendStoreError as exc:
            if accounting.phase == "paused_horizon":
                raise JobGaveUp(
                    f"Batch settlement exceeded its safe recovery horizon: {exc}"
                ) from exc
            accounting.phase = "paused_store"
            await save_accounting(conn, run_id, accounting)
            polls += 1
            await enqueue_job(
                conn,
                key=f"collect_batch:{run_id}:settle:{polls}",
                kind="collect_batch",
                payload={"run_id": run_id, "import_id": str(row["import_id"]), "polls": polls},
                run_at=datetime.now(UTC) + poll_delay(polls - 1),
            )
            return {"status": "settlement_pending", "polls": polls}
        accounting.phase = "settled"

    async with conn.transaction():
        await conn.execute(
            """
            UPDATE categorize_runs SET accounting=$1, finished_at=now(), updated_at=now()
            WHERE id=$2
            """,
            accounting.to_record(),
            run_id,
        )
        await enqueue_job(
            conn,
            key=f"categorize_import:{row['import_id']}:batch:{run_id}",
            kind="categorize_import",
            payload={"import_id": str(row["import_id"]), "batch_id": run_id},
        )
    return {"status": "collected", "answers": len(answers), "run_id": run_id}


__all__ = [
    "BATCH_MAX_OUTPUT_TOKENS",
    "BATCH_PROMPT_OVERHEAD_TOKENS",
    "PHASES",
    "BatchAccounting",
    "Phase",
    "attempt_from_record",
    "attempt_to_record",
    "batch_identity",
    "batch_model_name",
    "ensure_no_pending_batch",
    "estimate_batch_usd",
    "estimate_token",
    "finish_collection",
    "normalize_provider",
    "poll_delay",
    "save_accounting",
    "settle",
    "settle_token",
    "trace_id",
    "within_settlement_horizon",
]
