"""Helpers for reading the undo log in tests."""

from __future__ import annotations

from typing import Any

from arno.db.conn import Connection


async def snapshot_entries(conn: Connection, table: str, key: str) -> list[dict[str, Any]]:
    """Every snapshot entry that touched one row, oldest group first, with its group's actor."""
    return await conn.fetch(
        """
        SELECT u.id AS group_id, u.actor, e -> 'before' AS before, e -> 'after' AS after
        FROM undo_log u, jsonb_array_elements(u.snapshot) e
        WHERE e ->> 'table' = $1 AND e ->> 'key' = $2
        ORDER BY u.created_at, u.id
        """,
        table,
        key,
    )
