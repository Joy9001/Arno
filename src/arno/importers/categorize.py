"""Categorizing after commit: rules first, then the categorizer agent, then the batch collector.

The job runner starts `categorize_import` after a commit. It labels every row a rule covers and
runs the agent on the rest. `submit_batch` suspends one labeling run per chunk and queues
`collect_batch`; that job re-checks the batch, stores its answers, and starts one fresh
categorizer run to save them.

A run says what happened: `done`, `partial` (some payees got no answer from the model and stay
uncategorized), `nothing_to_do`, or `batch_submitted`, and a finished run puts a counts-only item
in the inbox. A run the agent could not finish raises, so the job runner retries it and, at the
end, reports the failure; a spend or usage ceiling gives up at once, since a retry hits it again.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic_ai.capabilities import BatchModelRequests
from pydantic_ai.exceptions import SuspendedResponseExpired, UsageLimitExceeded
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.output import PendingModelResponse
from pydantic_ai_harness.spend import SpendEntry, UnpricedModelError

from arno.agent.capabilities import drain_events
from arno.agent.categorizer import (
    CATEGORIZE_USAGE_LIMITS,
    CategorizerDeps,
    build_categorizer_agent,
    build_labeling_agent,
    ensure_no_pending_batch,
)
from arno.agent.cost_journal import CostAttempt, record_final
from arno.agent.models import batch_model as configured_batch_model
from arno.agent.models import primary_model
from arno.agent.pricing import observe_cost
from arno.agent.spend import (
    SPEND_DEDUP_RETAIN,
    SpendStoreError,
    ceiling_name,
    shared_day_status,
    spend_store_scope,
)
from arno.db.conn import Connection, connect
from arno.importers import rules as rules_mod
from arno.jobs.runner import JobGaveUp, enqueue_job
from arno.ledger.audit import new_id
from arno.notifications import create_notification, send_push_for_kind

logger = logging.getLogger("arno.importers.categorize")

COLLECT_POLL_MINUTES = 2
COLLECT_POLL_MAX_MINUTES = 30


async def _counts(conn: Connection, import_id: str) -> tuple[int, int]:
    row = await conn.fetchrow_required(
        """
        SELECT
            count(*) FILTER (
                WHERE EXISTS (
                    SELECT 1 FROM movement_legs ml
                    WHERE ml.movement_id = m.id AND ml.category_id IS NOT NULL
                )
            )::int AS labeled,
            count(*) FILTER (
                WHERE NOT EXISTS (
                    SELECT 1 FROM movement_legs ml
                    WHERE ml.movement_id = m.id AND ml.category_id IS NOT NULL
                )
            )::int AS uncategorized
        FROM statement_lines sl
        JOIN movements m ON m.id = sl.movement_id
        JOIN v_movement_state ms ON ms.movement_id = m.id
        WHERE sl.import_id = $1 AND sl.match_state = 'merged' AND ms.is_effective
        """,
        import_id,
    )
    return int(row["labeled"]), int(row["uncategorized"])


async def _finish_run(conn: Connection, run_id: str, import_id: str, deps: CategorizerDeps) -> None:
    labeled, uncategorized = await _counts(conn, import_id)
    await conn.execute(
        """
        UPDATE categorize_runs
        SET agreement_rate = $1, agreement_checked = $2, labeled = $3, uncategorized = $4,
            finished_at = now(), updated_at = now()
        WHERE id = $5
        """,
        deps.agreement_rate,
        deps.agreement_checked,
        labeled,
        uncategorized,
        run_id,
    )


def _done_body(labeled: int, uncategorized: int, failed_payees: int) -> str:
    """Counts only: an inbox item never names a payee."""
    rows = "transaction" if labeled == 1 else "transactions"
    if uncategorized:
        need = "needs" if uncategorized == 1 else "need"
        body = f"{labeled} {rows} labeled; {uncategorized} {need} a category from you."
    else:
        body = f"All {labeled} {rows} have a category."
    if failed_payees:
        payees = "payee" if failed_payees == 1 else "payees"
        body += (
            f" The model gave no answer for {failed_payees} {payees}, so they stay uncategorized."
        )
    return body


async def _announce(
    conn: Connection, import_id: str, result: dict[str, Any], *, batch: bool
) -> None:
    if result["labeled"] + result["uncategorized"] == 0:
        return
    created = await create_notification(
        conn,
        kind="categorize_done",
        title="Statement categorized",
        body=_done_body(result["labeled"], result["uncategorized"], result["failed_payees"]),
        data={
            "import_id": import_id,
            "run_id": result["run_id"],
            "labeled": result["labeled"],
            "uncategorized": result["uncategorized"],
            "failed_payees": result["failed_payees"],
        },
        dedupe_key=f"categorize_done:{result['run_id']}",
    )
    # A batch can take hours, so its end earns a ping; a run started a minute ago doesn't.
    if created is not None and batch:
        await send_push_for_kind(conn, "categorize_done")


async def run_categorize(
    conn: Connection,
    import_id: str,
    *,
    batch_id: str | None = None,
    model: Any = None,
    batch: Any = None,
    spend_store: Any = None,
) -> dict[str, Any]:
    """Rules, then the agent, for one committed import. `batch_id` reads a finished batch."""
    if batch_id is None:
        await ensure_no_pending_batch(conn, import_id)
    await rules_mod.apply_rules(conn, import_id)
    run_id = await new_id(conn, "cat")
    await conn.execute(
        """
        INSERT INTO categorize_runs (id, import_id, mode, batch_id)
        VALUES ($1, $2, $3, $4)
        """,
        run_id,
        import_id,
        "batch" if batch_id else "now",
        batch_id,
    )
    pending = await rules_mod.pending_rows(conn, import_id)
    if not pending:
        deps = CategorizerDeps(import_id=import_id, run_id=run_id)
        await _finish_run(conn, run_id, import_id, deps)
        labeled, uncategorized = await _counts(conn, import_id)
        result = {
            "status": "nothing_to_do",
            "labeled": labeled,
            "uncategorized": uncategorized,
            "failed_payees": 0,
            "run_id": run_id,
        }
        await _announce(conn, import_id, result, batch=batch_id is not None)
        return result

    checks: dict[str, str] = {}
    if batch_id:
        row = await conn.fetchrow(
            "SELECT batch_checks FROM categorize_runs WHERE id = $1", batch_id
        )
        checks = dict((row["batch_checks"] if row else None) or {})

    async with spend_store_scope(spend_store) as store:
        deps = CategorizerDeps(
            import_id=import_id,
            run_id=run_id,
            model=model,
            batch_model=batch,
            spend_store=store,
            check_answers=checks,
            batch_id=batch_id,
        )
        agent = build_categorizer_agent(
            model=model,
            batch_model=batch,
            spend_store=store,
            with_batch_results=batch_id is not None,
        )
        try:
            await agent.run(
                f"Categorize the payees of import {import_id}. Save once, then report.",
                deps=deps,
                usage_limits=CATEGORIZE_USAGE_LIMITS,
                event_stream_handler=drain_events,
            )
        except Exception as exc:
            # The import stays committed either way; this only decides how the job ends.
            await _finish_run(conn, run_id, import_id, deps)
            ceiling = ceiling_name(exc)
            if ceiling:
                raise JobGaveUp(f"Stopped at the {ceiling} spend ceiling.") from exc
            if isinstance(exc, (SpendStoreError, UnpricedModelError)):
                # The call may already have been dispatched; a retry would spend again.
                raise JobGaveUp(f"Stopped at the cost ledger: {exc}") from exc
            if isinstance(exc, UsageLimitExceeded):
                raise JobGaveUp(f"Stopped at the categorizer's step limit: {exc}") from exc
            raise

    if deps.submitted_batch:
        return {"status": "batch_submitted", "batch_id": deps.submitted_batch, "run_id": run_id}

    await _finish_run(conn, run_id, import_id, deps)
    labeled, uncategorized = await _counts(conn, import_id)
    result = {
        "status": "partial" if deps.failed_keys else "done",
        "run_id": run_id,
        "labeled": labeled,
        "uncategorized": uncategorized,
        "failed_payees": len(deps.failed_keys),
        "agreement": deps.agreement_rate,
    }
    await _announce(conn, import_id, result, batch=batch_id is not None)
    return result


def _poll_delay(polls: int) -> timedelta:
    minutes = min(COLLECT_POLL_MAX_MINUTES, COLLECT_POLL_MINUTES * (2 ** max(0, polls)))
    return timedelta(minutes=minutes)


def _attempt_from(entry: dict[str, Any]) -> CostAttempt | None:
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


async def _save_accounting(conn: Connection, run_id: str, accounting: dict[str, Any]) -> None:
    await conn.execute(
        "UPDATE categorize_runs SET accounting = $1, updated_at = now() WHERE id = $2",
        accounting,
        run_id,
    )


def _within_settlement_horizon(accounting: dict[str, Any]) -> bool:
    """Whether a stable token can still be replayed without risking a second charge."""
    raw = accounting.get("estimate_at")
    if not raw:
        return False
    try:
        posted = datetime.fromisoformat(str(raw))
    except ValueError:
        return False
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=UTC)
    return datetime.now(UTC) - posted <= SPEND_DEDUP_RETAIN


async def _settle(
    conn: Connection,
    run_id: str,
    accounting: dict[str, Any],
    *,
    store: Any,
    actual_usd: Decimal,
) -> None:
    """Post `actual - estimate` once to the saved keys, using the stable settlement token."""
    if accounting.get("settle_applied"):
        return
    if not _within_settlement_horizon(accounting):
        accounting["phase"] = "paused_horizon"
        await _save_accounting(conn, run_id, accounting)
        raise SpendStoreError(
            "The batch estimate is older than the spend deduplication horizon; "
            "settlement paused rather than risking a second charge."
        )
    keys = [str(key) for key in accounting.get("keys") or [] if key]
    if not keys:
        # A batch submitted before accounting existed was never estimated, so its full actual is
        # the honest settlement. It is still posted once through a stable token.
        status = await shared_day_status(store)
        keys = [status.key]
    estimate = Decimal(str(accounting.get("estimate_usd") or "0"))
    delta = actual_usd - estimate
    token = str(accounting.get("settle_token") or f"categorize-batch-settle:{run_id}")
    await store.add_many(
        [SpendEntry(key=key, usd=delta, requests=0, ttl=None, token=token) for key in keys]
    )
    accounting["settle_applied"] = True
    accounting["settle_applied_at"] = datetime.now(UTC).isoformat()
    accounting["actual_usd"] = str(actual_usd)


async def _finish_collection(
    conn: Connection,
    row: dict[str, Any],
    accounting: dict[str, Any],
    *,
    answers: list[dict[str, Any]],
    actual: Decimal,
    store: Any,
    polls: int,
) -> dict[str, Any]:
    """Settle saved results without requesting or resubmitting provider work."""
    run_id = str(row["id"])
    if accounting.get("provisional"):
        accounting["phase"] = "collected_provisional"
    else:
        try:
            await _settle(conn, run_id, accounting, store=store, actual_usd=actual)
        except SpendStoreError as exc:
            if accounting.get("phase") == "paused_horizon":
                raise JobGaveUp(
                    f"Batch settlement exceeded its safe recovery horizon: {exc}"
                ) from exc
            accounting["phase"] = "paused_store"
            await _save_accounting(conn, run_id, accounting)
            polls += 1
            await enqueue_job(
                conn,
                key=f"collect_batch:{run_id}:settle:{polls}",
                kind="collect_batch",
                payload={"run_id": run_id, "import_id": str(row["import_id"]), "polls": polls},
                run_at=datetime.now(UTC) + _poll_delay(polls - 1),
            )
            return {"status": "settlement_pending", "polls": polls}
        accounting["phase"] = "settled"

    async with conn.transaction():
        await conn.execute(
            """
            UPDATE categorize_runs SET accounting=$1, finished_at=now(), updated_at=now()
            WHERE id=$2
            """,
            accounting,
            run_id,
        )
        await enqueue_job(
            conn,
            key=f"categorize_import:{row['import_id']}:batch:{run_id}",
            kind="categorize_import",
            payload={"import_id": str(row["import_id"]), "batch_id": run_id},
        )
    return {"status": "collected", "answers": len(answers), "run_id": run_id}


async def collect_batch(
    conn: Connection,
    run_id: str,
    *,
    polls: int = 0,
    batch: Any = None,
    spend_store: Any = None,
) -> dict[str, Any]:
    """Check the batch's suspended runs; re-queue while pending, settle, and save when ended.

    Collection is not another paid call: the resumed runs only read already-submitted results,
    so they carry no `SpendLimits`. The per-item journal rows and the actual cost are recorded
    here, and the estimate is corrected once through the stored stable token.
    """
    row = await conn.fetchrow("SELECT * FROM categorize_runs WHERE id = $1", run_id)
    if row is None:
        return {"status": "gone"}
    if row["batch_answers"] is not None:
        accounting = dict(row["accounting"] or {})
        if accounting.get("phase") in {"settlement_pending", "paused_store"}:
            async with spend_store_scope(spend_store) as store:
                return await _finish_collection(
                    conn,
                    row,
                    accounting,
                    answers=list(row["batch_answers"]),
                    actual=Decimal(str(accounting["actual_usd"])),
                    store=store,
                    polls=polls,
                )
        return {"status": "already_collected"}
    runs = row["batch_runs"] or []
    if not runs:
        await conn.execute(
            "UPDATE categorize_runs SET finished_at = now(), updated_at = now() WHERE id = $1",
            run_id,
        )
        return {"status": "nothing_submitted"}

    batch = batch or configured_batch_model()
    if batch is None:
        logger.warning("collect_batch %s has no batch model configured", run_id)
        await conn.execute(
            "UPDATE categorize_runs SET finished_at = now(), updated_at = now() WHERE id = $1",
            run_id,
        )
        return {"status": "no_batch_model"}

    accounting = dict(row["accounting"] or {})
    if not accounting.get("estimate_at"):
        accounting["estimate_at"] = row["started_at"].isoformat()
    provider = str(accounting.get("provider") or "")
    if not provider:
        provider = str(getattr(getattr(batch, "provider", None), "name", "") or "")
    if provider == "gemini":
        provider = "google"
    billing_mode = str(accounting.get("billing_mode") or "metered")
    model_name = str(accounting.get("model") or getattr(batch, "model_name", "") or "")

    agent = build_labeling_agent(batch)
    answers: list[dict[str, Any]] = []
    pending = False
    actual = Decimal(0)
    provisional = False
    batching = BatchModelRequests(
        max_requests=max(1, len(runs)),
        max_wait=timedelta(seconds=30),
        max_resubmissions=0,
    )
    async with spend_store_scope(spend_store) as store:
        try:
            for entry in runs:
                history = ModelMessagesTypeAdapter.validate_python(entry["history"])
                attempt = _attempt_from(entry)
                try:
                    result = await agent.run(
                        message_history=history,
                        capabilities=[batching],
                    )
                except SuspendedResponseExpired as exc:
                    accounting["phase"] = "paused_ambiguous"
                    await _save_accounting(conn, run_id, accounting)
                    raise JobGaveUp(
                        "The provider no longer holds this batch; it was not resubmitted."
                    ) from exc
                if isinstance(result.output, PendingModelResponse):
                    pending = True
                    continue
                response = result.response
                if not response.model_name:
                    response.model_name = model_name or None
                observation = observe_cost(
                    response, provider=provider or None, billing_mode=billing_mode
                )
                if attempt is not None:
                    served = response.model_name or attempt.model
                    if served and served != attempt.model:
                        attempt = replace(attempt, model=served)
                    await record_final(conn, attempt, observation, finished_at=datetime.now(UTC))
                if observation.usd is None or not observation.complete:
                    provisional = True
                else:
                    actual += observation.usd
                keys = list(entry.get("keys") or [])
                for answer in result.output:
                    item = answer.model_dump()
                    if item.get("key") in keys:
                        answers.append(item)
        except SpendStoreError as exc:
            if accounting.get("phase") != "paused_horizon":
                accounting["phase"] = "paused_store"
                await _save_accounting(conn, run_id, accounting)
            raise JobGaveUp(f"Batch accounting stopped: {exc}") from exc

        if pending:
            polls += 1
            await enqueue_job(
                conn,
                key=f"collect_batch:{run_id}:{polls}",
                kind="collect_batch",
                payload={"run_id": run_id, "import_id": str(row["import_id"]), "polls": polls},
                run_at=datetime.now(UTC) + _poll_delay(polls - 1),
            )
            return {"status": "pending", "polls": polls}

        # Save provider results before touching the ledger. A collection retry can then settle
        # with the original token even after a provider job expires or the model config changes.
        accounting["provisional"] = provisional
        accounting["actual_usd"] = str(actual)
        accounting["phase"] = "settlement_pending"
        await conn.execute(
            "UPDATE categorize_runs SET batch_answers=$1, accounting=$2, updated_at=now() "
            "WHERE id=$3",
            answers,
            accounting,
            run_id,
        )
        return await _finish_collection(
            conn,
            row,
            accounting,
            answers=answers,
            actual=actual,
            store=store,
            polls=polls,
        )


async def handle_categorize_import(payload: dict[str, Any]) -> dict[str, Any]:
    import_id = str(payload["import_id"])
    async with connect() as conn:
        return await run_categorize(
            conn,
            import_id,
            batch_id=payload.get("batch_id"),
            model=primary_model(),
            batch=configured_batch_model(),
        )


async def handle_collect_batch(payload: dict[str, Any]) -> dict[str, Any]:
    run_id = str(payload["run_id"])
    async with connect() as conn:
        return await collect_batch(
            conn,
            run_id,
            polls=int(payload.get("polls") or 0),
            batch=configured_batch_model(),
        )


__all__ = [
    "collect_batch",
    "handle_categorize_import",
    "handle_collect_batch",
    "run_categorize",
]
