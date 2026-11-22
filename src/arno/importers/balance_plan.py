"""Statement balance planning, booking, and restoration in one module.

Review and commit must plan against the same closing and the same period. This module resolves
that window once, forecasts the adjustment the review states, books the opening and anchors in one
place, and reads the review snapshot back on undo. Plain Python over the database; no agent types.
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from pydantic import BaseModel, Field, field_validator

from arno.db.conn import Connection
from arno.india.formatting import format_paise
from arno.ledger import movements as movements_mod
from arno.ledger.audit import write_group
from arno.ledger.common import system_account
from arno.ledger.errors import ConflictLedgerError, InvalidLedgerError
from arno.ledger.postings import TransferSpec, create_movement_in_group


class BalanceWindow(BaseModel):
    """The closing figure and the dates both the preview and the booking use."""

    closing: int | None = None
    period_start: date | None = None
    period_end: date | None = None


class BalancePlan(BaseModel):
    """The read-only forecast the review screen states, from one resolved window."""

    closing: int | None = None
    period_start: date | None = None
    period_end: date | None = None
    closing_adjustment_paise: int = 0
    stated_balance: tuple[int, int] | None = None


class BalanceOutcome(BaseModel):
    """What the booking wrote, for the caller's group list and review snapshot."""

    groups: list[str] = Field(default_factory=list)
    opening_booked_paise: int = 0
    superseded_anchors: int = 0
    adjustment_paise: int = 0
    reanchored_paise: int = 0


class AccountBalanceBefore(BaseModel):
    """The account's stated balance before an import replaced it.

    The review stores the date as an ISO string; Pydantic reads that back as a date and writes it
    out the same way.
    """

    statement_balance_paise: int | None = None
    statement_balance_as_of: date | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "statement_balance_paise": self.statement_balance_paise,
            "statement_balance_as_of": (
                self.statement_balance_as_of.isoformat() if self.statement_balance_as_of else None
            ),
        }


class BalanceReviewSnapshot(BaseModel):
    """The balance keys of `imports.review`, parseable from rows written by any older release.

    Missing keys take the same meaning they had before these fields existed: no restore when
    `account_before` is empty, and a stated balance is replaced unless the review said otherwise.
    """

    account_before: AccountBalanceBefore | None = None
    statement_updated: bool = True
    adjustment_paise: int | None = None
    opening_booked_paise: int | None = None
    superseded_anchors: int | None = None
    reanchored_paise: int | None = None
    anchors_repaired: bool | None = None

    @field_validator("account_before", mode="before")
    @classmethod
    def _empty_before_is_absent(cls, value: Any) -> Any:
        return None if value == {} else value

    @field_validator("statement_updated", mode="before")
    @classmethod
    def _missing_flag_updates(cls, value: Any) -> Any:
        return True if value is None else value

    def to_review_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "account_before": self.account_before.to_json() if self.account_before else None,
            "statement_updated": self.statement_updated,
            "adjustment_paise": self.adjustment_paise,
            "opening_booked_paise": self.opening_booked_paise,
            "superseded_anchors": self.superseded_anchors,
            "reanchored_paise": self.reanchored_paise,
        }
        # repair.py selects rows where this key is absent; a JSON null is not the same thing.
        if self.anchors_repaired is not None:
            result["anchors_repaired"] = self.anchors_repaired
        return result


async def _account_balance_at(
    conn: Connection, ledger_account_id: str, day: date, *, before: bool = False
) -> int:
    comparison = "<" if before else "<="
    row = await conn.fetchrow(
        f"""
        SELECT coalesce(sum(e.amount), 0)::bigint AS balance
        FROM pgledger_entries e
        JOIN movement_legs ml ON ml.transfer_id = e.transfer_id
        JOIN movements m ON m.id = ml.movement_id
        WHERE e.account_id = $1
          AND m.date {comparison} $2
        """,  # noqa: S608 - the comparison is a constant of this module
        ledger_account_id,
        day,
    )
    return int(row["balance"]) if row else 0


