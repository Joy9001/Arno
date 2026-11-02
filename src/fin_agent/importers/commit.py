"""Commit a verified import: write movements, save the layout, adjust the closing balance.

Nothing is written before the review screen. The parser is saved only after a commit. Commit
groups are recorded, so undoing the import reverses every movement it wrote.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from fin_agent.importers.categorizer import category_for_merchant, extract_merchant
from fin_agent.importers.layouts import save_layout
from fin_agent.importers.pipeline import _finish_open_transaction
from fin_agent.jobs.runner import enqueue_job
from fin_agent.ledger import movements as movements_mod
from fin_agent.ledger.audit import undo_group, write_group
from fin_agent.ledger.common import get_account_row, system_account
from fin_agent.ledger.errors import ConflictLedgerError, InvalidLedgerError
from fin_agent.ledger.postings import TransferSpec, create_movement_in_group
from fin_agent.tools.write import resolve_payee

logger = logging.getLogger("fin_agent.importers.commit")


def _net_on_account(conn: psycopg.Connection, movement_id: str, ledger_account_id: str) -> int:
    row = conn.execute(
        """
        SELECT coalesce(sum(
            CASE WHEN t.from_account_id = %s THEN -t.amount
                 WHEN t.to_account_id = %s THEN t.amount
                 ELSE 0 END
        ), 0)::bigint AS net
        FROM pgledger_transfers t
        WHERE t.metadata ->> 'movement_id' = %s
        """,
        (ledger_account_id, ledger_account_id, movement_id),
    ).fetchone()
    return int(row["net"]) if row else 0


def _movement_date(conn: psycopg.Connection, movement_id: str) -> date:
    row = conn.execute("SELECT date FROM movements WHERE id = %s", (movement_id,)).fetchone()
    if row is None:
        raise InvalidLedgerError("The matched movement disappeared")
    return row["date"]


def _copy_captures(
    conn: psycopg.Connection, *, old_movement_id: str, new_movement_id: str
) -> str | None:
    rows = conn.execute(
        "SELECT kind, ref, upi_ref, confidence FROM captures WHERE movement_id = %s",
        (old_movement_id,),
    ).fetchall()
    if not rows:
        return None
    with write_group(conn, actor="user") as group:
        for row in rows:
            created = conn.execute(
                """
                INSERT INTO captures (movement_id, kind, ref, upi_ref, confidence)
                VALUES (%s, %s, %s, %s, %s) RETURNING *
                """,
                (
                    new_movement_id,
                    row["kind"],
                    row["ref"],
                    row["upi_ref"],
                    row["confidence"],
                ),
            ).fetchone()
            group.record_created("captures", created)
    return group.group_id


def _capture_statement_line(
    conn: psycopg.Connection,
    *,
    movement_id: str,
    line: dict[str, Any],
) -> str:
    with write_group(conn, actor="user") as group:
        row = conn.execute(
            """
            INSERT INTO captures (movement_id, kind, ref, upi_ref, confidence)
            VALUES (%s, 'statement_line', %s, %s, 1.0)
            RETURNING *
            """,
            (movement_id, line["id"], line.get("upi_ref")),
        ).fetchone()
        group.record_created("captures", row)
    return group.group_id


def _new_movement(
    conn: psycopg.Connection,
    *,
    line: dict[str, Any],
    account: dict[str, Any],
    categorize: bool,
) -> dict[str, Any]:
    amount = int(line["amount_paise"])
    if amount == 0:
        raise InvalidLedgerError("A statement line with no amount cannot be committed")
    description = line["description_raw"]
    merchant = extract_merchant(description)
    upi_id = None
    capture = {
        "kind": "statement_line",
        "ref": line["id"],
        "upi_ref": line.get("upi_ref"),
    }
    payee = None
    if merchant:
        hashes = (line.get("match_detail") or {}).get("identifier_hashes") or []
        payee = resolve_payee(
            conn,
            merchant,
            kind="merchant",
            upi_id=upi_id,
            match_hash=hashes[0] if hashes else None,
            outside_text=True,
        )
    category = category_for_merchant(conn, merchant) if categorize else None
    category_id = category["category_id"] if category else None
    if amount < 0:
        return movements_mod.record_expense(
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
    return movements_mod.record_income(
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


def _account_balance_at(conn: psycopg.Connection, ledger_account_id: str, day: date) -> int:
    row = conn.execute(
        """
        SELECT coalesce(sum(e.amount), 0)::bigint AS balance
        FROM pgledger_entries e
        JOIN pgledger_transfers t ON t.id = e.transfer_id
        LEFT JOIN movements m ON m.id = t.metadata ->> 'movement_id'
        WHERE e.account_id = %s
          AND coalesce(m.date, (t.event_at AT TIME ZONE 'Asia/Kolkata')::date) <= %s
        """,
        (ledger_account_id, day),
    ).fetchone()
    return int(row["balance"]) if row else 0


def _adjust_closing(
    conn: psycopg.Connection,
    *,
    account: dict[str, Any],
    closing: int,
    day: date,
) -> tuple[str | None, int]:
    ledger = _account_balance_at(conn, account["ledger_account_id"], day)
    difference = int(closing) - ledger
    if difference == 0:
        return None, 0
    equity = system_account(conn, "equity")
    with write_group(conn, actor="user") as group:
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
        create_movement_in_group(
            conn,
            group,
            date=day,
            description=f"Balance adjustment for {account['name']} statement",
            kind="adjustment",
            transfers=[transfer],
        )
    return group.group_id, difference


def commit_import(
    conn: psycopg.Connection,
    import_id: str,
    *,
    categorize: bool = True,
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM imports WHERE id = %s", (import_id,)).fetchone()
    if row is None:
        raise InvalidLedgerError("No such import")
    if row["status"] == "committed":
        return {"status": "committed", "import_id": import_id, "already": True}
    if row["status"] != "review":
        raise InvalidLedgerError(f"That import is {row['status']}, not ready to commit")
    check = row["check_result"] or {}
    if check.get("status") != "VERIFIED":
        raise ConflictLedgerError("Only a VERIFIED import can be committed")
    account = get_account_row(conn, row["account_id"]) if row["account_id"] else None
    if account is None:
        raise InvalidLedgerError("The import has no account")
    lines = [
        dict(line)
        for line in conn.execute(
            "SELECT * FROM statement_lines WHERE import_id = %s ORDER BY date, id",
            (import_id,),
        ).fetchall()
    ]
    unresolved = [line for line in lines if line["match_state"] == "ambiguous"]
    if unresolved:
        raise ConflictLedgerError(
            "Resolve every ambiguous line on the review screen first.",
            details={"ambiguous": [line["id"] for line in unresolved]},
        )
    group_ids = [str(group) for group in (row["commit_groups"] or [])]
    committed_lines = 0
    for line in lines:
        if line["match_state"] == "ignored":
            continue
        movement_id = line.get("movement_id")
        if movement_id:
            net = _net_on_account(conn, movement_id, account["ledger_account_id"])
            amount = abs(int(line["amount_paise"]))
            changes: dict[str, Any] = {}
            if _movement_date(conn, movement_id) != line["date"]:
                changes["date"] = line["date"]
            if abs(net) != amount:
                changes["amount_paise"] = amount
            if not _touches_account(conn, movement_id, account["ledger_account_id"]):
                changes["account_id"] = account["id"]
            if changes:
                result = movements_mod.correct_movement(
                    conn,
                    movement_id,
                    changes=changes,
                    actor="user",
                )
                group_ids.append(str(result["undo_id"]))
                corrected_id = str(result["movement"]["id"])
                copy_group = _copy_captures(
                    conn, old_movement_id=movement_id, new_movement_id=corrected_id
                )
                if copy_group:
                    group_ids.append(copy_group)
                movement_id = corrected_id
            group_ids.append(_capture_statement_line(conn, movement_id=movement_id, line=line))
            conn.execute(
                """
                UPDATE statement_lines
                SET match_state = 'merged', movement_id = %s, updated_at = now()
                WHERE id = %s
                """,
                (movement_id, line["id"]),
            )
            conn.commit()
            committed_lines += 1
            continue
        result = _new_movement(conn, line=line, account=account, categorize=categorize)
        movement_id = str(result["id"])
        group_ids.append(str(result["undo_id"]))
        conn.execute(
            """
            UPDATE statement_lines
            SET match_state = 'merged', movement_id = %s, updated_at = now()
            WHERE id = %s
            """,
            (movement_id, line["id"]),
        )
        conn.commit()
        committed_lines += 1

    closing = row["closing_balance_paise"]
    period_end = row["period_end"] or line_date_or_none(lines)
    adjustment_difference = 0
    if closing is not None and period_end is not None:
        adjustment_group, adjustment_difference = _adjust_closing(
            conn, account=account, closing=int(closing), day=period_end
        )
        if adjustment_group:
            group_ids.append(adjustment_group)
        conn.commit()

    _finish_open_transaction(conn)
    with conn.transaction():
        conn.execute(
            """
            UPDATE accounts
            SET statement_balance_paise = %s,
                statement_balance_as_of = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (closing, period_end, account["id"]),
        )
        layout = None
        fingerprint = row["fingerprint"]
        parser = row["parser"]
        if fingerprint and parser:
            layout = save_layout(
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
        conn.execute(
            """
            UPDATE imports
            SET status = 'committed', committed_at = now(), commit_groups = %s,
                review = %s, layout_id = %s
            WHERE id = %s
            """,
            (Jsonb(group_ids), Jsonb(review), layout["id"] if layout else None, import_id),
        )
        conn.execute(
            """
            UPDATE attachments
            SET data = NULL, purged_at = now()
            WHERE import_id = %s AND data IS NOT NULL
            """,
            (import_id,),
        )
        enqueue_job(
            conn,
            key=f"baselines:{date.today().isoformat()}:import:{import_id}",
            kind="recompute_baselines",
            payload={"day": date.today().isoformat()},
        )
    result = {
        "status": "committed",
        "import_id": import_id,
        "rows": committed_lines,
        "adjustment_paise": _pending_adjustment(conn, import_id),
        "layout_id": layout["id"] if layout else None,
    }
    conn.commit()
    return result


def _touches_account(conn: psycopg.Connection, movement_id: str, ledger_account_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM pgledger_transfers t
        WHERE t.metadata ->> 'movement_id' = %s
          AND (t.from_account_id = %s OR t.to_account_id = %s)
        LIMIT 1
        """,
        (movement_id, ledger_account_id, ledger_account_id),
    ).fetchone()
    return row is not None


def line_date_or_none(lines: list[dict[str, Any]]) -> date | None:
    return max((line["date"] for line in lines), default=None)


def _pending_adjustment(conn: psycopg.Connection, import_id: str) -> int | None:
    row = conn.execute("SELECT review FROM imports WHERE id = %s", (import_id,)).fetchone()
    return (row["review"] or {}).get("adjustment_paise") if row else None


def undo_import(conn: psycopg.Connection, import_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM imports WHERE id = %s", (import_id,)).fetchone()
    if row is None:
        raise InvalidLedgerError("No such import")
    if row["status"] != "committed":
        raise InvalidLedgerError("Only a committed import can be undone")
    if row["undone_at"] is not None:
        return {"status": "already_undone", "import_id": import_id}
    for group_id in reversed([str(group) for group in (row["commit_groups"] or [])]):
        try:
            undo_group(conn, group_id, actor="user")
        except ConflictLedgerError:
            logger.warning("import %s undo conflict in group %s", import_id, group_id)
            raise
    review = dict(row["review"] or {})
    before = review.get("account_before") or {}
    _finish_open_transaction(conn)
    with conn.transaction():
        if before:
            conn.execute(
                """
                UPDATE accounts
                SET statement_balance_paise = %s, statement_balance_as_of = %s, updated_at = now()
                WHERE id = %s
                """,
                (
                    before.get("statement_balance_paise"),
                    date.fromisoformat(before["statement_balance_as_of"])
                    if before.get("statement_balance_as_of")
                    else None,
                    row["account_id"],
                ),
            )
        conn.execute(
            """
            UPDATE statement_lines
            SET match_state = 'ignored', movement_id = NULL, updated_at = now()
            WHERE import_id = %s
            """,
            (import_id,),
        )
        conn.execute(
            """
            UPDATE imports
            SET status = 'rejected', undone_at = now(), rejected_at = now(),
                reason = 'Undone from the Imports screen'
            WHERE id = %s
            """,
            (import_id,),
        )
        enqueue_job(
            conn,
            key=f"baselines:{date.today().isoformat()}:undo:{import_id}",
            kind="recompute_baselines",
            payload={"day": date.today().isoformat()},
        )
    return {"status": "undone", "import_id": import_id}


def reject_import(conn: psycopg.Connection, import_id: str, reason: str | None = None) -> None:
    conn.execute(
        """
        UPDATE imports
        SET status = 'rejected', rejected_at = now(), reason = %s
        WHERE id = %s AND status IN ('review', 'needs_input', 'needs_password', 'failed',
                                     'not_a_statement')
        """,
        (reason, import_id),
    )
    conn.execute(
        """
        UPDATE attachments
        SET data = NULL, purged_at = now()
        WHERE import_id = %s AND data IS NOT NULL
        """,
        (import_id,),
    )
    conn.commit()


__all__ = ["commit_import", "reject_import", "undo_import"]
