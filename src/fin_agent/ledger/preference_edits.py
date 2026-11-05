"""Edit only a saved preference's text, keeping its identity and audit trail intact."""

from __future__ import annotations

from typing import Any

import psycopg

from fin_agent.ledger.audit import write_group
from fin_agent.ledger.errors import InvalidLedgerError, NotFoundLedgerError

MAX_PREFERENCE_BODY = 2000


def update_preference_body(
    conn: psycopg.Connection, preference_id: str, body: str
) -> dict[str, Any]:
    """Rewrite the saved text; created_by, source message, and active stay as they were."""
    text = body.strip()
    if not text:
        raise InvalidLedgerError("A saved preference cannot be empty.")
    if len(text) > MAX_PREFERENCE_BODY:
        raise InvalidLedgerError(
            f"A saved preference must be at most {MAX_PREFERENCE_BODY} characters."
        )
    row = conn.execute(
        "SELECT * FROM preferences WHERE id = %s AND deleted_at IS NULL", (preference_id,)
    ).fetchone()
    if row is None:
        raise NotFoundLedgerError("That preference does not exist.")
    with write_group(conn, actor="user") as group:
        after = conn.execute(
            "UPDATE preferences SET body = %s, updated_at = now() WHERE id = %s RETURNING *",
            (text, preference_id),
        ).fetchone()
        group.record_updated("preferences", preference_id, row, after)
    return dict(after)


__all__ = ["MAX_PREFERENCE_BODY", "update_preference_body"]
