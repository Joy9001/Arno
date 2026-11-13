"""Commit a verified import: write movements, save the layout, adjust the closing balance.

Nothing is written before the review screen. The parser is saved only after a commit. Commit
groups are recorded, so undoing the import reverses every movement it wrote.

A statement also says what the balance was on its first day, so the commit books that opening
where it belongs, at `period_start`, against Equity. Without it the opening stayed inside a later
balance adjustment and every date the statement covers read short by it. A balance adjustment the
statement's own dates explain is superseded, and a stated balance dated after the statement keeps
its figure.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, cast

from arno.db.conn import Connection
from arno.importers.checks import opening_is_evidenced
from arno.importers.layouts import save_version
from arno.importers.reconcile import (
    credit_decision_required,
    release_suppressed_duplicates,
    rematch_new_lines,
)
from arno.india.formatting import format_paise
from arno.jobs.runner import enqueue_job
from arno.ledger import movements as movements_mod
from arno.ledger.audit import undo_group, write_group
from arno.ledger.common import system_account
from arno.ledger.errors import ConflictLedgerError, InvalidLedgerError
from arno.ledger.payees import is_placeholder_name
from arno.ledger.postings import TransferSpec, create_movement_in_group
from arno.tools.write import resolve_payee

logger = logging.getLogger("arno.importers.commit")


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


async def _movement_date(conn: Connection, movement_id: str) -> date:
    row = await conn.fetchrow("SELECT date FROM movements WHERE id = $1", movement_id)
    if row is None:
        raise InvalidLedgerError("The matched movement disappeared")
    return cast(date, row["date"])  # movements.date is a date column


async def _copy_captures(
    conn: Connection, *, old_movement_id: str, new_movement_id: str
) -> str | None:
    rows = await conn.fetch(
        "SELECT kind, ref, upi_ref, confidence FROM captures WHERE movement_id = $1",
        old_movement_id,
    )
    if not rows:
        return None
    async with write_group(conn, actor="user") as group:
        for row in rows:
            created = await conn.fetchrow_required(
                """
                INSERT INTO captures (movement_id, kind, ref, upi_ref, confidence)
                VALUES ($1, $2, $3, $4, $5) RETURNING *
                """,
                new_movement_id,
                row["kind"],
                row["ref"],
                row["upi_ref"],
                row["confidence"],
            )
            group.record_created("captures", created)
    return group.group_id


async def _capture_statement_line(
    conn: Connection,
    *,
    movement_id: str,
    line: dict[str, Any],
) -> str:
    async with write_group(conn, actor="user") as group:
        row = await conn.fetchrow_required(
            """
            INSERT INTO captures (movement_id, kind, ref, upi_ref, confidence)
            VALUES ($1, 'statement_line', $2, $3, 1.0)
            RETURNING *
            """,
            movement_id,
            line["id"],
            line.get("upi_ref"),
        )
        group.record_created("captures", row)
    return group.group_id


async def _new_movement(
    conn: Connection,
    *,
    line: dict[str, Any],
    account: dict[str, Any],
) -> dict[str, Any]:
    amount = int(line["amount_paise"])
    if amount == 0:
        raise InvalidLedgerError("A statement line with no amount cannot be committed")
    description = line["description_raw"]
    detail = line.get("match_detail") or {}
    upi_id = None
    capture = {
        "kind": "statement_line",
        "ref": line["id"],
        "upi_ref": line.get("upi_ref"),
    }
    payee = None
    payee_groups: list[str] = []
    counterparty = detail.get("counterparty")
    # A narration that names nobody ("N/A", "Unknown") must not invent a merchant.
    if counterparty and not is_placeholder_name(str(counterparty)):
        hashes = detail.get("identifier_hashes") or []
        payee = await resolve_payee(
            conn,
            counterparty,
            kind="merchant",
            upi_id=upi_id,
            match_hash=hashes[0] if hashes else None,
            outside_text=True,
        )
        # A payee this import created is part of the import: undoing it archives the payee too.
        if payee.get("undo_id"):
            payee_groups.append(str(payee["undo_id"]))
    category_id = None

    other_id = detail.get("suggested_transfer_account_id")
    other = None
    if other_id and str(other_id) != str(account["id"]):
        other = await conn.fetchrow("SELECT id FROM accounts WHERE id = $1 AND active", other_id)
    if other is not None:
        if amount < 0:
            from_id, to_id = account["id"], other["id"]
        else:
            from_id, to_id = other["id"], account["id"]
        transfer = await movements_mod.record_transfer(
            conn,
            from_account_id=from_id,
            to_account_id=to_id,
            amount_paise=abs(amount),
            description=description,
            date=line["date"],
            idempotency_key=f"stmt:{line['id']}",
            actor="user",
        )
        capture_group = await _capture_statement_line(
            conn, movement_id=str(transfer["id"]), line=line
        )
        return {
            "id": transfer["id"],
            "undo_id": [*payee_groups, transfer["undo_id"], capture_group],
        }
    credit_kind = detail.get("credit_kind")
    if amount > 0 and account["kind"] == "card":
        if credit_kind not in ("refund", "transfer", "income"):
            raise InvalidLedgerError("Choose what this card credit is before committing")
        if credit_kind == "refund":
            category = detail.get("credit_category_id")
            refund = await movements_mod.record_refund(
                conn,
                account_id=account["id"],
                amount_paise=amount,
                description=description,
                payee_id=payee["id"] if payee else None,
                category_id=category,
                date=line["date"],
                idempotency_key=f"stmt:{line['id']}",
                actor="user",
            )
            capture_group = await _capture_statement_line(
                conn, movement_id=str(refund["id"]), line=line
            )
            return {
                "id": refund["id"],
                "undo_id": [*payee_groups, refund["undo_id"], capture_group],
            }
        if credit_kind == "transfer":
            source_id = detail.get("credit_source_account_id")
            if not source_id:
                raise InvalidLedgerError("Choose the account that paid this card")
            transfer = await movements_mod.record_transfer(
                conn,
                from_account_id=source_id,
                to_account_id=account["id"],
                amount_paise=amount,
                description=description,
                date=line["date"],
                idempotency_key=f"stmt:{line['id']}",
                actor="user",
            )
            capture_group = await _capture_statement_line(
                conn, movement_id=str(transfer["id"]), line=line
            )
            return {
                "id": transfer["id"],
                "undo_id": [*payee_groups, transfer["undo_id"], capture_group],
            }
    if amount < 0:
        record = await movements_mod.record_expense(
            conn,
            account_id=account["id"],
            amount_paise=-amount,
            description=description,
            payee_id=payee["id"] if payee else None,
            category_id=category_id,
            date=line["date"],
            idempotency_key=f"stmt:{line['id']}",
            capture=capture,
            actor="user",
            apply_rules=False,
        )
        return {"id": record["id"], "undo_id": [record["undo_id"], *payee_groups]}
    record = await movements_mod.record_income(
        conn,
        account_id=account["id"],
        amount_paise=amount,
        description=description,
        payee_id=payee["id"] if payee else None,
        date=line["date"],
        idempotency_key=f"stmt:{line['id']}",
        capture=capture,
        actor="user",
    )
    return {"id": record["id"], "undo_id": [record["undo_id"], *payee_groups]}


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
    equity = await system_account(conn, "equity")
    async with write_group(conn, actor="user") as group:
        if difference > 0:
            transfer = TransferSpec(
                from_account_id=equity["id"],
                to_account_id=account["id"],
                amount_paise=difference,
            )
        else:
            transfer = TransferSpec(
                from_account_id=account["id"],
                to_account_id=equity["id"],
                amount_paise=-difference,
            )
        await create_movement_in_group(
            conn,
            group,
            date=day,
            description=f"Balance adjustment for {account['name']} statement",
            kind="adjustment",
            transfers=[transfer],
        )
    return group.group_id, difference


async def commit_import(
    conn: Connection,
    import_id: str,
    *,
    adjustment_confirmed: bool = False,
) -> dict[str, Any]:
    """Book one reviewed import. One transaction: a failure leaves nothing behind.

    Before the transaction, the lines still `new` meet the ledger as it stands now, so a movement
    logged after the review merges instead of being booked twice. A refusal then leaves that new
    match on the review screen. `adjustment_confirmed` is the review screen's yes to a non-zero
    closing difference, which is otherwise refused rather than booked silently.
    """
    rematched = await rematch_new_lines(conn, import_id)
    async with conn.transaction():
        return await _commit(
            conn,
            import_id,
            adjustment_confirmed=adjustment_confirmed,
            rematched=rematched,
        )


async def _commit(
    conn: Connection,
    import_id: str,
    *,
    adjustment_confirmed: bool = False,
    rematched: dict[str, int] | None = None,
) -> dict[str, Any]:
    # The row lock makes a double POST, a retry, and two concurrent commits book once.
    row = await conn.fetchrow("SELECT * FROM imports WHERE id = $1 FOR UPDATE", import_id)
    if row is None:
        raise InvalidLedgerError("No such import")
    if row["status"] == "committed":
        return {"status": "committed", "import_id": import_id, "already": True}
    if row["status"] != "review":
        raise InvalidLedgerError(f"That import is {row['status']}, not ready to commit")
    check = row["check_result"] or {}
    if row["check_status"] != "VERIFIED":
        raise ConflictLedgerError("Only a VERIFIED import can be committed")
    account = (
        await conn.fetchrow(
            "SELECT va.* FROM accounts a JOIN v_accounts va ON va.id = a.id "
            "WHERE a.id = $1 FOR UPDATE OF a",
            row["account_id"],
        )
        if row["account_id"]
        else None
    )
    if account is None:
        raise InvalidLedgerError("The import has no account")
    lines = await conn.fetch(
        "SELECT * FROM statement_lines WHERE import_id = $1 ORDER BY date, id",
        import_id,
    )
    if not opening_is_evidenced(
        check=check,
        check_status=row["check_status"],
        review=row["review"] or {},
        account=account,
        period_start=row["period_start"],
    ):
        raise ConflictLedgerError(
            "Confirm this statement's opening balance on the review screen first.",
            details={"opening_required": True},
        )
    unresolved = [line for line in lines if line["match_state"] == "ambiguous"]
    if unresolved:
        message = "Resolve every ambiguous line on the review screen first."
        appeared = (rematched or {}).get("ambiguous", 0)
        if appeared:
            message = (
                f"A movement logged after this statement reached review matches {appeared} "
                "line(s). Choose the right one on the review screen first."
            )
        raise ConflictLedgerError(
            message, details={"ambiguous": [line["id"] for line in unresolved]}
        )
    unconfirmed = [
        line
        for line in lines
        if line["match_state"] != "ignored"
        and (line["match_detail"] or {}).get("suggested_transfer_account_id")
        and not (line["match_detail"] or {}).get("transfer_confirmed")
    ]
    if unconfirmed:
        raise ConflictLedgerError(
            "Confirm every payment to your own account on the review screen first.",
            details={"unconfirmed_transfers": [line["id"] for line in unconfirmed]},
        )
    unclassified = [line["id"] for line in lines if credit_decision_required(line, account["kind"])]
    if unclassified:
        raise ConflictLedgerError(
            "Choose what each card credit is on the review screen first.",
            details={"unclassified_credits": unclassified},
        )
    invalid_sources: list[str] = []
    for line in lines:
        detail = line["match_detail"] or {}
        if (
            account["kind"] != "card"
            or int(line["amount_paise"]) <= 0
            or line["match_state"] in ("ignored", "certain", "merged")
            or line.get("movement_id")
            or detail.get("credit_kind") != "transfer"
            or detail.get("suggested_transfer_account_id")
        ):
            continue
        source = await conn.fetchrow(
            """
            SELECT 1 FROM accounts
            WHERE id = $1 AND active AND kind IN ('bank', 'cash', 'wallet') AND id <> $2
            """,
            detail.get("credit_source_account_id"),
            account["id"],
        )
        if source is None:
            invalid_sources.append(line["id"])
    if invalid_sources:
        raise ConflictLedgerError(
            "Choose the account that paid each card repayment on the review screen first.",
            details={"invalid_credit_sources": invalid_sources},
        )
    group_ids = [str(group) for group in (row["commit_groups"] or [])]
    period_start = row["period_start"] or min((line["date"] for line in lines), default=None)
    period_end = row["period_end"] or line_date_or_none(lines)
    opening = statement_opening(check)
    conflict = await stated_balance_conflict(
        conn,
        account=account,
        check=check,
        lines=lines,
        period_start=period_start,
        period_end=period_end,
    )
    # The statement is bank evidence for every day it covers, so a stated balance on one of those
    # days is replaced. Never silently: the review says both figures, and this gate needs its yes.
    if conflict is not None and not adjustment_confirmed:
        stated, shown = conflict
        raise ConflictLedgerError(
            stated_balance_reason(stated, account["statement_balance_as_of"], shown),
            details={"stated_balance_paise": stated, "statement_balance_paise": shown},
        )
    committed_lines = 0
    for line in lines:
        if line["match_state"] == "ignored":
            continue
        line_amount = int(line["amount_paise"])
        if line_amount == 0:
            raise InvalidLedgerError(
                "This statement has a line with no amount. Ignore it on the review screen "
                "before committing."
            )
        movement_id = line.get("movement_id")
        if movement_id:
            net = await _net_on_account(conn, movement_id, account["ledger_account_id"])
            # Money out is negative on both sides. A merge that flips the direction would tie a
            # debit to a credit, so the closing adjustment would hide the real difference.
            if net != 0 and (net < 0) != (line_amount < 0):
                raise ConflictLedgerError(
                    "The matched movement moves money the other way. Merge this line with a "
                    "movement in the same direction, or keep it as new.",
                    details={"line": line["id"], "movement_id": movement_id},
                )
            changes: dict[str, Any] = {}
            if await _movement_date(conn, movement_id) != line["date"]:
                changes["date"] = line["date"]
            if net != line_amount:
                changes["amount_paise"] = abs(line_amount)
            if not await _touches_account(conn, movement_id, account["ledger_account_id"]):
                changes["account_id"] = account["id"]
            if changes:
                result = await movements_mod.correct_movement(
                    conn,
                    movement_id,
                    changes=changes,
                    actor="user",
                )
                group_ids.append(str(result["undo_id"]))
                corrected_id = str(result["movement"]["id"])
                if corrected_id != movement_id:
                    copy_group = await _copy_captures(
                        conn, old_movement_id=movement_id, new_movement_id=corrected_id
                    )
                    if copy_group:
                        group_ids.append(copy_group)
                movement_id = corrected_id
            group_ids.append(
                await _capture_statement_line(conn, movement_id=movement_id, line=line)
            )
            await conn.execute(
                """
                UPDATE statement_lines
                SET match_state = 'merged', movement_id = $1, updated_at = now()
                WHERE id = $2
                """,
                movement_id,
                line["id"],
            )
            committed_lines += 1
            continue
        result = await _new_movement(conn, line=line, account=account)
        movement_id = str(result["id"])
        group_ids.extend(str(part) for part in result["undo_id"])

        await conn.execute(
            """
            UPDATE statement_lines
            SET match_state = 'merged', movement_id = $1, updated_at = now()
            WHERE id = $2
            """,
            movement_id,
            line["id"],
        )
        committed_lines += 1

    anchor_groups, opening_booked, superseded = await _reanchor_opening(
        conn,
        account,
        period_start=period_start,
        period_end=period_end,
        opening=opening,
    )
    group_ids.extend(anchor_groups)

    closing = row["closing_balance_paise"]
    adjustment_difference = 0
    if closing is not None and period_end is not None:
        difference = await _closing_difference(
            conn, account=account, closing=int(closing), day=period_end
        )
        # A statement whose closing disagrees with the ledger is a real difference, not a
        # rounding. It is the user's call: the review says the amount, and commit books it only
        # when the review screen carried that confirmation.
        if difference and not adjustment_confirmed:
            raise ConflictLedgerError(
                closing_adjustment_reason(difference, period_end)
                + " Confirm the adjustment first.",
                details={"adjustment_paise": difference, "adjustment_confirmed": False},
            )
        adjustment_group, adjustment_difference = await _adjust_closing(
            conn, account=account, closing=int(closing), day=period_end
        )
        if adjustment_group:
            group_ids.append(adjustment_group)

    reanchored = 0
    anchor_group, reanchored = await _hold_later_anchor(conn, account, period_end=period_end)
    if anchor_group:
        group_ids.append(anchor_group)

    # A backdated statement must not replace a newer stated balance or erase one when the
    # statement prints no closing balance.
    existing_as_of = account["statement_balance_as_of"]
    statement_updated = (
        closing is not None
        and period_end is not None
        and (existing_as_of is None or period_end >= existing_as_of)
    )
    async with conn.transaction():
        if statement_updated:
            await conn.execute(
                """
                UPDATE accounts
                SET statement_balance_paise = $1,
                    statement_balance_as_of = $2,
                    updated_at = now()
                WHERE id = $3
                """,
                closing,
                period_end,
                account["id"],
            )
        layout = None
        fingerprint = row["fingerprint"]
        parser = row["parser"]
        if parser:
            layout = await save_version(
                conn,
                institution=account["institution"] or account["name"],
                kind=account["kind"],
                fingerprint=fingerprint,
                parser=parser,
                source_import_id=import_id,
            )
        review = dict(row["review"] or {})
        review["account_before"] = {
            "statement_balance_paise": account["statement_balance_paise"],
            "statement_balance_as_of": (
                account["statement_balance_as_of"].isoformat()
                if account["statement_balance_as_of"]
                else None
            ),
        }
        review["adjustment_paise"] = adjustment_difference
        review["statement_updated"] = statement_updated
        review["opening_booked_paise"] = opening_booked
        review["superseded_anchors"] = superseded
        review["reanchored_paise"] = reanchored
        # The repair command skips an import whose anchors this commit already placed.
        review["anchors_repaired"] = True
        await conn.execute(
            """
            UPDATE imports
            SET status = 'committed', committed_at = now(), commit_groups = $1,
                review = $2, layout_id = $3
            WHERE id = $4
            """,
            group_ids,
            review,
            layout["id"] if layout else None,
            import_id,
        )
        await enqueue_job(
            conn,
            key=f"baselines:{date.today().isoformat()}:import:{import_id}",
            kind="recompute_baselines",
            payload={"day": date.today().isoformat()},
        )
        await enqueue_job(
            conn,
            key=f"pattern_detection:{date.today().isoformat()}:import:{import_id}",
            kind="pattern_detection",
            payload={"day": date.today().isoformat(), "import_id": import_id},
        )
        await enqueue_job(
            conn,
            key=f"categorize_import:{import_id}",
            kind="categorize_import",
            payload={"import_id": import_id},
        )
    result = {
        "status": "committed",
        "import_id": import_id,
        "rows": committed_lines,
        "adjustment_paise": await _pending_adjustment(conn, import_id),
        "layout_id": layout["id"] if layout else None,
    }
    return result


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


async def review_closing_adjustment(
    conn: Connection,
    *,
    import_id: str,
    account: dict[str, Any],
    check: dict[str, Any],
    period_start: date | None,
    period_end: date | None,
) -> int:
    """The balance adjustment a commit would book at the statement's last day, before it runs.

    The review screen states this amount, so the same arithmetic the commit does: the rows it
    would write (a merge only moves the difference), the opening it would book at the first day,
    and the adjustments the statement's own dates supersede. A forecast is not the booking: the
    commit recomputes the difference and refuses to book one the review did not confirm.
    """
    closing = check.get("closing_paise")
    if not isinstance(closing, int) or isinstance(closing, bool) or period_end is None:
        return 0
    lines = await conn.fetch("SELECT * FROM statement_lines WHERE import_id = $1", import_id)
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
        for row in await _superseded_anchors(
            conn, account["ledger_account_id"], period_start, period_end
        ):
            delta -= int(row["net"])
    ledger = await _account_balance_at(conn, account["ledger_account_id"], period_end)
    return int(closing) - (ledger + delta)


async def _touches_account(conn: Connection, movement_id: str, ledger_account_id: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT 1 FROM movement_legs ml
        JOIN pgledger_transfers t ON t.id = ml.transfer_id
        WHERE ml.movement_id = $1
          AND (t.from_account_id = $2 OR t.to_account_id = $3)
        LIMIT 1
        """,
        movement_id,
        ledger_account_id,
        ledger_account_id,
    )
    return row is not None


