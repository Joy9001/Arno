"""The rules engine: match a statement payee to a category before any model is asked.

A rule is `equals`, `contains`, or a regex. Your rules beat the model's; inside one source a
higher priority wins, then a longer pattern. Indian payment-rail tokens and
reference numbers are stripped from a narration first, and a parser's own `counterparty` is
preferred. A credit that reverses an earlier debit within 60 days takes that purchase's category.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from arno.db.conn import Connection
from arno.ledger.audit import WriteGroup, write_group
from arno.ledger.payees import is_placeholder_name

SOURCE_RANK = {"user": 2, "llm": 1}
PAYMENT_APPS = ("one97", "paytm", "phonepe", "google pay", "gpay", "amazon pay", "razorpay")
RAILS = ("upi/dr", "upi/cr", "upi", "neft", "imps", "rtgs", "pos", "nach", "wdl tfr", "atm wdl")
_RAIL_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(token) for token in RAILS).replace(r"\ ", r"\s*") + r")\b",
    re.IGNORECASE,
)
_HANDLE_RE = re.compile(r"[^\s/]+@[^\s/]+")
_LONG_NUMBER_RE = re.compile(r"\b\d{4,}\b")
_NOISE_RE = re.compile(r"[^a-z0-9 ]+")
_SPACE_RE = re.compile(r"\s+")
_CRED_RE = re.compile(r"\bcred\b")


def normalize_pattern(text: str) -> str:
    """One spelling for a merchant: trim, collapse whitespace, lower-case."""
    return _SPACE_RE.sub(" ", str(text or "").strip()).lower()


def payee_key(narration: Any, counterparty: Any = None) -> str:
    """The payee a rule matches: the parsed counterparty when there is one, else the narration
    with payment-rail tokens, UPI handles, and long reference numbers removed."""
    text = str(counterparty or "").strip()
    key = _clean_key(text) if text and not is_placeholder_name(text) else ""
    if _letters(key) < MIN_KEY_LETTERS:
        # No counterparty, a rail token ("DR", "UPI") that names nobody, or a fragment such as a
        # lone initial or a day number: the narration still names the merchant.
        key = _clean_key(str(narration or ""))
    # A key that names only a rail token, a placeholder, or a fragment is no payee: no rule is
    # ever learned for it, and no match is attempted against it.
    if is_placeholder_name(key) or _letters(key) < MIN_KEY_LETTERS:
        return ""
    return key


MIN_KEY_LETTERS = 3
"""A payee key with fewer letters names nobody ("m", "08"); the model only guessed at those."""


def _letters(text: str) -> int:
    return sum(1 for char in text if char.isalpha())


def _clean_key(text: str) -> str:
    if "@" in text and " " not in text:
        # A UPI handle is the only name this line has; keep its local part.
        text = text.split("@", 1)[0]
    text = _HANDLE_RE.sub(" ", text)
    text = _RAIL_RE.sub(" ", text)
    text = _LONG_NUMBER_RE.sub(" ", text)
    text = _NOISE_RE.sub(" ", text.lower())
    return _SPACE_RE.sub(" ", text).strip()[:200]


def is_payment_app(key: str) -> bool:
    """True when the payee is an app that fronts anything, so a name-only rule would be wrong."""
    text = normalize_pattern(key)
    return any(name in text for name in PAYMENT_APPS) or _CRED_RE.search(text) is not None


async def load_rules(conn: Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in await conn.fetch(
            """
            SELECT r.id, r.match, r.pattern, r.priority, r.source, r.category_id,
                   c.name AS category_name
            FROM category_rules r
            JOIN categories c ON c.id = r.category_id
            WHERE c.active
            """
        )
    ]


def _rule_matches(rule: dict[str, Any], text: str) -> bool:
    pattern = str(rule["pattern"])
    match = str(rule["match"])
    if match == "equals":
        return text == pattern
    if match == "contains":
        return pattern in text
    try:
        return re.search(pattern, text) is not None
    except re.error:
        return False


def match_rule(
    text: str,
    rules: list[dict[str, Any]],
    *,
    sources: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    """The best rule for a payee key, or None."""
    key = normalize_pattern(text)
    if not key:
        return None
    allowed = set(sources) if sources is not None else set(SOURCE_RANK)
    best: dict[str, Any] | None = None
    best_rank: tuple[int, int, int] = (-1, -1, -1)
    for rule in rules:
        source = str(rule["source"])
        if source not in allowed:
            continue
        if not _rule_matches(rule, key):
            continue
        rank = (
            SOURCE_RANK.get(source, 0),
            int(rule.get("priority") or 0),
            len(str(rule["pattern"])),
        )
        if rank > best_rank:
            best, best_rank = rule, rank
    return best


async def _import_rows(conn: Connection, import_id: str) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT sl.id AS line_id, sl.date, sl.description_raw, sl.amount_paise, sl.match_detail,
               m.id AS movement_id, m.kind, m.category_rule_id, m.category_hint,
               label.category_id,
               payee.kind AS payee_kind, payee.display_name AS payee_name
        FROM statement_lines sl
        JOIN movements m ON m.id = sl.movement_id
        LEFT JOIN LATERAL (
            SELECT ml.category_id
            FROM movement_legs ml
            WHERE ml.movement_id = m.id AND ml.category_id IS NOT NULL
            LIMIT 1
        ) label ON true
        LEFT JOIN LATERAL (
            SELECT p.kind, p.display_name
            FROM movement_legs ml
            JOIN payees p ON p.id = ml.payee_id
            WHERE ml.movement_id = m.id
            LIMIT 1
        ) payee ON true
        WHERE sl.import_id = $1
          AND sl.match_state = 'merged'
          AND sl.movement_id IS NOT NULL
        ORDER BY sl.date, sl.id
        """,
        import_id,
    )
    return [dict(row) for row in rows]


