"""Bounded reads over the import history.

`GET /api/import-history` is additive: `GET /api/imports` keeps its existing shape and cap for
every current client, so older statements stay reachable through this keyset route instead.
Ordering is `created_at DESC, id DESC`; the id is the deterministic tie-break.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException

from arno.db.conn import Connection

DEFAULT_LIMIT = 25
MAX_LIMIT = 100

# One attachment per import: multiple uploads for the same statement must not duplicate a row.
_SELECT = """
SELECT
    i.id, i.status, i.period_start, i.period_end, i.check_result, i.counts,
    i.reason, i.created_at, i.committed_at, i.undone_at,
    a.filename, acc.name AS account_name
FROM imports i
LEFT JOIN LATERAL (
    SELECT filename
    FROM attachments
    WHERE import_id = i.id
    ORDER BY created_at DESC, id DESC
    LIMIT 1
) a ON TRUE
LEFT JOIN accounts acc ON acc.id = i.account_id
"""


def _encode_cursor(created_at: datetime, import_id: str) -> str:
    return f"{created_at.isoformat()}|{import_id}"


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        created_at, import_id = cursor.rsplit("|", 1)
        if not import_id:
            raise ValueError
        value = datetime.fromisoformat(created_at)
        if value.tzinfo is None:
            raise ValueError
        return value, import_id
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid import history cursor.") from exc


def _item(row: dict[str, Any]) -> dict[str, Any]:
    check = row["check_result"] or {}
    return {
        "import_id": row["id"],
        "status": row["status"],
        "filename": row["filename"],
        "account": row["account_name"],
        "period": {
            "start": row["period_start"].isoformat() if row["period_start"] else None,
            "end": row["period_end"].isoformat() if row["period_end"] else None,
        }
        if row["period_start"] or row["period_end"]
        else None,
        "check": check.get("status") if check else None,
        "counts": row["counts"],
        "reason": row["reason"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "committed_at": row["committed_at"].isoformat() if row["committed_at"] else None,
        "undone_at": row["undone_at"].isoformat() if row["undone_at"] else None,
    }


async def list_import_history(
    conn: Connection,
    *,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    """One history page, newest first, with an opaque cursor for the next page."""
    limit = max(1, min(limit, MAX_LIMIT))
    where = ""
    params: list[Any] = []
    if cursor:
        created_at, import_id = _decode_cursor(cursor)
        where = "WHERE (i.created_at, i.id) < ($1, $2)"
        params.extend([created_at, import_id])

    rows = await conn.fetch(
        f"""
        {_SELECT}
        {where}
        ORDER BY i.created_at DESC, i.id DESC
        LIMIT ${len(params) + 1}
        """,  # noqa: S608 - `where` is a fixed fragment, never user text
        *params,
        limit + 1,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        _encode_cursor(rows[-1]["created_at"], rows[-1]["id"]) if has_more and rows else None
    )
    return {"items": [_item(row) for row in rows], "next_cursor": next_cursor}


__all__ = ["DEFAULT_LIMIT", "MAX_LIMIT", "list_import_history"]