async def _book_adjustment(
    conn: Connection,
    account: dict[str, Any],
    *,
    amount: int,
    day: date,
    description: str,
) -> str:
    """One visible balance adjustment against Equity: an opening or an anchor, never spending."""
    equity = await system_account(conn, "equity")
    if amount > 0:
        transfer = TransferSpec(
            from_account_id=equity["id"],
            to_account_id=account["id"],
            amount_paise=amount,
        )
    else:
        transfer = TransferSpec(
            from_account_id=account["id"],
            to_account_id=equity["id"],
            amount_paise=-amount,
        )
    async with write_group(conn, actor="user") as group:
        await create_movement_in_group(
            conn,
            group,
            date=day,
            description=description,
            kind="adjustment",
            transfers=[transfer],
        )
    return group.group_id


_TRUSTED_OPENINGS = ("printed", "manual", "ledger")


def statement_opening(check: dict[str, Any]) -> int | None:
    """The opening the checks trusted: the printed figure, the user's answer, or the ledger's own.

    A parser's fallback zero is a claim, not a fact, so it places nothing.
    """
    if check.get("opening_source") not in _TRUSTED_OPENINGS:
        return None
    value = check.get("opening_paise")
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return value


def statement_balance_on(
    lines: list[dict[str, Any]], check: dict[str, Any], day: date
) -> int | None:
    """The statement's own balance on `day`: its opening plus every row dated on or before it."""
    opening = statement_opening(check)
    if opening is None:
        return None
    running = opening
    for line in sorted(lines, key=lambda line: line["date"]):
        if line["date"] > day:
            break
        running += int(line["amount_paise"])
    return running


async def stated_balance_conflict(
    conn: Connection,
    *,
    account: dict[str, Any],
    check: dict[str, Any],
    lines: list[dict[str, Any]],
    period_start: date | None,
    period_end: date | None,
) -> tuple[int, int] | None:
    """The user's stated balance and the statement's own, when the two disagree on a day it covers.

    A stated balance is a fact about its day. Inside the statement's period the bank's own rows
    are the evidence for that day, so the statement wins — but the user is told and confirms. A
    figure that is only the last committed statement's own closing is not the user's claim: the
    closing adjustment already states it, so it is not a second confirmation.
    """
    day = account.get("statement_balance_as_of")
    stated = account.get("statement_balance_paise")
    if day is None or not isinstance(stated, int) or isinstance(stated, bool):
        return None
    if isinstance(day, str):
        day = date.fromisoformat(day)
    if period_start is None or period_end is None or not period_start <= day <= period_end:
        return None
    from_statement = await conn.fetchrow(
        """
        SELECT 1 FROM imports
        WHERE account_id = $1 AND status = 'committed' AND undone_at IS NULL
          AND period_end = $2 AND closing_balance_paise = $3
        LIMIT 1
        """,
        account["id"],
        day,
        stated,
    )
    if from_statement is not None:
        return None
    shown = statement_balance_on(lines, check, day)
    if shown is None or shown == stated:
        return None
    return stated, shown


def stated_balance_reason(stated: int, day: date, statement: int) -> str:
    """Why a commit replaces a stated balance, in one host-written sentence."""
    return (
        f"You stated {format_paise(stated)} on {day.isoformat()}; the statement shows "
        f"{format_paise(statement)} on that day. Committing replaces your figure with the "
        "statement's; confirm that first."
    )


def closing_adjustment_reason(difference: int, day: date) -> str:
    """Why a commit books a balance adjustment, in one host-written sentence."""
    side = "above" if difference > 0 else "below"
    return (
        f"The statement's closing balance is {format_paise(abs(difference))} {side} the ledger "
        f"at {day.isoformat()}, so committing it books that difference as a visible balance "
        "adjustment."
    )


def closing_source_reason(closing: int) -> str:
    """Why the review's closing came from the rows instead of a printed figure."""
    return (
        "No printed closing balance was read on this statement, so the closing shown here "
        f"({format_paise(closing)}) is its last row's running balance. Check it against the "
        "statement's own closing line before committing."
    )


def line_date_or_none(lines: list[dict[str, Any]]) -> date | None:
    return max((line["date"] for line in lines), default=None)