def line_date_or_none(lines: list[dict[str, Any]]) -> date | None:
    return max((line["date"] for line in lines), default=None)


async def _pending_adjustment(conn: Connection, import_id: str) -> int | None:
    row = await conn.fetchrow("SELECT review FROM imports WHERE id = $1", import_id)
    return (row["review"] or {}).get("adjustment_paise") if row else None


async def undo_import(conn: Connection, import_id: str) -> dict[str, Any]:
    """Undo a committed import. One transaction: a conflicting group cannot half-revert it."""
    async with conn.transaction():
        return await _undo(conn, import_id)


async def _undo(conn: Connection, import_id: str) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM imports WHERE id = $1 FOR UPDATE", import_id)
    if row is None:
        raise InvalidLedgerError("No such import")
    if row["undone_at"] is not None:
        return {"status": "already_undone", "import_id": import_id}
    if row["status"] != "committed":
        raise InvalidLedgerError("Only a committed import can be undone")
    groups = [str(group) for group in (row["commit_groups"] or [])]
    groups += [str(group) for group in (row["categorize_groups"] or [])]
    # The categorizer runs after the commit, so its groups are listed after the commit groups and
    # reversing the list reverts them first. Its audit rows are the later writes a commit group's
    # undo would call a conflict, and its labels and `llm` rules were derived from this import: an
    # un-imported payee must not stay labelled by a rule it wrote.
    undoing: set[str] = set()
    for group_id in reversed(groups):
        try:
            result = await undo_group(
                conn, group_id, actor="user", ignore_groups=frozenset(undoing)
            )
        except ConflictLedgerError:
            logger.warning("import %s undo conflict in group %s", import_id, group_id)
            raise
        # This undo's own audit rows touch the same rows; the next step must not call them later
        # writes when it reverts the movement and labels the commit group created.
        if result.get("undo_undo_id"):
            undoing.add(str(result["undo_undo_id"]))
    review = dict(row["review"] or {})
    before = review.get("account_before") or {}
    statement_updated = review.get("statement_updated", True)
    async with conn.transaction():
        current = await conn.fetchrow(
            """
            SELECT statement_balance_paise, statement_balance_as_of
            FROM accounts WHERE id = $1
            """,
            row["account_id"],
        )
        current_as_of = current["statement_balance_as_of"] if current else None
        # Restore only while this import is still the effective balance source. A newer
        # statement, or a manual balance on the same date, must survive the undo.
        is_effective = (
            current is not None
            and (
                row["period_end"] is None
                or current_as_of is None
                or current_as_of <= row["period_end"]
            )
            and (
                row["period_end"] is None
                or current_as_of != row["period_end"]
                or row["closing_balance_paise"] is None
                or current["statement_balance_paise"] == row["closing_balance_paise"]
            )
        )
        if before and statement_updated and is_effective:
            await conn.execute(
                """
                UPDATE accounts
                SET statement_balance_paise = $1, statement_balance_as_of = $2, updated_at = now()
                WHERE id = $3
                """,
                before.get("statement_balance_paise"),
                date.fromisoformat(before["statement_balance_as_of"])
                if before.get("statement_balance_as_of")
                else None,
                row["account_id"],
            )
        await conn.execute(
            """
            UPDATE statement_lines
            SET match_state = 'ignored', movement_id = NULL, updated_at = now()
            WHERE import_id = $1
            """,
            import_id,
        )
        await conn.execute(
            """
            UPDATE imports
            SET status = 'rejected', undone_at = now(), rejected_at = now(),
                reason = 'Undone from the Imports screen'
            WHERE id = $1
            """,
            import_id,
        )
        # This import's rows no longer count as duplicates, so a waiting statement keeps its own.
        await release_suppressed_duplicates(conn, account_id=row["account_id"])

        await enqueue_job(
            conn,
            key=f"baselines:{date.today().isoformat()}:undo:{import_id}",
            kind="recompute_baselines",
            payload={"day": date.today().isoformat()},
        )
    return {"status": "undone", "import_id": import_id}


async def reject_import(conn: Connection, import_id: str, reason: str | None = None) -> None:
    async with conn.transaction():
        row = await conn.fetchrow(
            """
            UPDATE imports
            SET status = 'rejected', rejected_at = now(), reason = $1
            WHERE id = $2 AND status NOT IN ('committed', 'rejected')
            RETURNING account_id
            """,
            reason,
            import_id,
        )
        # Rows another statement suppressed as duplicates of this one belong to them again.
        if row is not None:
            await release_suppressed_duplicates(conn, account_id=row["account_id"])


__all__ = [
    "closing_adjustment_reason",
    "closing_source_reason",
    "commit_import",
    "reject_import",
    "review_closing_adjustment",
    "stated_balance_conflict",
    "stated_balance_reason",
    "undo_import",
]
