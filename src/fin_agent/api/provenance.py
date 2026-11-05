"""Attach a movement's real capture rows to its detail payload.

The chat capture stores the user message id in `captures.ref`; the message row holds the
conversation. No link is invented: fields stay null when the captured row no longer exists.
"""

from __future__ import annotations

from typing import Any

import psycopg


def attach_sources(
    conn: psycopg.Connection, movement_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT c.kind, c.ref, m.id AS message_id, m.conversation_id
        FROM captures c
        LEFT JOIN messages m ON c.kind = 'chat' AND m.id = c.ref
        WHERE c.movement_id = %s
        ORDER BY c.id
        """,
        (movement_id,),
    ).fetchall()
    payload["sources"] = [
        {
            "kind": row["kind"],
            "ref": row["ref"],
            "message_id": row["message_id"],
            "conversation_id": row["conversation_id"],
        }
        for row in rows
    ]
    return payload
