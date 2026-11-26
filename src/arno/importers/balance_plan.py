"""Statement balance planning, booking, and restoration in one module.

Review and commit must plan against the same closing and the same period. `plan_balances` is the
one function for both: it resolves the window once and either forecasts what the review states or,
under the commit's locks, books it. The opening and re-anchor adjustments it books carry the
import's id, so a later commit finds them by that id. The review snapshot is read back on undo.
Plain Python over the database; no agent types.
"""

from __future__ import annotations

from datetime import date
from typing import Any

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
    """What `plan_balances` returns: the forecast, and when booking, what it wrote.

    `groups` and `review` stay empty for a forecast. After booking, `closing_adjustment_paise` is
    the adjustment written and `review` holds the balance keys of `imports.review`.
    """

    closing: int | None = None
    period_start: date | None = None
    period_end: date | None = None
    closing_adjustment_paise: int = 0
    stated_balance: tuple[int, int] | None = None
    groups: list[str] = Field(default_factory=list)
    review: dict[str, Any] = Field(default_factory=dict)


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
    import_id: str | None = None,
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
            import_id=import_id,
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


def _opening_description(account: dict[str, Any]) -> str:
    return f"Opening balance for {account['name']} statement"


def _reanchor_description(account: dict[str, Any]) -> str:
    return f"Stated balance re-anchor for {account['name']} statement"


async def _import_adjustments(
    conn: Connection, ledger_account_id: str, day: date, *, opening_of: str | None
) -> list[dict[str, Any]]:
    """Live import adjustments on one day, with their net on the account.

    With `opening_of`, the opening that import placed on its first day. Without it, the
    re-anchors: an import's adjustment dated after that import's own period. A closing
    adjustment carries no import, so neither lookup sees it.
    """
    rows = await conn.fetch(
        """
        SELECT m.id,
               sum(CASE WHEN t.to_account_id = $1 THEN t.amount ELSE -t.amount END)::bigint AS net
        FROM movements m
        JOIN imports i ON i.id = m.import_id
        JOIN v_movement_state ms ON ms.movement_id = m.id
        JOIN movement_legs ml ON ml.movement_id = m.id
        JOIN pgledger_transfers t ON t.id = ml.transfer_id
        WHERE m.kind = 'adjustment'
          AND ms.is_effective
          AND m.date = $2
          AND CASE WHEN $3::text IS NULL
                   THEN m.date > coalesce(
                       i.period_end,
                       i.period_start,
                       -- An import with no printed period ends on its last line, like its window.
                       (SELECT max(sl.date) FROM statement_lines sl WHERE sl.import_id = i.id)
                   )
                   ELSE i.id = $3 END
          AND (t.from_account_id = $1 OR t.to_account_id = $1)
        GROUP BY m.id
        ORDER BY m.id
        """,
        ledger_account_id,
        day,
        opening_of,
    )
    return [dict(row) for row in rows]


async def _rebook(
    conn: Connection,
    account: dict[str, Any],
    *,
    day: date,
    description: str,
    import_id: str,
    opening_of: str | None,
    wanted: int,
) -> list[str]:
    """Make this day's adjustments of one kind net to `wanted`: reverse them and book it once."""
    rows = await _import_adjustments(conn, account["ledger_account_id"], day, opening_of=opening_of)
    if sum(int(row["net"]) for row in rows) == wanted:
        return []
    groups: list[str] = []
    for row in rows:
        async with write_group(conn, actor="user") as group:
            await movements_mod.reverse_movement(group, str(row["id"]))
        groups.append(group.group_id)
    if wanted:
        groups.append(
            await _book_adjustment(
                conn,
                account,
                amount=wanted,
                day=day,
                description=description,
                import_id=import_id,
            )
        )
    return groups


async def _rebalance_later_openings(
    conn: Connection, account: dict[str, Any], *, after: date
) -> list[str]:
    """Re-place the openings of later statements once earlier rows explain their first day.

    A statement committed before an older one booked its printed opening to fill an empty
    ledger. When the older statement lands, its rows already carry the balance to that day, so
    the later opening must shrink to what is still unexplained, or every day of the later
    statement reads high by the overlap. Oldest later statement first, since each one's first
    day depends on the ones before it.
    """
    rows = await conn.fetch(
        """
        SELECT id, period_start, check_result FROM imports
        WHERE account_id = $1 AND status = 'committed' AND undone_at IS NULL
          AND period_start > $2
        ORDER BY period_start, committed_at, id
        """,
        account["id"],
        after,
    )
    groups: list[str] = []
    description = _opening_description(account)
    for row in rows:
        opening = statement_opening(row["check_result"] or {})
        if opening is None:
            continue
        day = row["period_start"]
        before = await _account_balance_at(conn, account["ledger_account_id"], day, before=True)
        groups.extend(
            await _rebook(
                conn,
                account,
                day=day,
                description=description,
                import_id=row["id"],
                opening_of=row["id"],
                wanted=opening - before,
            )
        )
    return groups


