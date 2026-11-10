"""The rules engine: match a statement payee to a category before any model is asked.

A rule is `equals`, `contains`, or a regex. Your rules beat the model's, which beat the seed list;
inside one source a higher priority wins, then a longer pattern. Indian payment-rail tokens and
reference numbers are stripped from a narration first, and a parser's own `counterparty` is
preferred. A credit that reverses an earlier debit within 60 days takes that purchase's category.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from fin_agent.db.conn import Connection
from fin_agent.importers.seed_rules import PAYMENT_APPS
from fin_agent.ledger.audit import WriteGroup, write_group
from fin_agent.ledger.payees import is_placeholder_name

SOURCE_RANK = {"user": 3, "llm": 2, "seed": 1}
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
    if not text or is_placeholder_name(text):
        # No counterparty, or a rail token ("DR", "UPI") that names nobody: the narration still
        # names the merchant.
        text = str(narration or "")
    if "@" in text and " " not in text:
        # A UPI handle is the only name this line has; keep its local part.
        text = text.split("@", 1)[0]
    text = _HANDLE_RE.sub(" ", text)
    text = _RAIL_RE.sub(" ", text)
    text = _LONG_NUMBER_RE.sub(" ", text)
    text = _NOISE_RE.sub(" ", text.lower())
    key = _SPACE_RE.sub(" ", text).strip()[:200]
    # A key that names only a rail token or a placeholder is no payee: no rule is ever learned
    # for it, and no match is attempted against it.
    return "" if is_placeholder_name(key) else key


def is_payment_app(key: str) -> bool:
    """True when the payee is an app that fronts anything, so a name match would be wrong."""
    text = normalize_pattern(key)
    if any(name in text for name in PAYMENT_APPS if name != "cred"):
        return True
    return _CRED_RE.search(text) is not None


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
    """The best rule for a payee key, or None. Seed rules never match a payment app by name."""
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
        if source == "seed" and is_payment_app(key):
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
            SELECT tl.category_id
            FROM transfer_labels tl
            WHERE tl.movement_id = m.id AND tl.category_id IS NOT NULL
            LIMIT 1
        ) label ON true
        LEFT JOIN LATERAL (
            SELECT p.kind, p.display_name
            FROM transfer_labels tl
            JOIN payees p ON p.id = tl.payee_id
            WHERE tl.movement_id = m.id
            LIMIT 1
        ) payee ON true
        WHERE sl.import_id = $1
          AND sl.match_state = 'merged'
          AND sl.movement_id IS NOT NULL
          AND m.reverses_id IS NULL
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


async def _label(
    conn: Connection,
    group: WriteGroup,
    movement_id: str,
    category_id: str,
    rule_id: str | None,
) -> bool:
    labels = await conn.fetch("SELECT * FROM transfer_labels WHERE movement_id = $1", movement_id)
    if not labels:
        # A plain expense with no payee, note, or category has no label row at all. Without
        # creating one the rule fires, records itself, and leaves the row uncategorized — how a
        # card's annual fee, whose narration names nobody, used to stay uncategorized.
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
        if transfer is None:
            return False
        labels = [
            await conn.fetchrow(
                """
                INSERT INTO transfer_labels (transfer_id, movement_id, category_id)
                VALUES ($1, $2, $3) RETURNING *
                """,
                transfer["id"],
                movement_id,
                category_id,
            )
        ]
        group.record_created("transfer_labels", labels[0])
    for label in labels:
        if label["category_id"] == category_id:
            continue
        await conn.execute(
            """
            UPDATE transfer_labels
            SET category_id = $1, updated_at = now()
            WHERE transfer_id = $2
            """,
            category_id,
            label["transfer_id"],
        )
        after = await conn.fetchrow(
            "SELECT * FROM transfer_labels WHERE transfer_id = $1", label["transfer_id"]
        )
        if after is not None:
            group.record_updated(
                "transfer_labels", str(label["transfer_id"]), dict(label), dict(after)
            )
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
            SELECT tl.category_id
            FROM transfer_labels tl
            WHERE tl.movement_id = m.id AND tl.category_id IS NOT NULL
            LIMIT 1
        ) label ON true
        LEFT JOIN LATERAL (
            SELECT p.display_name
            FROM transfer_labels tl
            JOIN payees p ON p.id = tl.payee_id
            WHERE tl.movement_id = m.id
            LIMIT 1
        ) payee ON true
        WHERE sl.amount_paise < 0
          AND m.date BETWEEN $1 AND $2
          AND abs(sl.amount_paise) >= $3
          AND m.reverses_id IS NULL
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
    rows = await _import_rows(conn, import_id)
    labeled = 0
    async with write_group(conn, actor="agent") as group:
        for row in rows:
            if row["category_id"] is not None:
                continue
            key = row_key(row)
            if not key:
                continue
            rule = match_rule(key, rules)
            if rule is None:
                continue
            if not await _label(
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
            if not await _label(
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


def _group_payees(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row_key(row)
        if not key:
            continue
        amount = abs(int(row["amount_paise"]))
        entry = groups.get(key)
        if entry is None:
            groups[key] = {
                "key": key,
                "count": 1,
                "total_paise": amount,
                "narration": str(row["description_raw"])[:200],
                "direction": "credit" if int(row["amount_paise"]) > 0 else "debit",
                "category_id": row["category_id"],
            }
        else:
            entry["count"] += 1
            entry["total_paise"] += amount
    return sorted(
        groups.values(),
        key=lambda item: (-item["count"], -item["total_paise"], item["key"]),
    )


async def pending_payees(conn: Connection, import_id: str) -> list[dict[str, Any]]:
    """Uncategorized payees in this import, most frequent first."""
    rows = [row for row in await _import_rows(conn, import_id) if row["category_id"] is None]
    return [
        {key: value for key, value in group.items() if key != "category_id"}
        for group in _group_payees(rows)
    ]


async def payee_samples(conn: Connection, import_id: str, keys: list[str]) -> list[dict[str, Any]]:
    """Sample narration and amount for any payee keys, including rows a rule covers."""
    wanted = {normalize_pattern(key) for key in keys}
    groups = _group_payees(await _import_rows(conn, import_id))
    return [group for group in groups if group["key"] in wanted]


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


async def check_payees(
    conn: Connection, import_id: str, count: int
) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Payees a rule already covers, without their category, for measuring agreement."""
    rows = await conn.fetch(
        """
        SELECT sl.description_raw, sl.match_detail, label.category_id, label.category_name,
               payee.display_name AS payee_name
        FROM statement_lines sl
        JOIN movements m ON m.id = sl.movement_id
        JOIN LATERAL (
            SELECT tl.category_id, c.name AS category_name
            FROM transfer_labels tl
            JOIN categories c ON c.id = tl.category_id
            WHERE tl.movement_id = m.id AND tl.category_id IS NOT NULL
            LIMIT 1
        ) label ON true
        LEFT JOIN LATERAL (
            SELECT p.display_name
            FROM transfer_labels tl
            JOIN payees p ON p.id = tl.payee_id
            WHERE tl.movement_id = m.id
            LIMIT 1
        ) payee ON true
        WHERE sl.import_id = $1 AND sl.match_state = 'merged' AND m.reverses_id IS NULL
        ORDER BY sl.date, sl.id
        """,
        import_id,
    )
    samples: list[dict[str, str]] = []
    known: dict[str, str] = {}
    seen: set[str] = set()
    for row in rows:
        if len(samples) >= max(0, count):
            break
        detail = dict(row)
        key = payee_key(detail.get("description_raw"), detail.get("payee_name"))
        if not key or key in seen:
            continue
        seen.add(key)
        samples.append({"key": key, "narration": str(row["description_raw"])[:200]})
        known[key] = str(row["category_name"])
    return samples, known


def agreement(answers: list[dict[str, Any]], known: dict[str, str]) -> tuple[float | None, int]:
    """Share of sampled answers that match the category the rule already gave."""
    checked = 0
    matched = 0
    for answer in answers:
        expected = known.get(str(answer.get("key") or ""))
        if expected is None:
            continue
        checked += 1
        if str(answer.get("category") or "") == expected:
            matched += 1
    if checked == 0:
        return None, 0
    return matched / checked, checked
