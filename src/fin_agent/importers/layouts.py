"""Saved statement layouts: one parser text for one normalized header fingerprint."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg

from fin_agent.importers.helpers import HELPER_VERSION
from fin_agent.ledger.audit import new_id


def layout_for(conn: psycopg.Connection, fingerprint: str | None) -> dict[str, Any] | None:
    if not fingerprint:
        return None
    return conn.execute(
        "SELECT * FROM statement_layouts WHERE fingerprint = %s",
        (fingerprint,),
    ).fetchone()


def save_layout(
    conn: psycopg.Connection,
    *,
    institution: str,
    fingerprint: str,
    parser: str,
    source_import_id: str,
) -> dict[str, Any]:
    """Called only from a committed import, never from a rejected or failed one."""
    row = conn.execute(
        """
        INSERT INTO statement_layouts
            (id, institution, fingerprint, parser, helper_version, source_import_id, last_used_at)
        VALUES (%s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (fingerprint) DO UPDATE
        SET institution = EXCLUDED.institution,
            parser = EXCLUDED.parser,
            helper_version = EXCLUDED.helper_version,
            source_import_id = EXCLUDED.source_import_id,
            last_used_at = now()
        RETURNING *
        """,
        (
            new_id(conn, "lay"),
            institution[:120] or "unknown",
            fingerprint,
            parser,
            HELPER_VERSION,
            source_import_id,
        ),
    ).fetchone()
    return row


def touch_layout(conn: psycopg.Connection, layout_id: str) -> None:
    conn.execute(
        "UPDATE statement_layouts SET last_used_at = %s WHERE id = %s",
        (datetime.now().astimezone(), layout_id),
    )


__all__ = ["layout_for", "save_layout", "touch_layout"]