def resolve_balance_window(row: dict[str, Any], lines: list[dict[str, Any]]) -> BalanceWindow:
    """The one closing and the one period both the preview and the booking plan against.

    `imports.closing_balance_paise` is the sole closing figure (`NULL` means no closing balance to
    reconcile or book). Missing dates fall back to the statement's own first and last rows, so a
    forecast cannot use a different window than booking.
    """
    raw_closing = row["closing_balance_paise"]
    closing = (
        int(raw_closing)
        if isinstance(raw_closing, int) and not isinstance(raw_closing, bool)
        else None
    )
    period_start = row["period_start"] or min((line["date"] for line in lines), default=None)
    period_end = row["period_end"] or line_date_or_none(lines)
    return BalanceWindow(closing=closing, period_start=period_start, period_end=period_end)


async def _superseded_anchors(
    conn: Connection, ledger_account_id: str, start: date, end: date
) -> list[dict[str, Any]]:
    """The balance adjustments a statement's own dates now explain, oldest first."""
    rows = await conn.fetch(
        """
        SELECT m.id, m.date,
               sum(CASE WHEN t.to_account_id = $1 THEN t.amount ELSE -t.amount END)::bigint AS net
        FROM movements m
        JOIN v_movement_state ms ON ms.movement_id = m.id
        JOIN movement_legs ml ON ml.movement_id = m.id
        JOIN pgledger_transfers t ON t.id = ml.transfer_id
        WHERE m.kind = 'adjustment'
          AND ms.is_effective
          AND m.date BETWEEN $2 AND $3
          AND (t.from_account_id = $1 OR t.to_account_id = $1)
        GROUP BY m.id, m.date
        HAVING sum(CASE WHEN t.to_account_id = $1 THEN t.amount ELSE -t.amount END) <> 0
        ORDER BY m.date, m.id
        """,
        ledger_account_id,
        start,
        end,
    )
    return [dict(row) for row in rows]


async def _reanchor_opening(
    conn: Connection,
    account: dict[str, Any],
    *,
    period_start: date | None,
    period_end: date | None,
    opening: int | None,
) -> tuple[list[str], int, int]:
    """Book the statement's opening at its first day and supersede what it explains.

    An account with no earlier entry, or one a later statement moved, holds no balance at
    `period_start`: book the printed opening there. A balance adjustment dated inside the
    statement is the old answer to a question this statement now answers row by row, so it is
    reversed; a genuine difference still shows in the closing adjustment.
    """
    groups: list[str] = []
    if period_start is None or opening is None:
        return groups, 0, 0
    superseded = await _superseded_anchors(
        conn, account["ledger_account_id"], period_start, period_end or period_start
    )
    booked = opening - await _account_balance_at(
        conn, account["ledger_account_id"], period_start, before=True
    )
    if booked:
        groups.append(
            await _book_adjustment(
                conn,
                account,
                amount=booked,
                day=period_start,
                description=f"Opening balance for {account['name']} statement",
            )
        )
    for row in superseded:
        async with write_group(conn, actor="user") as group:
            await movements_mod.reverse_movement(group, str(row["id"]))
        groups.append(group.group_id)
    return groups, booked, len(superseded)


async def _hold_later_anchor(
    conn: Connection,
    account: dict[str, Any],
    *,
    period_end: date | None,
) -> tuple[str | None, int]:
    """Keep a balance stated after the statement: the balance on that day stays its figure.

    A stated balance is a fact about its day. The statement's own dates never reach it, so the
    difference is genuine and visible at that day, never inside the period the statement explains.
    """
    anchor_day = account["statement_balance_as_of"]
    anchor_paise = account["statement_balance_paise"]
    if period_end is None or anchor_day is None or anchor_paise is None or anchor_day <= period_end:
        return None, 0
    current = await _account_balance_at(conn, account["ledger_account_id"], anchor_day)
    residual = int(anchor_paise) - current
    if residual == 0:
        return None, 0
    group = await _book_adjustment(
        conn,
        account,
        amount=residual,
        day=anchor_day,
        description=f"Stated balance re-anchor for {account['name']} statement",
    )
    return group, residual


async def _closing_difference(
    conn: Connection,
    *,
    account: dict[str, Any],
    closing: int,
    day: date,
) -> int:
    ledger = await _account_balance_at(conn, account["ledger_account_id"], day)
    return int(closing) - ledger


