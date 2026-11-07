"""Seeded Indian categories and the learned merchant rules.

Rules always win over a guess. A `user` rule always wins over an `llm` rule, and a correction
replaces the guess. Categories are labels, so recategorizing never touches pgledger.
"""

from __future__ import annotations

import re
from typing import Any

from fin_agent.db.conn import Connection, quote_ident
from fin_agent.india.categories import DEFAULT_CATEGORIES
from fin_agent.ledger.audit import WriteGroup, write_group
from fin_agent.ledger.common import today
from fin_agent.ledger.errors import InvalidLedgerError, NotFoundLedgerError

RuleSource = str
_NORMALIZE_RE = re.compile(r"\s+")


def normalize_pattern(text: str) -> str:
    """One spelling for a merchant: trim, collapse whitespace, lower-case."""
    return _NORMALIZE_RE.sub(" ", text.strip()).lower()


async def _category_row(conn: Connection, category_id: str) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM categories WHERE id = $1", category_id)
    if row is None:
        raise NotFoundLedgerError(f"No category {category_id}")
    return row


async def ensure_default_categories(conn: Connection) -> list[dict[str, Any]]:
    """Insert any missing Indian defaults. Existing categories (and user edits) stay put."""
    existing = {row["name"] for row in await conn.fetch("SELECT name FROM categories")}
    missing = [(name, reducible) for name, reducible in DEFAULT_CATEGORIES if name not in existing]
    if missing:
        async with write_group(conn, actor="system") as group:
            for name, reducible in missing:
                row = await conn.fetchrow(
                    """
                    INSERT INTO categories (name, reducible) VALUES ($1, $2)
                    ON CONFLICT (name) DO NOTHING RETURNING *
                    """,
                    name,
                    reducible,
                )
                if row is not None:
                    group.record_created("categories", row)
    return await list_categories(conn)


async def list_categories(conn: Connection, *, include_inactive: bool = False) -> list[dict]:
    where = "" if include_inactive else "WHERE active"
    return await conn.fetch(f"SELECT * FROM categories {where} ORDER BY name")


