"""The Uncategorized list: what the categorizer left with a hint, grouped by payee.

Only spending rows belong here: income, transfers, and adjustments never need a category. Rows
are grouped by the same payee key rules match on. A UPI person is grouped by name and shown as a
person, because rent and a friend's dinner share cannot be told apart from the narration.
Answering one group writes a `user` rule for that payee and labels the spending side of every row
in one write group; the other people on a split keep their own labels untouched.
"""

from __future__ import annotations

import re
from typing import Any

from fin_agent.db.conn import Connection
from fin_agent.importers.rules import normalize_pattern, payee_key
from fin_agent.ledger.audit import write_group
from fin_agent.ledger.categories import upsert_rule_in_group
from fin_agent.ledger.errors import InvalidLedgerError

_MERCHANT_TOKENS = re.compile(
    r"\b(?:pvt|ltd|limited|llp|inc|corp|company|enterprises|technologies|services|"
    r"stores?|retail|traders?|industries|foods?|restaurant|cafe|hospital|pharmacy|mall|mart|"
    r"hotel|india|international)\b"
)
_UPI = re.compile(r"upi", re.IGNORECASE)
_HANDLE = re.compile(r"[^\s/@]+@[^\s/]+")
_MERCHANT_HANDLES = ("merchant", "merch", "store", "shop", "business", "pay")


def _is_person(key: str, narration: str, payee_kind: str | None) -> bool:
    if payee_kind == "person":
        return True
    if _MERCHANT_TOKENS.search(key):
        return False
    handles = _HANDLE.findall(narration)
    if not handles and _UPI.search(narration) is None:
        return False
    words = set(key.split())
    for handle in handles:
        local = normalize_pattern(handle.split("@", 1)[0])
        if any(token in local for token in _MERCHANT_HANDLES):
            return False
        if local.isdigit() or local in words or local.replace(" ", "") in key.replace(" ", ""):
            return True
    return False


