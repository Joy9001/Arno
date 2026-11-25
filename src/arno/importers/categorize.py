"""Categorizing after commit: rules first, then the categorizer agent.

The job runner starts `categorize_import` after a commit. It labels every row a rule covers and
runs the agent on the rest.

A run says what happened: `done`, `partial` (some payees got no answer from the model and stay
uncategorized), or `nothing_to_do`, and a finished run puts a counts-only item in the inbox. A run
the agent could not finish raises, so the job runner retries it and, at the end, reports the
failure; a spend or usage ceiling gives up at once, since a retry hits it again.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic_ai.exceptions import UsageLimitExceeded

from arno.agent.capabilities import drain_events
from arno.agent.categorizer import (
    CATEGORIZE_USAGE_LIMITS,
    CategorizerDeps,
    build_categorizer_agent,
    save_held_answers,
)
from arno.agent.models import primary_model
from arno.agent.usage import DailyBudgetExceeded
from arno.db.conn import Connection, connect
from arno.importers import rules as rules_mod
from arno.jobs.runner import JobGaveUp
from arno.ledger.audit import new_id
from arno.notifications import create_notification

logger = logging.getLogger("arno.importers.categorize")


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


async def _keep_held_answers(conn: Connection, deps: CategorizerDeps, exc: Exception) -> bool:
    """Keep the answers a failed run already paid for, as hints, unless a spend stop ended it.

    Without this a model error after `categorize_now` threw the answers away and the job retry
    asked the same payees again. True when answers were saved, so the job ends instead of retrying.
    """
    if deps.saved or not deps.answers or isinstance(exc, DailyBudgetExceeded):
        return False
    # No agent reviewed these answers, so they are kept as hints only: nothing is labeled and no
    # rule is learned until a reviewed run or the user confirms them.
    await save_held_answers(conn, deps, distrust=set(deps.answers))
    return True


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


async def _announce(conn: Connection, import_id: str, result: dict[str, Any]) -> None:
    if result["labeled"] + result["uncategorized"] == 0:
        return
    await create_notification(
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


async def run_categorize(
    conn: Connection,
    import_id: str,
    *,
    model: Any = None,
) -> dict[str, Any]:
    """Rules, then the agent, for one committed import."""
    await rules_mod.apply_rules(conn, import_id)
    run_id = await new_id(conn, "cat")
    await conn.execute(
        "INSERT INTO categorize_runs (id, import_id, mode) VALUES ($1, $2, 'now')",
        run_id,
        import_id,
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
        await _announce(conn, import_id, result)
        return result

    deps = CategorizerDeps(import_id=import_id, run_id=run_id, model=model)
    agent = build_categorizer_agent(model=model)
    try:
        await agent.run(
            f"Categorize the payees of import {import_id}. Save once, then report.",
            deps=deps,
            usage_limits=CATEGORIZE_USAGE_LIMITS,
            event_stream_handler=drain_events,
            # One trace session per import, so a retry joins the run it repeats.
            conversation_id=f"categorize-{import_id}",
        )
    except Exception as exc:
        # The import stays committed either way; this only decides how the job ends.
        salvaged = await _keep_held_answers(conn, deps, exc)
        await _finish_run(conn, run_id, import_id, deps)
        if salvaged:
            logger.warning(
                "the categorizer stopped after asking (%s); kept its %d answers",
                type(exc).__name__,
                len(deps.answers),
            )
        if isinstance(exc, DailyBudgetExceeded):
            raise JobGaveUp(str(exc)) from exc
        if isinstance(exc, UsageLimitExceeded):
            raise JobGaveUp(f"Stopped at the categorizer's step limit: {exc}") from exc
        if not salvaged:
            raise

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
    await _announce(conn, import_id, result)
    return result


async def handle_categorize_import(payload: dict[str, Any]) -> dict[str, Any]:
    async with connect() as conn:
        return await run_categorize(conn, str(payload["import_id"]), model=primary_model())


__all__ = ["handle_categorize_import", "run_categorize"]