async def rebalance_after(
    conn: Connection, account: dict[str, Any], *, period_end: date | None, import_id: str
) -> tuple[list[str], int]:
    """Re-place what sits after a statement: later openings, then a later stated balance.

    Commit runs this after booking a statement. With nothing stale it writes nothing.
    """
    groups: list[str] = []
    if period_end is not None:
        groups.extend(await _rebalance_later_openings(conn, account, after=period_end))
    anchor_groups, reanchored = await _hold_later_anchor(
        conn, account, period_end=period_end, import_id=import_id
    )
    groups.extend(anchor_groups)
    return groups, reanchored


async def _hold_later_anchor(
    conn: Connection,
    account: dict[str, Any],
    *,
    period_end: date | None,
    import_id: str,
) -> tuple[list[str], int]:
    """Keep a balance stated after the statement: the balance on that day stays its figure.

    A stated balance is a fact about its day. The statement's own dates never reach it, so the
    difference is genuine and visible at that day, never inside the period the statement explains.
    The re-anchor is recomputed, not stacked: an earlier re-anchor on that day is replaced by the
    one difference that is still unexplained, which is zero once the rows explain it.
    """
    anchor_day = account["statement_balance_as_of"]
    anchor_paise = account["statement_balance_paise"]
    if period_end is None or anchor_day is None or anchor_paise is None or anchor_day <= period_end:
        return [], 0
    current = await _account_balance_at(conn, account["ledger_account_id"], anchor_day)
    held = sum(
        int(row["net"])
        for row in await _import_adjustments(
            conn, account["ledger_account_id"], anchor_day, opening_of=None
        )
    )
    wanted = int(anchor_paise) - (current - held)
    groups = await _rebook(
        conn,
        account,
        day=anchor_day,
        description=_reanchor_description(account),
        import_id=import_id,
        opening_of=None,
        wanted=wanted,
    )
    return groups, (wanted - held) if groups else 0


async def _forecast_delta(
    conn: Connection, ledger_account_id: str, lines: list[dict[str, Any]], period_end: date
) -> int:
    """What the statement's rows would add to the account by `period_end`.

    A merge only moves the difference to the movement it matched.
    """
    kept = [line for line in lines if line["match_state"] != "ignored"]
    matched = {str(line["movement_id"]) for line in kept if line["movement_id"]}
    found: dict[str, tuple[date, int]] = {}
    if matched:
        rows = await conn.fetch(
            """
            SELECT m.id, m.date,
                   coalesce(sum(CASE WHEN t.from_account_id = $1 THEN -t.amount
                                     WHEN t.to_account_id = $1 THEN t.amount
                                     ELSE 0 END), 0)::bigint AS net
            FROM movements m
            LEFT JOIN movement_legs ml ON ml.movement_id = m.id
            LEFT JOIN pgledger_transfers t ON t.id = ml.transfer_id
            WHERE m.id = ANY($2::text[])
            GROUP BY m.id, m.date
            """,
            ledger_account_id,
            sorted(matched),
        )
        found = {str(row["id"]): (row["date"], int(row["net"])) for row in rows}
    delta = 0
    for line in kept:
        already = 0
        if line["movement_id"]:
            movement = found.get(str(line["movement_id"]))
            if movement is None:
                raise InvalidLedgerError("The matched movement disappeared")
            if movement[0] <= period_end:
                already = movement[1]
        delta += int(line["amount_paise"]) - already
    return delta