async def _adjust_closing(
    conn: Connection,
    *,
    account: dict[str, Any],
    closing: int,
    day: date,
) -> tuple[str | None, int]:
    difference = await _closing_difference(conn, account=account, closing=closing, day=day)
    if difference == 0:
        return None, 0
    group = await _book_adjustment(
        conn,
        account,
        amount=difference,
        day=day,
        description=f"Balance adjustment for {account['name']} statement",
    )
    return group, difference


async def review_closing_adjustment(
    conn: Connection,
    *,
    account: dict[str, Any],
    check: dict[str, Any],
    window: BalanceWindow,
    lines: list[dict[str, Any]],
) -> int:
    """The balance adjustment a commit would book at the statement's last day, before it runs.

    The review screen states this amount, so the same arithmetic the commit does: the rows it
    would write (a merge only moves the difference), the opening it would book at the first day,
    and the adjustments the statement's own dates supersede. A forecast is not the booking: the
    commit recomputes the difference and refuses to book one the review did not confirm.
    """
    closing = window.closing
    period_start = window.period_start
    period_end = window.period_end
    if closing is None or period_end is None:
        return 0
    delta = 0
    for line in lines:
        if line["match_state"] == "ignored":
            continue
        amount = int(line["amount_paise"])
        already = 0
        if line["movement_id"]:
            movement_id = str(line["movement_id"])
            if await _movement_date(conn, movement_id) <= period_end:
                already = await _net_on_account(conn, movement_id, account["ledger_account_id"])
        delta += amount - already
    opening = statement_opening(check)
    if opening is not None and period_start is not None:
        delta += opening - await _account_balance_at(
            conn, account["ledger_account_id"], period_start, before=True
        )
        if period_end >= period_start:
            for row in await _superseded_anchors(
                conn, account["ledger_account_id"], period_start, period_end
            ):
                delta -= int(row["net"])
    ledger = await _account_balance_at(conn, account["ledger_account_id"], period_end)
    return int(closing) - (ledger + delta)


async def plan_balances(
    conn: Connection,
    *,
    account: dict[str, Any],
    row: dict[str, Any],
    lines: list[dict[str, Any]],
) -> BalancePlan:
    """Read-only forecast for the review screen, from the same window the commit books."""
    window = resolve_balance_window(row, lines)
    check = row["check_result"] or {}
    adjustment = await review_closing_adjustment(
        conn, account=account, check=check, window=window, lines=lines
    )
    conflict = await stated_balance_conflict(
        conn,
        account=account,
        check=check,
        lines=lines,
        period_start=window.period_start,
        period_end=window.period_end,
    )
    return BalancePlan(
        closing=window.closing,
        period_start=window.period_start,
        period_end=window.period_end,
        closing_adjustment_paise=adjustment,
        stated_balance=conflict,
    )


async def apply_balances(
    conn: Connection,
    *,
    account: dict[str, Any],
    check: dict[str, Any],
    period_start: date | None,
    period_end: date | None,
    closing: int | None,
    adjustment_confirmed: bool = False,
) -> tuple[BalanceOutcome, dict[str, Any]]:
    """Book the opening, the closing adjustment, and a later anchor, once both paths hold locks.

    Returns the groups to join to the import's undo groups and the balance keys for its review.
    A non-zero closing difference needs the review screen's confirmation before it is booked.
    """
    groups: list[str] = []
    anchor_groups, opening_booked, superseded = await _reanchor_opening(
        conn,
        account,
        period_start=period_start,
        period_end=period_end,
        opening=statement_opening(check),
    )
    groups.extend(anchor_groups)

    adjustment = 0
    if closing is not None and period_end is not None:
        difference = await _closing_difference(
            conn, account=account, closing=closing, day=period_end
        )
        # A statement whose closing disagrees with the ledger is a real difference, not a rounding.
        # It is the user's call: the review says the amount, and commit books it only when the
        # review screen carried that confirmation.
        if difference and not adjustment_confirmed:
            raise ConflictLedgerError(
                closing_adjustment_reason(difference, period_end)
                + " Confirm the adjustment first.",
                details={"adjustment_paise": difference, "adjustment_confirmed": False},
            )
        adjustment_group, adjustment = await _adjust_closing(
            conn, account=account, closing=closing, day=period_end
        )
        if adjustment_group:
            groups.append(adjustment_group)

    reanchored = 0
    anchor_group, reanchored = await _hold_later_anchor(conn, account, period_end=period_end)
    if anchor_group:
        groups.append(anchor_group)

    existing_as_of = account["statement_balance_as_of"]
    statement_updated = (
        closing is not None
        and period_end is not None
        and (existing_as_of is None or period_end >= existing_as_of)
    )
    snapshot = BalanceReviewSnapshot(
        account_before=AccountBalanceBefore(
            statement_balance_paise=account["statement_balance_paise"],
            statement_balance_as_of=existing_as_of,
        ),
        statement_updated=statement_updated,
        adjustment_paise=adjustment,
        opening_booked_paise=opening_booked,
        superseded_anchors=superseded,
        reanchored_paise=reanchored,
        anchors_repaired=True,
    )
    outcome = BalanceOutcome(
        groups=groups,
        opening_booked_paise=opening_booked,
        superseded_anchors=superseded,
        adjustment_paise=adjustment,
        reanchored_paise=reanchored,
    )
    return outcome, snapshot.to_review_json()


