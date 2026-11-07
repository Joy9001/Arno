"""Statement layouts and their append-only parser versions.

A layout is one bank and statement type, holding the header fingerprints seen for it and a
pointer to its active version. A commit appends a version only when the script changed; a
rollback moves the pointer without deleting anything.
"""

from __future__ import annotations

from typing import Any

from fin_agent.db.conn import Connection
from fin_agent.importers.helpers import HELPER_VERSION
from fin_agent.ledger.audit import new_id


async def layout_for(
    conn: Connection,
    fingerprint: str | None,
    *,
    institution: str | None = None,
    kind: str | None = None,
) -> dict[str, Any] | None:
    """The layout whose variants include this header fingerprint, with its active version."""
    if not fingerprint:
        return None
    return await conn.fetchrow(
        """
        SELECT l.id, l.institution, l.kind, l.variants, l.active_version_id,
               v.id AS version_id, v.version, v.parser, v.helper_version
        FROM statement_layouts l
        JOIN parser_versions v ON v.id = l.active_version_id
        WHERE $1 = ANY(l.variants)
          AND ($2::text IS NULL OR lower(l.kind) = lower($2))
          AND ($3::text IS NULL OR lower(l.institution) = lower($3))
        ORDER BY l.created_at
        LIMIT 1
        """,
        fingerprint,
        kind,
        institution,
    )


async def save_version(
    conn: Connection,
    *,
    institution: str,
    kind: str,
    fingerprint: str,
    parser: str,
    source_import_id: str | None = None,
) -> dict[str, Any]:
    """Save a committed parser. Called only after a commit, never after a reject or failure."""
    institution = (institution or "unknown")[:120]
    kind = (kind or "unknown")[:40]
    layout = await conn.fetchrow(
        """
        SELECT * FROM statement_layouts
        WHERE institution = $1 AND kind = $2
        FOR UPDATE
        """,
        institution,
        kind,
    )
    if layout is None:
        layout = await conn.fetchrow(
            """
            INSERT INTO statement_layouts (id, institution, kind, variants)
            VALUES ($1, $2, $3, ARRAY[$4]::text[])
            RETURNING *
            """,
            await new_id(conn, "lay"),
            institution,
            kind,
            fingerprint,
        )
    else:
        await conn.execute(
            """
            UPDATE statement_layouts
            SET variants = variants || $1::text[], updated_at = now()
            WHERE id = $2 AND NOT ($1 = ANY(variants))
            """,
            fingerprint,
            layout["id"],
        )

    active = None
    if layout["active_version_id"]:
        active = await conn.fetchrow(
            "SELECT * FROM parser_versions WHERE id = $1", layout["active_version_id"]
        )
    if (
        active is not None
        and active["parser"] == parser
        and active["helper_version"] == HELPER_VERSION
    ):
        version = active
    else:
        number = await conn.fetchval(
            "SELECT coalesce(max(version), 0) + 1 FROM parser_versions WHERE layout_id = $1",
            layout["id"],
        )
        version = await conn.fetchrow(
            """
            INSERT INTO parser_versions
                (id, layout_id, version, parser, helper_version, source_import_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            await new_id(conn, "pv"),
            layout["id"],
            int(number),
            parser,
            HELPER_VERSION,
            source_import_id,
        )
        await conn.execute(
            "UPDATE statement_layouts SET active_version_id = $1, updated_at = now() WHERE id = $2",
            version["id"],
            layout["id"],
        )
    return {**layout, "active_version_id": version["id"], "version": version["version"]}


async def rollback_layout(conn: Connection, *, layout_id: str, version: int) -> dict[str, Any]:
    """Move a layout's active pointer back to an earlier version."""
    target = await conn.fetchrow(
        "SELECT id, version FROM parser_versions WHERE layout_id = $1 AND version = $2",
        layout_id,
        int(version),
    )
    if target is None:
        raise ValueError("No such parser version")
    await conn.execute(
        "UPDATE statement_layouts SET active_version_id = $1, updated_at = now() WHERE id = $2",
        target["id"],
        layout_id,
    )
    return await conn.fetchrow("SELECT * FROM statement_layouts WHERE id = $1", layout_id)


__all__ = ["layout_for", "rollback_layout", "save_version"]
