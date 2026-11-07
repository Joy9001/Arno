"""Saved statement layouts: one parser text for one normalized header fingerprint."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fin_agent.db.conn import Connection
from fin_agent.importers.helpers import HELPER_VERSION
from fin_agent.ledger.audit import new_id


async def layout_for(conn: Connection, fingerprint: str | None) -> dict[str, Any] | None:
    if not fingerprint:
        return None
    return await conn.fetchrow(
        "SELECT * FROM statement_layouts WHERE fingerprint = $1",
        fingerprint,
    )


async def save_layout(
    conn: Connection,
    *,
    institution: str,
    fingerprint: str,
    parser: str,
    source_import_id: str,
) -> dict[str, Any]:
    """Called only from a committed import, never from a rejected or failed one."""
    row = await conn.fetchrow(
        """
        INSERT INTO statement_layouts
            (id, institution, fingerprint, parser, helper_version, source_import_id, last_used_at)
        VALUES ($1, $2, $3, $4, $5, $6, now())
        ON CONFLICT (fingerprint) DO UPDATE
        SET institution = EXCLUDED.institution,
            parser = EXCLUDED.parser,
            helper_version = EXCLUDED.helper_version,
            source_import_id = EXCLUDED.source_import_id,
            last_used_at = now()
        RETURNING *
        """,
        await new_id(conn, "lay"),
        institution[:120] or "unknown",
        fingerprint,
        parser,
        HELPER_VERSION,
        source_import_id,
    )
    return row


async def touch_layout(conn: Connection, layout_id: str) -> None:
    await conn.execute(
        "UPDATE statement_layouts SET last_used_at = $1 WHERE id = $2",
        datetime.now().astimezone(),
        layout_id,
    )


__all__ = ["layout_for", "save_layout", "touch_layout"]
