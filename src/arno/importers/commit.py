"""Commit a verified import: write movements and adjust the closing balance.

Nothing is written before the review screen. Commit
groups are recorded, so undoing the import deletes every movement it wrote.

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
from arno.importers.balance_plan import plan_balances, restore_account_balance
from arno.importers.baselines import handle_recompute_baselines
from arno.importers.categorize import handle_categorize_import
from arno.importers.checks import opening_is_evidenced
from arno.importers.reconcile import (
    credit_decision_required,
    release_suppressed_duplicates,
    rematch_new_lines,
)
from arno.jobs.background import spawn
from arno.jobs.patterns import handle_pattern_detection
from arno.ledger import movements as movements_mod
from arno.ledger.audit import undo_group, write_group
from arno.ledger.errors import ConflictLedgerError, InvalidLedgerError
from arno.ledger.payees import is_placeholder_name
from arno.tools.write import resolve_payee

logger = logging.getLogger("arno.importers.commit")


async def _net_on_account(conn: Connection, movement_id: str, account_id: str) -> int:
    row = await conn.fetchrow(
        """
        SELECT coalesce(sum(
            CASE WHEN from_account_id = $1 THEN -amount_paise
                 WHEN to_account_id = $1 THEN amount_paise
                 ELSE 0 END
        ), 0)::bigint AS net
        FROM movement_legs
        WHERE movement_id = $2
        """,
        account_id,
        movement_id,
    )
    return int(row["net"]) if row else 0


async def _movement_date(conn: Connection, movement_id: str) -> date:
    row = await conn.fetchrow("SELECT date FROM movements WHERE id = $1", movement_id)
    if row is None:
        raise InvalidLedgerError("The matched movement disappeared")
    return cast(date, row["date"])  # movements.date is a date column


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
        result = await _commit(
            conn,
            import_id,
            adjustment_confirmed=adjustment_confirmed,
            rematched=rematched,
        )
    if "already" not in result:
        spawn(handle_categorize_import(import_id), name=f"categorize-{import_id}")
        spawn(handle_recompute_baselines(), name=f"baselines-{import_id}")
        spawn(handle_pattern_detection(), name=f"patterns-{import_id}")
    return result


async def lock_account_and_import(conn: Connection, import_id: str) -> dict[str, Any] | None:
    """Lock the account, then the import row: the gate commit, undo, and repair all share.

    The account's row lock is the gate, so two operations on one account wait for each other
    rather than for a row each of them holds. The import's own row lock makes a double POST, a
    retry, and two concurrent commits book once.
    """
    account_id = await conn.fetchval("SELECT account_id FROM imports WHERE id = $1", import_id)
    if account_id is not None:
        await conn.fetchval("SELECT id FROM accounts WHERE id = $1 FOR UPDATE", account_id)
    row = await conn.fetchrow("SELECT * FROM imports WHERE id = $1 FOR UPDATE", import_id)
    if row is None:
        return None
    if row["account_id"] is not None and row["account_id"] != account_id:
        # An extraction re-bound the import while this waited for its row: take that gate too.
        await conn.fetchval("SELECT id FROM accounts WHERE id = $1 FOR UPDATE", row["account_id"])
    return row


async def _lock_import_scope(conn: Connection, import_id: str) -> dict[str, Any] | None:
    """Take the account, the import, and the payees it names, in an order both paths share.

    Commit writes the statement's movements and resolves the payees they name; undo deletes
    them, archives the payees it created, and restores the account last. Taking those rows in
    each path's own order made a commit and an undo of two imports on one account hold a row the
    other needed, so Postgres broke a deadlock and picked a side. The account's row lock is the
    gate: the import's own row and its payees come under it, payees in id order, so two
    operations on one account wait for each other rather than for a row each of them holds.
    """
    row = await lock_account_and_import(conn, import_id)
    if row is None:
        return None
    if row["account_id"] is not None:
        await conn.fetch(
            """
            SELECT id FROM payees
            WHERE id IN (
                SELECT ml.payee_id
                FROM statement_lines sl
                JOIN movement_legs ml ON ml.movement_id = sl.movement_id
                WHERE sl.import_id = $1 AND ml.payee_id IS NOT NULL
            ) OR lower(display_name) IN (
                SELECT lower(sl.match_detail ->> 'counterparty')
                FROM statement_lines sl
                WHERE sl.import_id = $1 AND sl.movement_id IS NULL
                  AND sl.match_state <> 'ignored'
            )
            ORDER BY id
            FOR UPDATE
            """,
            import_id,
        )
    return row


async def _commit(
    conn: Connection,
    import_id: str,
    *,
    adjustment_confirmed: bool = False,
    rematched: dict[str, int] | None = None,
) -> dict[str, Any]:
    row = await _lock_import_scope(conn, import_id)
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
            net = await _net_on_account(conn, movement_id, account["id"])
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
            if not await _touches_account(conn, movement_id, account["id"]):
                changes["account_id"] = account["id"]
            if changes:
                result = await movements_mod.correct_movement(
                    conn,
                    movement_id,
                    changes=changes,
                    actor="user",
                )
                group_ids.append(str(result["undo_id"]))
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

    booked = await plan_balances(
        conn,
        account=account,
        row=row,
        lines=lines,
        book=True,
        adjustment_confirmed=adjustment_confirmed,
    )
    balance_review = booked.review
    closing = booked.closing
    period_end = booked.period_end
    group_ids.extend(booked.groups)

    async with conn.transaction():
        if balance_review["statement_updated"]:
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
        review = {**(row["review"] or {}), **balance_review}
        await conn.execute(
            """
            UPDATE imports
            SET status = 'committed', committed_at = now(), commit_groups = $1,
                review = $2
            WHERE id = $3
            """,
            group_ids,
            review,
            import_id,
        )
        await conn.execute(
            "UPDATE imports SET categorize = $1 WHERE id = $2",
            {"state": "queued"},
            import_id,
        )
    result = {
        "status": "committed",
        "import_id": import_id,
        "rows": committed_lines,
        "adjustment_paise": await _pending_adjustment(conn, import_id),
    }
    return result


async def _touches_account(conn: Connection, movement_id: str, account_id: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT 1 FROM movement_legs
        WHERE movement_id = $1 AND (from_account_id = $2 OR to_account_id = $2)
        LIMIT 1
        """,
        movement_id,
        account_id,
    )
    return row is not None