def row_key(row: dict[str, Any]) -> str:
    detail = row.get("match_detail") or {}
    other = detail.get("counterparty") or row.get("payee_name")
    return payee_key(row.get("description_raw"), other)


async def record_categorize_group(conn: Connection, *, import_id: str, group_id: str) -> None:
    """Attribute one categorization write group to its import, so undoing the import reverts it."""
    await conn.execute(
        """
        UPDATE imports SET categorize_groups = categorize_groups || to_jsonb($2::text)
        WHERE id = $1
        """,
        import_id,
        group_id,
    )


async def lock_import(conn: Connection, import_id: str) -> None:
    """Take the import's row lock before writing any row the import owns.

    Undo starts with this same lock, so it and a categorization write wait for each other
    instead of each holding a row the other needs.
    """
    await conn.fetchval("SELECT id FROM imports WHERE id = $1 FOR UPDATE", import_id)


async def label(
    conn: Connection,
    group: WriteGroup,
    movement_id: str,
    category_id: str,
    rule_id: str | None,
) -> bool:
    legs = await conn.fetch(
        "SELECT * FROM movement_legs WHERE movement_id = $1 ORDER BY transfer_id",
        movement_id,
    )
    if not legs:
        return False
    for leg in legs:
        if leg["category_id"] == category_id:
            continue
        after = await conn.fetchrow(
            """
            UPDATE movement_legs
            SET category_id = $1, updated_at = now()
            WHERE transfer_id = $2
            RETURNING *
            """,
            category_id,
            leg["transfer_id"],
        )
        if after is not None:
            group.record_updated("movement_legs", str(leg["transfer_id"]), dict(leg), dict(after))
    before = await conn.fetchrow("SELECT * FROM movements WHERE id = $1", movement_id)
    if before is None:
        return False
    await conn.execute(
        """
        UPDATE movements
        SET category_rule_id = $1, category_hint = NULL
        WHERE id = $2
        """,
        rule_id,
        movement_id,
    )
    after = await conn.fetchrow("SELECT * FROM movements WHERE id = $1", movement_id)
    if after is not None and dict(after) != dict(before):
        group.record_updated("movements", movement_id, dict(before), dict(after))
    return True


