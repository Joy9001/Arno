"""Build a reviewed import from synthetic statement rows."""

from __future__ import annotations

from datetime import date
from typing import Any

from arno.db.conn import Connection
from arno.importers.reconcile import insert_lines, reconcile
from arno.ledger.audit import new_id


async def review_statement(
    conn: Connection,
    account: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    start: date,
    end: date,
    opening: int | None = None,
    closing: int | None = None,
    check: dict[str, Any],
    review: dict[str, Any] | None = None,
) -> str:
    import_id = await new_id(conn, "imp")
    check_status = check.get("status", "VERIFIED")
    await conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             opening_balance_paise, closing_balance_paise, check_result,
                             check_status, review, commit_groups)
        VALUES ($1, $2, 'review', $3, $4, $5, $6, $7, $8, $9, $10, '[]'::jsonb)
        """,
        import_id,
        f"hash-{import_id}",
        account["id"],
        start,
        end,
        opening,
        closing,
        {key: value for key, value in check.items() if key != "status"},
        check_status,
        review or {},
    )
    fresh = await conn.fetchrow_required("SELECT * FROM accounts WHERE id = $1", account["id"])
    await insert_lines(conn, import_id=import_id, account=fresh, parsed_account={"rows": rows})
    await reconcile(conn, import_id=import_id, account=fresh, period=(start, end))
    return import_id