async def restore_account_balance(
    conn: Connection,
    *,
    account_id: str,
    review: dict[str, Any] | None,
    period_end: date | None,
    closing: int | None,
) -> None:
    """Restore the stated balance an import replaced, while that import is still the source.

    Rows written before the review carried these keys have no `account_before`, so they skip the
    restore; a missing `statement_updated` means the older behavior, which did replace the balance.
    """
    snapshot = BalanceReviewSnapshot.model_validate(review or {})
    before = snapshot.account_before
    if before is None or not snapshot.statement_updated:
        return
    current = await conn.fetchrow(
        """
        SELECT statement_balance_paise, statement_balance_as_of
        FROM accounts WHERE id = $1
        """,
        account_id,
    )
    current_as_of = current["statement_balance_as_of"] if current else None
    # Restore only while this import is still the effective balance source. A newer statement,
    # or a manual balance on the same date, must survive the undo.
    is_effective = (
        current is not None
        and (period_end is None or current_as_of is None or current_as_of <= period_end)
        and (
            period_end is None
            or current_as_of != period_end
            or closing is None
            or current["statement_balance_paise"] == closing
        )
    )
    if is_effective:
        await conn.execute(
            """
            UPDATE accounts
            SET statement_balance_paise = $1, statement_balance_as_of = $2, updated_at = now()
            WHERE id = $3
            """,
            before.statement_balance_paise,
            before.statement_balance_as_of,
            account_id,
        )


async def _movement_date(conn: Connection, movement_id: str) -> date:
    row = await conn.fetchrow("SELECT date FROM movements WHERE id = $1", movement_id)
    if row is None:
        raise InvalidLedgerError("The matched movement disappeared")
    return cast(date, row["date"])  # movements.date is a date column


async def _net_on_account(conn: Connection, movement_id: str, ledger_account_id: str) -> int:
    row = await conn.fetchrow(
        """
        SELECT coalesce(sum(
            CASE WHEN t.from_account_id = $1 THEN -t.amount
                 WHEN t.to_account_id = $2 THEN t.amount
                 ELSE 0 END
        ), 0)::bigint AS net
        FROM movement_legs ml
        JOIN pgledger_transfers t ON t.id = ml.transfer_id
        WHERE ml.movement_id = $3
        """,
        ledger_account_id,
        ledger_account_id,
        movement_id,
    )
    return int(row["net"]) if row else 0


# Public seams for the balance tests; the booking path calls the private helpers above directly.
book_adjustment = _book_adjustment
superseded_anchors = _superseded_anchors
adjust_closing = _adjust_closing


__all__ = [
    "AccountBalanceBefore",
    "BalanceOutcome",
    "BalancePlan",
    "BalanceReviewSnapshot",
    "BalanceWindow",
    "adjust_closing",
    "apply_balances",
    "book_adjustment",
    "closing_adjustment_reason",
    "closing_source_reason",
    "line_date_or_none",
    "plan_balances",
    "resolve_balance_window",
    "restore_account_balance",
    "review_closing_adjustment",
    "statement_balance_on",
    "statement_opening",
    "stated_balance_conflict",
    "stated_balance_reason",
    "superseded_anchors",
]