async def _refund_source(
    conn: Connection, *, row: dict[str, Any], key: str, amount: int
) -> dict[str, Any] | None:
    day = row["date"]
    rows = await conn.fetch(
        """
        SELECT sl.description_raw, sl.match_detail, sl.amount_paise, m.id AS movement_id,
               m.category_rule_id, label.category_id, payee.display_name AS payee_name
        FROM statement_lines sl
        JOIN movements m ON m.id = sl.movement_id
        JOIN LATERAL (
            SELECT ml.category_id
            FROM movement_legs ml
            WHERE ml.movement_id = m.id AND ml.category_id IS NOT NULL
            LIMIT 1
        ) label ON true
        LEFT JOIN LATERAL (
            SELECT p.display_name
            FROM movement_legs ml
            JOIN payees p ON p.id = ml.payee_id
            WHERE ml.movement_id = m.id
            LIMIT 1
        ) payee ON true
        WHERE sl.amount_paise < 0
          AND m.date BETWEEN $1 AND $2
          AND abs(sl.amount_paise) >= $3
        ORDER BY m.date DESC, sl.id DESC
        LIMIT 100
        """,
        day - timedelta(days=60),
        day,
        amount,
    )
    for candidate in rows:
        if row_key(dict(candidate)) == key:
            return dict(candidate)
    return None


async def apply_rules(conn: Connection, import_id: str) -> dict[str, int]:
    """Label every committed row of an import that a rule or a refund covers.

    One write group per import, attributed to it: undoing the import reverts these labels.
    """
    rules = await load_rules(conn)
    labeled = 0
    async with write_group(conn, actor="agent") as group:
        await lock_import(conn, import_id)
        rows = await _import_rows(conn, import_id)
        for row in rows:
            if row["category_id"] is not None:
                continue
            key = row_key(row)
            if not key:
                continue
            rule = match_rule(key, rules)
            if rule is None:
                continue
            if not await label(
                conn, group, str(row["movement_id"]), str(rule["category_id"]), str(rule["id"])
            ):
                continue
            await conn.execute(
                "UPDATE category_rules SET hits = hits + 1 WHERE id = $1", rule["id"]
            )
            labeled += 1

        # A refund is a credit that reverses an earlier debit from the same payee.
        for row in rows:
            if row["category_id"] is not None or int(row["amount_paise"]) <= 0:
                continue
            key = row_key(row)
            if not key:
                continue
            source = await _refund_source(conn, row=row, key=key, amount=int(row["amount_paise"]))
            if source is None:
                continue
            if not await label(
                conn,
                group,
                str(row["movement_id"]),
                str(source["category_id"]),
                str(source["category_rule_id"]) if source["category_rule_id"] else None,
            ):
                continue
            labeled += 1
        if group.entries:
            await record_categorize_group(conn, import_id=import_id, group_id=group.group_id)
    return {"labeled": labeled, "rows": len(rows)}


async def pending_payees(conn: Connection, import_id: str) -> list[dict[str, Any]]:
    """Uncategorized payees in this import: key, count, total, a sample narration, direction."""
    groups: dict[str, dict[str, Any]] = {}
    for row in await _import_rows(conn, import_id):
        key = row_key(row)
        if row["category_id"] is not None or not key:
            continue
        amount = abs(int(row["amount_paise"]))
        entry = groups.setdefault(
            key,
            {
                "key": key,
                "count": 0,
                "total_paise": 0,
                "narration": str(row["description_raw"])[:200],
                "direction": "credit" if int(row["amount_paise"]) > 0 else "debit",
            },
        )
        entry["count"] += 1
        entry["total_paise"] += amount
    return sorted(
        groups.values(), key=lambda item: (-item["count"], -item["total_paise"], item["key"])
    )


async def pending_rows(conn: Connection, import_id: str) -> dict[str, list[str]]:
    """The movement ids still uncategorized, by payee key."""
    groups: dict[str, list[str]] = {}
    for row in await _import_rows(conn, import_id):
        if row["category_id"] is not None:
            continue
        key = row_key(row)
        if key:
            groups.setdefault(key, []).append(str(row["movement_id"]))
    return groups