async def plan_balances(
    conn: Connection,
    *,
    account: dict[str, Any],
    row: dict[str, Any],
    lines: list[dict[str, Any]],
    book: bool = False,
    adjustment_confirmed: bool = False,
) -> BalancePlan:
    """The one plan for a statement's balances: the review's forecast, or the commit's booking.

    Both read the same window. Without `book` it writes nothing and forecasts the adjustment a
    commit would book at the statement's last day: the rows it would write (a merge only moves
    the difference), the opening it would place at the first day, and the adjustments the
    statement's own dates supersede. With `book`, called after the commit wrote its rows and
    under its locks, it places the opening, supersedes those adjustments, books the closing
    difference, and re-places what sits after the statement. The booking recomputes the
    difference from the ledger and refuses one the review did not confirm.
    """
    window = resolve_balance_window(row, lines)
    check = row["check_result"] or {}
    closing, start, end = window.closing, window.period_start, window.period_end
    ledger_id = account["ledger_account_id"]
    opening = statement_opening(check)
    opening_delta = 0
    superseded: list[dict[str, Any]] = []
    if opening is not None and start is not None:
        superseded = await _superseded_anchors(conn, ledger_id, start, end or start)
        opening_delta = opening - await _account_balance_at(conn, ledger_id, start, before=True)

    if not book:
        adjustment = 0
        if closing is not None and end is not None:
            delta = await _forecast_delta(conn, ledger_id, lines, end)
            delta += opening_delta - sum(int(item["net"]) for item in superseded)
            adjustment = int(closing) - (await _account_balance_at(conn, ledger_id, end) + delta)
        conflict = await stated_balance_conflict(
            conn,
            account=account,
            check=check,
            lines=lines,
            period_start=start,
            period_end=end,
        )
        return BalancePlan(
            closing=closing,
            period_start=start,
            period_end=end,
            closing_adjustment_paise=adjustment,
            stated_balance=conflict,
        )

    # The statement is bank evidence for every day it covers, so a stated balance on one of those
    # days is replaced. Never silently: the review says both figures, and this gate needs its yes.
    conflict = await stated_balance_conflict(
        conn, account=account, check=check, lines=lines, period_start=start, period_end=end
    )
    if conflict is not None and not adjustment_confirmed:
        stated, shown = conflict
        raise ConflictLedgerError(
            stated_balance_reason(stated, account["statement_balance_as_of"], shown),
            details={"stated_balance_paise": stated, "statement_balance_paise": shown},
        )
    import_id = str(row["id"])
    groups: list[str] = []
    if opening_delta and start is not None:
        groups.append(
            await _book_adjustment(
                conn,
                account,
                amount=opening_delta,
                day=start,
                description=_opening_description(account),
                import_id=import_id,
            )
        )
    for item in superseded:
        async with write_group(conn, actor="user") as group:
            await movements_mod.reverse_movement(group, str(item["id"]))
        groups.append(group.group_id)

    adjustment = 0
    if closing is not None and end is not None:
        difference = int(closing) - await _account_balance_at(conn, ledger_id, end)
        # A statement whose closing disagrees with the ledger is a real difference, not a rounding.
        # It is the user's call: the review says the amount, and commit books it only when the
        # review screen carried that confirmation.
        if difference and not adjustment_confirmed:
            raise ConflictLedgerError(
                closing_adjustment_reason(difference, end) + " Confirm the adjustment first.",
                details={"adjustment_paise": difference, "adjustment_confirmed": False},
            )
        if difference:
            adjustment = difference
            groups.append(
                await _book_adjustment(
                    conn,
                    account,
                    amount=difference,
                    day=end,
                    description=f"Balance adjustment for {account['name']} statement",
                )
            )

    later_groups, reanchored = await rebalance_after(
        conn, account, period_end=end, import_id=import_id
    )
    groups.extend(later_groups)

    existing_as_of = account["statement_balance_as_of"]
    snapshot = BalanceReviewSnapshot(
        account_before=AccountBalanceBefore(
            statement_balance_paise=account["statement_balance_paise"],
            statement_balance_as_of=existing_as_of,
        ),
        statement_updated=(
            closing is not None
            and end is not None
            and (existing_as_of is None or end >= existing_as_of)
        ),
        adjustment_paise=adjustment,
        opening_booked_paise=opening_delta,
        superseded_anchors=len(superseded),
        reanchored_paise=reanchored,
        anchors_repaired=True,
    )
    return BalancePlan(
        closing=closing,
        period_start=start,
        period_end=end,
        closing_adjustment_paise=adjustment,
        groups=groups,
        review=snapshot.to_review_json(),
    )


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


# Public seams for the balance tests; the booking path calls the private helpers above directly.
book_adjustment = _book_adjustment
superseded_anchors = _superseded_anchors


__all__ = [
    "AccountBalanceBefore",
    "BalancePlan",
    "BalanceReviewSnapshot",
    "BalanceWindow",
    "book_adjustment",
    "closing_adjustment_reason",
    "closing_source_reason",
    "line_date_or_none",
    "plan_balances",
    "resolve_balance_window",
    "restore_account_balance",
    "statement_balance_on",
    "statement_opening",
    "stated_balance_conflict",
    "stated_balance_reason",
    "superseded_anchors",
]
