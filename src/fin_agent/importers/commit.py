"""Commit a verified import: write movements, save the layout, adjust the closing balance.

Nothing is written before the review screen. The parser is saved only after a commit. Commit
groups are recorded, so undoing the import reverses every movement it wrote.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fin_agent.db.conn import Connection
from fin_agent.importers.checks import opening_is_evidenced
from fin_agent.importers.layouts import save_layout
from fin_agent.importers.reconcile import credit_decision_required
from fin_agent.jobs.runner import enqueue_job
from fin_agent.ledger import movements as movements_mod
from fin_agent.ledger.audit import undo_group, write_group
from fin_agent.ledger.common import get_account_row, system_account
from fin_agent.ledger.errors import ConflictLedgerError, InvalidLedgerError
from fin_agent.ledger.postings import TransferSpec, create_movement_in_group
from fin_agent.tools.write import resolve_payee

logger = logging.getLogger("fin_agent.importers.commit")


async def _net_on_account(conn: Connection, movement_id: str, ledger_account_id: str) -> int:
    row = await conn.fetchrow(
        """
        SELECT coalesce(sum(
            CASE WHEN t.from_account_id = $1 THEN -t.amount
                 WHEN t.to_account_id = $2 THEN t.amount
                 ELSE 0 END
        ), 0)::bigint AS net
        FROM pgledger_transfers t
        WHERE t.metadata ->> 'movement_id' = $3
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
    return row["date"]


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
            created = await conn.fetchrow(
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
        row = await conn.fetchrow(
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
    categorize: bool,
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
    counterparty = detail.get("counterparty")
    if counterparty:
        hashes = detail.get("identifier_hashes") or []
        payee = await resolve_payee(
            conn,
            counterparty,
            kind="merchant",
            upi_id=upi_id,
            match_hash=hashes[0] if hashes else None,
            outside_text=True,
        )
    suggested = detail.get("suggested_category") or {}
    category_id = suggested.get("category_id") if categorize else None

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
        return {"id": transfer["id"], "undo_id": [transfer["undo_id"], capture_group]}
    credit_kind = detail.get("credit_kind")
    if amount > 0 and account["kind"] == "card":
        if credit_kind not in ("refund", "transfer", "income"):
            raise InvalidLedgerError("Choose what this card credit is before committing")
        if credit_kind == "refund":
            category = detail.get("credit_category_id") or (suggested or {}).get("category_id")
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
            return {"id": refund["id"], "undo_id": [refund["undo_id"], capture_group]}
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
            return {"id": transfer["id"], "undo_id": [transfer["undo_id"], capture_group]}
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
        )
        return {"id": record["id"], "undo_id": [record["undo_id"]]}
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
    return {"id": record["id"], "undo_id": [record["undo_id"]]}


async def _account_balance_at(conn: Connection, ledger_account_id: str, day: date) -> int:
    row = await conn.fetchrow(
        """
        SELECT coalesce(sum(e.amount), 0)::bigint AS balance
        FROM pgledger_entries e
        JOIN pgledger_transfers t ON t.id = e.transfer_id
        LEFT JOIN movements m ON m.id = t.metadata ->> 'movement_id'
        WHERE e.account_id = $1
          AND coalesce(m.date, (t.event_at AT TIME ZONE 'Asia/Kolkata')::date) <= $2
        """,
        ledger_account_id,
        day,
    )
    return int(row["balance"]) if row else 0


async def _adjust_closing(
    conn: Connection,
    *,
    account: dict[str, Any],
    closing: int,
    day: date,
) -> tuple[str | None, int]:
    ledger = await _account_balance_at(conn, account["ledger_account_id"], day)
    difference = int(closing) - ledger
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
    categorize: bool = True,
) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM imports WHERE id = $1", import_id)
    if row is None:
        raise InvalidLedgerError("No such import")
    if row["status"] == "committed":
        return {"status": "committed", "import_id": import_id, "already": True}
    if row["status"] != "review":
        raise InvalidLedgerError(f"That import is {row['status']}, not ready to commit")
    check = row["check_result"] or {}
    if check.get("status") != "VERIFIED":
        raise ConflictLedgerError("Only a VERIFIED import can be committed")
    account = await get_account_row(conn, row["account_id"]) if row["account_id"] else None
    if account is None:
        raise InvalidLedgerError("The import has no account")
    lines = await conn.fetch(
        "SELECT * FROM statement_lines WHERE import_id = $1 ORDER BY date, id",
        import_id,
    )
    if not opening_is_evidenced(
        check=check,
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
        raise ConflictLedgerError(
            "Resolve every ambiguous line on the review screen first.",
            details={"ambiguous": [line["id"] for line in unresolved]},
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
        movement_id = line.get("movement_id")
        if movement_id:
            net = await _net_on_account(conn, movement_id, account["ledger_account_id"])
            amount = abs(int(line["amount_paise"]))
            changes: dict[str, Any] = {}
            if await _movement_date(conn, movement_id) != line["date"]:
                changes["date"] = line["date"]
            if abs(net) != amount:
                changes["amount_paise"] = amount
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
        result = await _new_movement(conn, line=line, account=account, categorize=categorize)
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

    closing = row["closing_balance_paise"]
    period_end = row["period_end"] or line_date_or_none(lines)
    adjustment_difference = 0
    if closing is not None and period_end is not None:
        adjustment_group, adjustment_difference = await _adjust_closing(
            conn, account=account, closing=int(closing), day=period_end
        )
        if adjustment_group:
            group_ids.append(adjustment_group)

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
        if fingerprint and parser:
            layout = await save_layout(
                conn,
                institution=row["institution"] or account["institution"] or account["name"],
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
        await conn.execute(
            """
            UPDATE attachments
            SET data = NULL, purged_at = now()
            WHERE import_id = $1 AND data IS NOT NULL
            """,
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
    result = {
        "status": "committed",
        "import_id": import_id,
        "rows": committed_lines,
        "adjustment_paise": await _pending_adjustment(conn, import_id),
        "layout_id": layout["id"] if layout else None,
    }
    return result


async def _touches_account(conn: Connection, movement_id: str, ledger_account_id: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT 1 FROM pgledger_transfers t
        WHERE t.metadata ->> 'movement_id' = $1
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
    row = await conn.fetchrow("SELECT * FROM imports WHERE id = $1", import_id)
    if row is None:
        raise InvalidLedgerError("No such import")
    if row["status"] != "committed":
        raise InvalidLedgerError("Only a committed import can be undone")
    if row["undone_at"] is not None:
        return {"status": "already_undone", "import_id": import_id}
    for group_id in reversed([str(group) for group in (row["commit_groups"] or [])]):
        try:
            await undo_group(conn, group_id, actor="user")
        except ConflictLedgerError:
            logger.warning("import %s undo conflict in group %s", import_id, group_id)
            raise
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
        await enqueue_job(
            conn,
            key=f"baselines:{date.today().isoformat()}:undo:{import_id}",
            kind="recompute_baselines",
            payload={"day": date.today().isoformat()},
        )
    return {"status": "undone", "import_id": import_id}


async def reject_import(conn: Connection, import_id: str, reason: str | None = None) -> None:
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE imports
            SET status = 'rejected', rejected_at = now(), reason = $1
            WHERE id = $2 AND status IN ('review', 'needs_input', 'needs_password', 'failed',
                                         'not_a_statement')
            """,
            reason,
            import_id,
        )
        await conn.execute(
            """
            UPDATE attachments
            SET data = NULL, purged_at = now()
            WHERE import_id = $1 AND data IS NOT NULL
            """,
            import_id,
        )


__all__ = ["commit_import", "reject_import", "undo_import"]