async def uncategorized_rows(conn: Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        WITH spend AS (
            SELECT movement_id, abs(sum(amount_paise))::bigint AS amount_paise
            FROM v_spending
            GROUP BY movement_id
        ),
        label AS (
            SELECT movement_id, bool_or(category_id IS NOT NULL) AS labeled
            FROM transfer_labels
            GROUP BY movement_id
        )
        SELECT DISTINCT ON (m.id)
               m.id, m.date, m.description, m.kind, m.category_hint,
               sl.description_raw, sl.match_detail, abs(sl.amount_paise) AS line_amount_paise,
               spend.amount_paise AS spend_amount_paise,
               payee.kind AS payee_kind, payee.display_name AS payee_name
        FROM movements m
        LEFT JOIN statement_lines sl ON sl.movement_id = m.id
        JOIN spend ON spend.movement_id = m.id
        LEFT JOIN label ON label.movement_id = m.id
        LEFT JOIN LATERAL (
            SELECT p.kind, p.display_name
            FROM transfer_labels tl
            JOIN payees p ON p.id = tl.payee_id
            WHERE tl.movement_id = m.id
            LIMIT 1
        ) payee ON true
        WHERE NOT coalesce(label.labeled, false)
          AND m.kind NOT IN ('transfer', 'adjustment', 'reversal')
          AND m.reverses_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM movements r WHERE r.reverses_id = m.id)
        ORDER BY m.id, m.date DESC
        """,
    )
    return [dict(row) for row in rows]


def _key(row: dict[str, Any]) -> str:
    detail = row.get("match_detail") or {}
    return payee_key(
        row.get("description_raw") or row.get("description"),
        detail.get("counterparty") or row.get("payee_name"),
    )


async def groups(conn: Connection) -> dict[str, Any]:
    """Uncategorized movements grouped by payee, most frequent first."""
    grouped: dict[str, dict[str, Any]] = {}
    total = 0
    for row in await uncategorized_rows(conn):
        key = _key(row)
        if not key:
            continue
        amount = int(row.get("line_amount_paise") or row.get("spend_amount_paise") or 0)
        narration = str(row.get("description_raw") or row.get("description") or "")
        hint = row.get("category_hint")
        entry = grouped.get(key)
        if entry is None:
            grouped[key] = {
                "key": key,
                "kind": "person"
                if _is_person(key, narration, row.get("payee_kind"))
                else "merchant",
                "count": 1,
                "total_paise": amount,
                "narration": narration[:200],
                "hint": hint,
            }
        else:
            entry["count"] += 1
            entry["total_paise"] += amount
            entry["hint"] = entry["hint"] or hint
        total += 1
    return {
        "groups": sorted(
            grouped.values(),
            key=lambda item: (-item["count"], -item["total_paise"], item["key"]),
        ),
        "total": total,
    }


async def answer(
    conn: Connection,
    *,
    key: str,
    category_id: str,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    """Write a user rule for one payee and label every uncategorized row of that group."""
    wanted = normalize_pattern(key)
    if not wanted:
        raise InvalidLedgerError("Pick an uncategorized payee first")
    category = await conn.fetchrow(
        "SELECT id FROM categories WHERE id = $1 AND active", category_id
    )
    if category is None:
        raise InvalidLedgerError("Choose an active category")
    rows = [row for row in await uncategorized_rows(conn) if _key(row) == wanted]
    if not rows:
        raise InvalidLedgerError("That payee has no uncategorized rows")

    async with write_group(conn, actor=actor, message_id=message_id) as group:
        rule = await upsert_rule_in_group(
            conn,
            group,
            pattern=wanted,
            category_id=str(category["id"]),
            source="user",
        )
        for row in rows:
            movement_id = str(row["id"])
            # Only the spending transfer carries a category. A split's person transfers must
            # keep their own labels, or the money someone owes would look like spending.
            labels = await conn.fetch(
                """
                SELECT tl.*
                FROM transfer_labels tl
                JOIN pgledger_transfers t ON t.id = tl.transfer_id
                JOIN accounts sp ON sp.kind = 'spending'
                WHERE tl.movement_id = $1
                  AND (t.from_account_id = sp.ledger_account_id
                       OR t.to_account_id = sp.ledger_account_id)
                ORDER BY tl.transfer_id
                """,
                movement_id,
            )
            if not labels:
                # A plain expense with no payee, category, or note has no label row at all;
                # without this the answer writes a rule and leaves the spend uncategorized.
                transfer = await conn.fetchrow(
                    """
                    SELECT t.id
                    FROM pgledger_transfers t
                    JOIN accounts sp ON sp.kind = 'spending'
                    WHERE t.metadata ->> 'movement_id' = $1
                      AND (t.from_account_id = sp.ledger_account_id
                           OR t.to_account_id = sp.ledger_account_id)
                    ORDER BY t.id
                    LIMIT 1
                    """,
                    movement_id,
                )
                if transfer is None:  # pragma: no cover - uncategorized_rows only returns spend
                    continue
                created = await conn.fetchrow_required(
                    """
                    INSERT INTO transfer_labels (transfer_id, movement_id, category_id)
                    VALUES ($1, $2, $3) RETURNING *
                    """,
                    transfer["id"],
                    movement_id,
                    category["id"],
                )
                group.record_created("transfer_labels", created)
                labels = [created]
            for label in labels:
                if str(label["category_id"] or "") == str(category["id"]):
                    continue
                await conn.execute(
                    """
                    UPDATE transfer_labels SET category_id = $1, updated_at = now()
                    WHERE transfer_id = $2
                    """,
                    category["id"],
                    label["transfer_id"],
                )
                after = await conn.fetchrow(
                    "SELECT * FROM transfer_labels WHERE transfer_id = $1", label["transfer_id"]
                )
                if after is None:  # pragma: no cover - the label was updated just above
                    raise InvalidLedgerError("The label disappeared mid-correction")
                group.record_updated(
                    "transfer_labels", str(label["transfer_id"]), dict(label), dict(after)
                )
            before = await conn.fetchrow_required(
                "SELECT * FROM movements WHERE id = $1", movement_id
            )
            await conn.execute(
                """
                UPDATE movements SET category_rule_id = $1, category_hint = NULL
                WHERE id = $2
                """,
                rule["id"],
                movement_id,
            )
            after = await conn.fetchrow_required(
                "SELECT * FROM movements WHERE id = $1", movement_id
            )
            group.record_updated("movements", movement_id, dict(before), dict(after))

    return {
        "status": "answered",
        "undo_id": group.group_id,
        "rule_id": rule["id"],
        "labeled": len(rows),
    }


__all__ = ["answer", "groups", "uncategorized_rows"]