async def create_category(
    conn: Connection,
    *,
    name: str,
    reducible: bool = False,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise InvalidLedgerError("A category needs a name")
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        row = await conn.fetchrow(
            """
            INSERT INTO categories (name, reducible) VALUES ($1, $2)
            ON CONFLICT (name) DO NOTHING RETURNING *
            """,
            name,
            reducible,
        )
        if row is None:
            raise InvalidLedgerError(f"Category {name} already exists")
        group.record_created("categories", row)
    return dict(row) | {"undo_id": group.group_id}


async def update_category(
    conn: Connection,
    category_id: str,
    *,
    name: str | None = None,
    reducible: bool | None = None,
    active: bool | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    before = await _category_row(conn, category_id)
    updates: dict[str, Any] = {}
    if name is not None:
        trimmed = name.strip()
        if not trimmed:
            raise InvalidLedgerError("A category needs a name")
        clash = await conn.fetchrow(
            "SELECT 1 FROM categories WHERE name = $1 AND id <> $2", trimmed, category_id
        )
        if clash is not None:
            raise InvalidLedgerError(f"Category {trimmed} already exists")
        updates["name"] = trimmed
    if reducible is not None:
        updates["reducible"] = reducible
    if active is not None:
        updates["active"] = active
    if not updates:
        return dict(before) | {"undo_id": None}
    updates["updated_at"] = "now()"
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        assignments = []
        params: list[Any] = []
        for column, value in updates.items():
            if column == "updated_at":
                assignments.append("updated_at = now()")
            else:
                params.append(value)
                assignments.append(f"{quote_ident(column)} = ${len(params)}")
        after = await conn.fetchrow(
            f"UPDATE categories SET {', '.join(assignments)} "
            f"WHERE id = ${len(params) + 1} RETURNING *",
            *params,
            category_id,
        )
        group.record_updated("categories", category_id, before, after)
    return dict(after) | {"undo_id": group.group_id}


async def archive_category(
    conn: Connection,
    category_id: str,
    *,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    return await update_category(
        conn, category_id, active=False, actor=actor, message_id=message_id
    )


async def list_rules(conn: Connection) -> list[dict[str, Any]]:
    return await conn.fetch(
        """
        SELECT r.*, c.name AS category_name
        FROM category_rules r
        JOIN categories c ON c.id = r.category_id
        ORDER BY r.source DESC, r.merchant_pattern
        """
    )


async def _find_rule(conn: Connection, patterns: list[str], source: str) -> dict[str, Any] | None:
    if not patterns:
        return None
    return await conn.fetchrow(
        """
        SELECT * FROM category_rules
        WHERE merchant_pattern = ANY($1) AND source = $2
        ORDER BY updated_at DESC, created_at DESC
        LIMIT 1
        """,
        patterns,
        source,
    )


async def resolve_category(
    conn: Connection,
    *,
    payee_id: str | None = None,
    category_id: str | None = None,
    display_name: str | None = None,
    upi_id: str | None = None,
) -> dict[str, Any]:
    """Resolve a category, returning where it came from.

    Order: an explicit category, a user rule, the payee's default (also a user correction), an
    llm rule, then nothing.
    """
    if category_id:
        category = await _category_row(conn, category_id)
        return {
            "category_id": category["id"],
            "category_name": category["name"],
            "source": "stated",
            "rule_id": None,
        }

    payee = None
    if payee_id:
        payee = await conn.fetchrow("SELECT * FROM payees WHERE id = $1", payee_id)
        if payee is None:
            raise NotFoundLedgerError(f"No payee {payee_id}")
        display_name = display_name or payee["display_name"]
        upi_id = upi_id or payee["upi_id"]

    patterns = [normalize_pattern(text) for text in (display_name, upi_id) if text]

    async def from_rule(rule: dict[str, Any]) -> dict[str, Any]:
        await conn.execute("UPDATE category_rules SET hits = hits + 1 WHERE id = $1", rule["id"])
        category = await _category_row(conn, rule["category_id"])
        return {
            "category_id": category["id"],
            "category_name": category["name"],
            "source": "rule",
            "rule_id": rule["id"],
        }

    user_rule = await _find_rule(conn, patterns, "user")
    if user_rule is not None:
        return await from_rule(user_rule)

    # A payee's default category is a user correction, so it beats an llm guess.
    if payee and payee["default_category_id"]:
        category = await _category_row(conn, payee["default_category_id"])
        return {
            "category_id": category["id"],
            "category_name": category["name"],
            "source": "rule",
            "rule_id": None,
        }

    llm_rule = await _find_rule(conn, patterns, "llm")
    if llm_rule is not None:
        return await from_rule(llm_rule)

    return {"category_id": None, "category_name": None, "source": "none", "rule_id": None}


async def _upsert_rule_in_group(
    conn: Connection,
    group: WriteGroup,
    *,
    merchant_pattern: str,
    category_id: str,
    source: str,
) -> dict[str, Any]:
    pattern = normalize_pattern(merchant_pattern)
    if not pattern:
        raise InvalidLedgerError("A rule needs a merchant pattern")
    await _category_row(conn, category_id)

    before = await conn.fetchrow(
        "SELECT * FROM category_rules WHERE merchant_pattern = $1", pattern
    )
    after = await conn.fetchrow(
        """
        INSERT INTO category_rules (merchant_pattern, category_id, source)
        VALUES ($1, $2, $3)
        ON CONFLICT (merchant_pattern) DO UPDATE
            SET category_id = EXCLUDED.category_id,
                source = EXCLUDED.source,
                updated_at = now()
            WHERE category_rules.source = 'llm' OR EXCLUDED.source = 'user'
        RETURNING *
        """,
        pattern,
        category_id,
        source,
    )
    if after is None:
        after = before
    if before is None:
        group.record_created("category_rules", after)
    elif after["category_id"] != before["category_id"] or after["source"] != before["source"]:
        group.record_updated("category_rules", after["id"], before, after)
    return dict(after)


async def upsert_rule(
    conn: Connection,
    *,
    merchant_pattern: str,
    category_id: str,
    source: str = "user",
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    if source not in {"user", "llm"}:
        raise InvalidLedgerError("A rule source is user or llm")
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        after = await _upsert_rule_in_group(
            conn,
            group,
            merchant_pattern=merchant_pattern,
            category_id=category_id,
            source=source,
        )
    return after | {"undo_id": group.group_id}


async def learn_rule(
    conn: Connection,
    *,
    merchant_pattern: str,
    category_id: str,
    actor: str = "agent",
    message_id: str | None = None,
) -> dict[str, Any]:
    """Save the categorizer's answer as an `llm` rule. It never replaces a user rule."""
    return await upsert_rule(
        conn,
        merchant_pattern=merchant_pattern,
        category_id=category_id,
        source="llm",
        actor=actor,
        message_id=message_id,
    )


async def delete_rule(
    conn: Connection,
    rule_id: str,
    *,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    before = await conn.fetchrow("SELECT * FROM category_rules WHERE id = $1", rule_id)
    if before is None:
        raise NotFoundLedgerError(f"No rule {rule_id}")
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        await conn.execute("DELETE FROM category_rules WHERE id = $1", rule_id)
        group.record_deleted("category_rules", before)
    return {"status": "deleted", "id": rule_id, "undo_id": group.group_id}


async def relabel_payee_history(
    conn: Connection,
    *,
    payee_id: str,
    category_id: str,
    group: WriteGroup,
) -> int:
    """Apply a payee's category to every past label for that payee, auditing each change."""
    await _category_row(conn, category_id)
    labels = await conn.fetch(
        """
        SELECT * FROM transfer_labels
        WHERE payee_id = $1 AND category_id IS DISTINCT FROM $2
        """,
        payee_id,
        category_id,
    )
    for label in labels:
        await conn.execute(
            """
            UPDATE transfer_labels
            SET category_id = $1, updated_at = now()
            WHERE transfer_id = $2
            """,
            category_id,
            label["transfer_id"],
        )
        updated = await conn.fetchrow(
            "SELECT * FROM transfer_labels WHERE transfer_id = $1", label["transfer_id"]
        )
        if updated is not None:
            group.record_updated("transfer_labels", label["transfer_id"], label, updated)
        else:  # pragma: no cover - the row cannot disappear in this transaction
            raise InvalidLedgerError("The label disappeared mid-correction")
    return len(labels)


async def _apply_payee_correction_in_group(
    conn: Connection,
    group: WriteGroup,
    *,
    payee_id: str,
    category_id: str,
    scope: str,
) -> dict[str, Any]:
    payee = await conn.fetchrow("SELECT * FROM payees WHERE id = $1", payee_id)
    if payee is None:
        raise NotFoundLedgerError(f"No payee {payee_id}")
    await _category_row(conn, category_id)
    if scope not in {"future", "all"}:
        raise InvalidLedgerError("scope must be future or all")

    after = await conn.fetchrow(
        """
        UPDATE payees
        SET default_category_id = $1, updated_at = now()
        WHERE id = $2 RETURNING *
        """,
        category_id,
        payee_id,
    )
    group.record_updated("payees", payee_id, payee, after)

    rule = await _upsert_rule_in_group(
        conn,
        group,
        merchant_pattern=payee["display_name"],
        category_id=category_id,
        source="user",
    )
    rows_changed = 0
    if scope == "all":
        rows_changed = await relabel_payee_history(
            conn, payee_id=payee_id, category_id=category_id, group=group
        )
    return {
        "rule": rule,
        "rows_changed": rows_changed,
    }


async def apply_payee_correction(
    conn: Connection,
    *,
    payee_id: str,
    category_id: str,
    scope: str,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    """Set a payee's default category and (for `all`) relabel its past rows, in one group."""
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        result = await _apply_payee_correction_in_group(
            conn, group, payee_id=payee_id, category_id=category_id, scope=scope
        )
    return {
        "status": "corrected",
        "payee_id": payee_id,
        "category_id": category_id,
        **result,
        "undo_id": group.group_id,
    }


async def category_tree(conn: Connection) -> dict[str, Any]:
    """What the settings panel needs: categories and rules, with their undo IDs."""
    return {
        "categories": [dict(row) for row in await list_categories(conn, include_inactive=True)],
        "rules": [dict(row) for row in await list_rules(conn)],
        "as_of": today().isoformat(),
    }