async def _pending_adjustment(conn: Connection, import_id: str) -> int | None:
    row = await conn.fetchrow("SELECT review FROM imports WHERE id = $1", import_id)
    return (row["review"] or {}).get("adjustment_paise") if row else None


async def undo_import(conn: Connection, import_id: str) -> dict[str, Any]:
    """Undo a committed import. One transaction: a conflicting group cannot half-revert it."""
    async with conn.transaction():
        result = await _undo(conn, import_id)
    spawn(handle_recompute_baselines(), name=f"baselines-{import_id}")
    return result


async def _undo(conn: Connection, import_id: str) -> dict[str, Any]:
    row = await _lock_import_scope(conn, import_id)
    if row is None:
        raise InvalidLedgerError("No such import")
    if row["undone_at"] is not None:
        return {"status": "already_undone", "import_id": import_id}
    if row["status"] != "committed":
        raise InvalidLedgerError("Only a committed import can be undone")
    groups = [str(group) for group in (row["commit_groups"] or [])]
    groups += [str(group) for group in (row["categorize_groups"] or [])]
    # The categorizer runs after the commit, so its groups are listed after the commit groups and
    # reversing the list reverts them first. Its undo log rows are the later writes a commit group's
    # undo would call a conflict, and its labels and `llm` rules were derived from this import: an
    # un-imported payee must not stay labelled by a rule it wrote.
    # The lines point at the movements the groups delete, and lines are not recorded in a group.
    await conn.execute(
        """
        UPDATE statement_lines
        SET match_state = 'ignored', movement_id = NULL, updated_at = now()
        WHERE import_id = $1
        """,
        import_id,
    )
    undoing: set[str] = set()
    for group_id in reversed(groups):
        try:
            result = await undo_group(
                conn, group_id, actor="user", ignore_groups=frozenset(undoing)
            )
        except ConflictLedgerError:
            logger.warning("import %s undo conflict in group %s", import_id, group_id)
            raise
        # This undo's own undo log rows touch the same rows; the next step must not call them later
        # writes when it reverts the movement and labels the commit group created.
        if result.get("undo_undo_id"):
            undoing.add(str(result["undo_undo_id"]))
    async with conn.transaction():
        await restore_account_balance(
            conn,
            account_id=row["account_id"],
            review=row["review"],
            period_end=row["period_end"],
            closing=row["closing_balance_paise"],
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
    "commit_import",
    "lock_account_and_import",
    "reject_import",
    "undo_import",
]
