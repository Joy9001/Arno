"""Book the openings of imports committed before the commit path placed them.

An import committed by an earlier release left its statement's printed opening inside a balance
adjustment dated the day the balance was stated, so every date the statement covers read short by
that opening. This applies the commit path's own anchor repair to every committed import that has
not had it: the opening books at the statement's first day, the balance adjustments the statement's
dates explain are superseded, and a stated balance after the statement keeps its figure.

One transaction per import. The repair's write groups join the import, so undoing the import
reverses the repair too and returns the ledger to exactly the state it was handed. A dry run rolls
each transaction back after reading what it wrote, so the preview is the real thing.
"""

from __future__ import annotations

from typing import Any

from arno.db.conn import Connection
from arno.engine.money import money
from arno.importers.balance_plan import apply_balances, statement_opening
from arno.importers.commit import lock_account_and_import


class _Rollback(Exception):
    """Read a dry run, then undo its writes."""


async def _candidates(conn: Connection) -> list[dict[str, Any]]:
    """Committed imports whose anchors the old commit path placed, oldest statement first."""
    return await conn.fetch(
        """
        SELECT id, account_id, period_start, period_end, closing_balance_paise, check_result,
               commit_groups
        FROM imports
        WHERE status = 'committed' AND undone_at IS NULL AND account_id IS NOT NULL
          AND (review -> 'anchors_repaired') IS NULL
          AND check_status = 'VERIFIED'
        ORDER BY account_id, period_start NULLS LAST, committed_at, id
        """
    )


async def _repair_one(conn: Connection, item: dict[str, Any]) -> dict[str, Any] | None:
    # The account-then-import lock order commit and undo share: without the row lock, two repairs
    # or a repair racing a commit can plan against a bank balance the other has already moved.
    row = await lock_account_and_import(conn, item["id"])
    if row is None:
        return None
    account = await conn.fetchrow("SELECT * FROM v_accounts WHERE id = $1", row["account_id"])
    check = row["check_result"] or {}
    opening = statement_opening(check)
    if account is None or opening is None:
        # No evidenced opening places the curve, so there is nothing exact to repair.
        return None
    period_start = row["period_start"]
    period_end = row["period_end"]

    outcome, _ = await apply_balances(
        conn,
        account=account,
        check=check,
        period_start=period_start,
        period_end=period_end,
        closing=row["closing_balance_paise"],
    )
    review = {
        "anchors_repaired": True,
        "opening_booked_paise": outcome.opening_booked_paise,
        "superseded_anchors": outcome.superseded_anchors,
        "reanchored_paise": outcome.reanchored_paise,
    }
    await conn.execute(
        """
        UPDATE imports
        SET commit_groups = $1, review = coalesce(review, '{}'::jsonb) || $2::jsonb
        WHERE id = $3
        """,
        [str(group) for group in (row["commit_groups"] or [])] + outcome.groups,
        review,
        item["id"],
    )
    return {
        "import_id": str(item["id"]),
        "account": str(account["name"]),
        "period_start": period_start,
        "period_end": period_end,
        "opening_paise": outcome.opening_booked_paise,
        "replaced_adjustment_paise": outcome.adjustment_paise,
        "superseded": outcome.superseded_anchors,
        "reanchored_paise": outcome.reanchored_paise,
    }


async def repair_openings(conn: Connection, *, dry_run: bool) -> list[dict[str, Any]]:
    """Repair every import whose anchors were placed by the old commit path."""
    changes: list[dict[str, Any]] = []
    for item in await _candidates(conn):
        detail: dict[str, Any] | None = None
        try:
            async with conn.transaction():
                detail = await _repair_one(conn, item)
                if dry_run:
                    raise _Rollback
        except _Rollback:
            pass
        if detail is not None:
            changes.append(detail)
    return changes


def format_changes(changes: list[dict[str, Any]]) -> str:
    """One line per import: what moves, on which dates, by how much."""
    lines = []
    for change in changes:
        period = (
            f"{change['period_start'].isoformat()} to {change['period_end'].isoformat()}"
            if change["period_start"] and change["period_end"]
            else "no period"
        )
        lines.append(
            f"{change['account']}: {period} · opening booked "
            f"{money(change['opening_paise'])['text']} at the first day · closing difference "
            f"{money(change['replaced_adjustment_paise'])['text']} · "
            f"{change['superseded']} earlier balance adjustment(s) superseded · stated balance "
            f"re-anchored {money(change['reanchored_paise'])['text']}"
        )
    return "\n".join(lines)


__all__ = ["format_changes", "repair_openings"]
