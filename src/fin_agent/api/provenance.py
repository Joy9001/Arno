"""Attach a movement's real capture rows to its detail payload.

The chat capture stores the user message id in `captures.ref`; the message row holds the
conversation. No link is invented: fields stay null when the captured row no longer exists.
"""

from __future__ import annotations

from typing import Any

from fin_agent.db.conn import Connection


async def attach_sources(
    conn: Connection, movement_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    rows = await conn.fetch(
        """
        SELECT c.kind, c.ref, m.id AS message_id, m.conversation_id
        FROM captures c
        LEFT JOIN messages m ON c.kind = 'chat' AND m.id = c.ref
        WHERE c.movement_id = $1
        ORDER BY c.id
        """,
        movement_id,
    )
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
