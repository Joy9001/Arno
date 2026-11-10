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
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic_ai.capabilities import BatchModelRequests
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.output import PendingModelResponse

from arno.agent.capabilities import drain_events, spend_limits
from arno.agent.categorizer import (
    CATEGORIZE_USAGE_LIMITS,
    CategorizerDeps,
    build_categorizer_agent,
    build_labeling_agent,
)
from arno.agent.models import batch_model as configured_batch_model
from arno.agent.models import primary_model
from arno.agent.spend import ceiling_name, spend_store_scope
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
                    SELECT 1 FROM transfer_labels tl
                    WHERE tl.movement_id = m.id AND tl.category_id IS NOT NULL
                )
            )::int AS labeled,
            count(*) FILTER (
                WHERE NOT EXISTS (
                    SELECT 1 FROM transfer_labels tl
                    WHERE tl.movement_id = m.id AND tl.category_id IS NOT NULL
                )
            )::int AS uncategorized
        FROM statement_lines sl
        JOIN movements m ON m.id = sl.movement_id
        WHERE sl.import_id = $1 AND sl.match_state = 'merged' AND m.reverses_id IS NULL
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


async def collect_batch(
    conn: Connection,
    run_id: str,
    *,
    polls: int = 0,
    batch: Any = None,
    spend_store: Any = None,
) -> dict[str, Any]:
    """Check the batch's suspended runs; re-queue while pending, save when ended."""
    row = await conn.fetchrow("SELECT * FROM categorize_runs WHERE id = $1", run_id)
    if row is None:
        return {"status": "gone"}
    if row["batch_answers"] is not None:
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

    agent = build_labeling_agent(batch)
    answers: list[dict[str, Any]] = []
    pending = False
    async with spend_store_scope(spend_store) as store:
        batching = BatchModelRequests(
            max_requests=max(1, len(runs)), max_wait=timedelta(seconds=30)
        )
        limits = spend_limits(store)
        for entry in runs:
            history = ModelMessagesTypeAdapter.validate_python(entry["history"])
            result = await agent.run(
                message_history=history,
                capabilities=[batching, *([limits] if limits is not None else [])],
            )
            if isinstance(result.output, PendingModelResponse):
                pending = True
                continue
            keys = list(entry.get("keys") or [])
            for answer in result.output:
                item = answer.model_dump()
                if item.get("key") in keys:
                    answers.append(item)

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

    async with conn.transaction():
        await conn.execute(
            """
            UPDATE categorize_runs
            SET batch_answers = $1, finished_at = now(), updated_at = now()
            WHERE id = $2
            """,
            answers,
            run_id,
        )
        await enqueue_job(
            conn,
            key=f"categorize_import:{row['import_id']}:batch:{run_id}",
            kind="categorize_import",
            payload={"import_id": str(row["import_id"]), "batch_id": run_id},
        )
    return {"status": "collected", "answers": len(answers), "run_id": run_id}


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
