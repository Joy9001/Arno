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
from arno.importers.balance_plan import apply_balances, rebalance_after, statement_opening
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
    if (
        row is None
        or row["status"] != "committed"
        or row["undone_at"] is not None
        or (row["review"] or {}).get("anchors_repaired") is not None
    ):
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
        adjustment_confirmed=True,
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


async def _committed(conn: Connection) -> list[dict[str, Any]]:
    """Every live committed import with an account, oldest statement first."""
    return await conn.fetch(
        """
        SELECT id FROM imports
        WHERE status = 'committed' AND undone_at IS NULL AND account_id IS NOT NULL
        ORDER BY account_id, period_start NULLS LAST, committed_at, id
        """
    )


async def _rebalance_one(conn: Connection, item: dict[str, Any]) -> dict[str, Any] | None:
    """Re-place what sits after one import, as its commit does today.

    An older statement committed after a newer one left the newer opening on top of rows that
    already explain it. The writes join this import, so undoing it restores exactly what it found.
    """
    row = await lock_account_and_import(conn, item["id"])
    if row is None or row["status"] != "committed" or row["undone_at"] is not None:
        return None
    account = await conn.fetchrow("SELECT * FROM v_accounts WHERE id = $1", row["account_id"])
    if account is None:
        return None
    groups, reanchored = await rebalance_after(conn, account, period_end=row["period_end"])
    if not groups:
        return None
    await conn.execute(
        "UPDATE imports SET commit_groups = $1 WHERE id = $2",
        [str(group) for group in (row["commit_groups"] or [])] + groups,
        item["id"],
    )
    return {
        "import_id": str(item["id"]),
        "account": str(account["name"]),
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "opening_paise": 0,
        "replaced_adjustment_paise": 0,
        "superseded": 0,
        "reanchored_paise": reanchored,
        "later_openings_rebooked": True,
    }


async def _each_in_its_own_transaction(
    conn: Connection, items: list[dict[str, Any]], step: Any, *, dry_run: bool
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for item in items:
        detail: dict[str, Any] | None = None
        try:
            async with conn.transaction():
                detail = await step(conn, item)
                if dry_run:
                    raise _Rollback
        except _Rollback:
            pass
        if detail is not None:
            changes.append(detail)
    return changes


async def repair_openings(conn: Connection, *, dry_run: bool) -> list[dict[str, Any]]:
    """Repair every import whose anchors an older commit path placed.

    Two passes: imports that never had the opening repair, then every committed import's later
    openings and stated balance, which an out-of-order commit could leave stale. A dry run rolls
    each transaction back, so the second pass previews against the unrepaired first pass; the
    preview can list a later-opening change the first pass would have made unnecessary.
    """
    changes = await _each_in_its_own_transaction(
        conn, await _candidates(conn), _repair_one, dry_run=dry_run
    )
    changes += await _each_in_its_own_transaction(
        conn, await _committed(conn), _rebalance_one, dry_run=dry_run
    )
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
        if change.get("later_openings_rebooked"):
            lines.append(
                f"{change['account']}: {period} · later statement openings re-placed · stated "
                f"balance re-anchored {money(change['reanchored_paise'])['text']}"
            )
            continue
        lines.append(
            f"{change['account']}: {period} · opening booked "
            f"{money(change['opening_paise'])['text']} at the first day · closing difference "
            f"{money(change['replaced_adjustment_paise'])['text']} · "
            f"{change['superseded']} earlier balance adjustment(s) superseded · stated balance "
            f"re-anchored {money(change['reanchored_paise'])['text']}"
        )
    return "\n".join(lines)


__all__ = ["format_changes", "repair_openings"]
