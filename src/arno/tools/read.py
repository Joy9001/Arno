"""The read tools, as plain Python over the database.

PydanticAI never appears here: the agent toolset, a future MCP server, and the REST API all call
these same functions. Every return is JSON-native and source IDs travel beside the values they
explain.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from arno.db.conn import Connection, connect

# A memory's last replaced value, straight from the undo entry that replaced it: what it said, when
# it was saved, and when it was replaced. Undone edits are not history.
_PREFERENCE_PREVIOUS = """
    LEFT JOIN LATERAL (
        SELECT jsonb_build_object(
                   'text', e.entry -> 'before' -> 'body',
                   'changed_at', e.entry -> 'before' ->> 'updated_at',
                   'superseded_at', u.created_at) AS previous,
               u.created_at AS changed_at
        FROM undo_log u
        CROSS JOIN LATERAL jsonb_array_elements(u.snapshot) AS e(entry)
        WHERE u.snapshot @> jsonb_build_array(
                  jsonb_build_object('table', 'preferences', 'key', p.id::text))
          AND e.entry ->> 'table' = 'preferences' AND e.entry ->> 'key' = p.id::text
          AND u.undone_at IS NULL
          AND e.entry -> 'before' ? 'body' AND e.entry -> 'after' ? 'body'
          AND e.entry -> 'before' ->> 'body' IS DISTINCT FROM e.entry -> 'after' ->> 'body'
        ORDER BY u.created_at DESC, u.id DESC
        LIMIT 1
    ) change ON true
"""


async def run_query(sql: str, *, deadline_seconds: float = 2.0) -> dict[str, Any]:
    """Run one read-only statement as `agent_ro`, always rolled back, always marked."""
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise ValueError("No SQL was given")
    async with connect(role="agent_ro") as conn:
        tx = conn.transaction(readonly=True)
        await tx.start()
        try:
            await conn.execute("SET LOCAL statement_timeout = '2s'")
            stmt = await conn.prepare(text, timeout=deadline_seconds)
            attributes = stmt.get_attributes()
            if not attributes:
                raise ValueError("run_query needs a statement that returns rows")
            columns = [attr.name for attr in attributes]
            cursor = await stmt.cursor()
            records = await cursor.fetch(1001, timeout=deadline_seconds)
        finally:
            await tx.rollback()
    truncated = len(records) > 1000
    converted: list[list[Any]] = []
    for record in records[:1000]:
        values_in = list(record)
        values: list[Any] = []
        for value in values_in:
            if isinstance(value, Decimal):
                value = int(value) if value == value.to_integral_value() else float(value)
            elif isinstance(value, (date, datetime)):
                value = value.isoformat()
            values.append(value)
        converted.append(values)
    return {
        "columns": columns,
        "rows": converted,
        "row_count": len(converted),
        "truncated": truncated,
        "sql": text,
    }


async def active_preferences(conn: Connection) -> list[dict[str, Any]]:
    return await conn.fetch(
        f"""
        SELECT p.id, p.body, p.kind, p.subject, p.created_by, p.active, p.created_at, p.updated_at,
               change.previous
        FROM preferences p{_PREFERENCE_PREVIOUS}
        ORDER BY p.created_at
        """  # noqa: S608 - the fragment is a constant, not user input
    )


__all__ = ["active_preferences", "run_query"]
