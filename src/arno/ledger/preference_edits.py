"""Edit only a saved preference's text, keeping its identity and audit trail intact."""

from __future__ import annotations

from typing import Any

from arno.db.conn import Connection
from arno.ledger.audit import write_group
from arno.ledger.errors import InvalidLedgerError, NotFoundLedgerError

MAX_PREFERENCE_BODY = 2000


async def update_preference_body(conn: Connection, preference_id: str, body: str) -> dict[str, Any]:
    """Rewrite the saved text; created_by, source message, and active stay as they were."""
    text = body.strip()
    if not text:
        raise InvalidLedgerError("A saved preference cannot be empty.")
    if len(text) > MAX_PREFERENCE_BODY:
        raise InvalidLedgerError(
            f"A saved preference must be at most {MAX_PREFERENCE_BODY} characters."
        )
    row = await conn.fetchrow(
        "SELECT * FROM preferences WHERE id = $1 AND deleted_at IS NULL", preference_id
    )
    if row is None:
        raise NotFoundLedgerError("That preference does not exist.")
    async with write_group(conn, actor="user") as group:
        after = await conn.fetchrow(
            "UPDATE preferences SET body = $1, updated_at = now() WHERE id = $2 RETURNING *",
            text,
            preference_id,
        )
        if after is None:
            # Settings can delete a preference for good while this edit runs.
            raise NotFoundLedgerError("That preference does not exist.")
        group.record_updated("preferences", preference_id, row, after)
    return dict(after)


__all__ = ["MAX_PREFERENCE_BODY", "update_preference_body"]
