"""Statement layouts and their append-only parser versions.

A layout is one bank and statement type, holding the header fingerprints seen for it. A commit
appends a version only when the script changed; a rollback moves `is_active` without deleting
anything.
"""

from __future__ import annotations

from typing import Any

from arno.db.conn import Connection
from arno.importers.helpers import HELPER_VERSION
from arno.ledger.audit import new_id


async def layout_for(
    conn: Connection,
    fingerprint: str | None,
    *,
    institution: str | None = None,
    kind: str | None = None,
) -> dict[str, Any] | None:
    """The bank's layout for this statement, with its active version.

    A layout is a bank and statement type; each header set is a variant of it. A known variant
    picks its layout; otherwise the bank and type do, because a file's headers aren't always
    detectable before a parser runs (an SBI PDF's are not), and a changed header set is exactly
    what a replay-then-repair handles.
    """
    if not fingerprint and not (institution and kind):
        return None
    return await conn.fetchrow(
        """
        SELECT l.id, l.institution, l.kind, l.variants,
               v.id AS version_id, v.version, v.parser, v.helper_version
        FROM statement_layouts l
        JOIN parser_versions v ON v.layout_id = l.id AND v.is_active
        WHERE ($1::text IS NOT NULL AND $1 = ANY(l.variants)
               AND ($2::text IS NULL OR lower(l.kind) = lower($2))
               AND ($3::text IS NULL OR lower(l.institution) = lower($3)))
           OR ($2::text IS NOT NULL AND $3::text IS NOT NULL
               AND lower(l.kind) = lower($2) AND lower(l.institution) = lower($3))
        ORDER BY ($1::text IS NOT NULL AND $1 = ANY(l.variants)) DESC, l.created_at
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
    fingerprint: str | None,
    parser: str,
    source_import_id: str | None = None,
) -> dict[str, Any]:
    """Save a committed parser. Called only after a commit, never after a reject or failure."""
    institution = (institution or "unknown")[:120]
    kind = (kind or "unknown")[:40]
    async with conn.transaction():
        layout = await conn.fetchrow(
            """
            SELECT * FROM statement_layouts
            WHERE lower(institution) = lower($1) AND lower(kind) = lower($2)
            FOR UPDATE
            """,
            institution,
            kind,
        )
        if layout is None:
            layout = await conn.fetchrow_required(
                """
                INSERT INTO statement_layouts (id, institution, kind, variants)
                VALUES ($1, $2, $3, array_remove(ARRAY[$4::text], NULL))
                RETURNING *
                """,
                await new_id(conn, "lay"),
                institution,
                kind,
                fingerprint,
            )
        else:
            # `ARRAY[$1]`, not `variants || $1::text[]`: the parameter is one fingerprint string, so
            # asyncpg encodes it as text. Declaring the parameter text[] makes Postgres read the
            # string as an array literal and fail on the first new variant of a known bank.
            await conn.execute(
                """
                UPDATE statement_layouts
                SET variants = variants || ARRAY[$1::text], updated_at = now()
                WHERE id = $2 AND $1::text IS NOT NULL AND NOT ($1::text = ANY(variants))
                """,
                fingerprint,
                layout["id"],
            )

        active = await conn.fetchrow(
            "SELECT * FROM parser_versions WHERE layout_id = $1 AND is_active",
            layout["id"],
        )
        if (
            active is not None
            and active["parser"] == parser
            and active["helper_version"] == HELPER_VERSION
        ):
            version = active
        else:
            next_version = await conn.fetchrow_required(
                "SELECT coalesce(max(version), 0) + 1 AS version "
                "FROM parser_versions WHERE layout_id = $1",
                layout["id"],
            )
            await conn.execute(
                "UPDATE parser_versions SET is_active = false WHERE layout_id = $1 AND is_active",
                layout["id"],
            )
            version = await conn.fetchrow_required(
                """
                INSERT INTO parser_versions
                    (id, layout_id, version, parser, helper_version, source_import_id, is_active)
                VALUES ($1, $2, $3, $4, $5, $6, true)
                RETURNING *
                """,
                await new_id(conn, "pv"),
                layout["id"],
                int(next_version["version"]),
                parser,
                HELPER_VERSION,
                source_import_id,
            )
            await conn.execute(
                "UPDATE statement_layouts SET updated_at = now() WHERE id = $1",
                layout["id"],
            )
    return {**layout, "version_id": version["id"], "version": version["version"]}


async def rollback_layout(conn: Connection, *, layout_id: str, version: int) -> dict[str, Any]:
    """Move a layout's active flag back to an earlier version."""
    async with conn.transaction():
        layout = await conn.fetchrow(
            "SELECT id FROM statement_layouts WHERE id = $1 FOR UPDATE",
            layout_id,
        )
        if layout is None:
            raise ValueError("No such layout")
        target = await conn.fetchrow(
            "SELECT id, version FROM parser_versions WHERE layout_id = $1 AND version = $2",
            layout_id,
            int(version),
        )
        if target is None:
            raise ValueError("No such parser version")
        await conn.execute(
            "UPDATE parser_versions SET is_active = false WHERE layout_id = $1 AND is_active",
            layout_id,
        )
        await conn.execute(
            "UPDATE parser_versions SET is_active = true WHERE id = $1",
            target["id"],
        )
        return await conn.fetchrow_required(
            "UPDATE statement_layouts SET updated_at = now() WHERE id = $1 RETURNING *",
            layout_id,
        )


__all__ = ["layout_for", "rollback_layout", "save_version"]
