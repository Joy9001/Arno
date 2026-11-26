"""Dedupe and reconcile statement rows against rows already logged by chat, photo, or voice.

The outcomes are certain, ambiguous, or new. A certain match can be committed; an ambiguous
one waits for the review screen. Logged rows still unmatched after a statement that covers
their date are listed as "logged but not on the statement".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from arno.db.conn import Connection
from arno.importers.identifiers import find_upi_ref, scrub

CREDIT_KINDS = ("refund", "transfer", "income")
DUPLICATE_REASON = "content hash already imported"


def content_hash(day: str, amount_paise: int, description: str) -> str:
    normalized = " ".join(str(description or "").lower().split())
    return hashlib.sha256(f"{day}|{amount_paise}|{normalized}".encode()).hexdigest()


def credit_decision_required(line: dict[str, Any], account_kind: str | None) -> bool:
    """A card credit is never income by default; it needs an explicit user decision."""
    detail = line.get("match_detail") or {}
    if (
        account_kind != "card"
        or int(line.get("amount_paise") or 0) <= 0
        or line.get("match_state") in ("ignored", "certain", "merged")
        or line.get("movement_id")
        or detail.get("suggested_transfer_account_id")
    ):
        return False
    if detail.get("credit_kind") not in CREDIT_KINDS:
        return True
    return detail["credit_kind"] == "transfer" and not detail.get("credit_source_account_id")


async def insert_lines(
    conn: Connection,
    *,
    import_id: str,
    account: dict[str, Any],
    parsed_account: dict[str, Any],
) -> list[dict[str, Any]]:
    own_last4 = {account["last4"]} if account.get("last4") else set()
    rows = parsed_account.get("rows") or []
    if not rows:
        return []
    # Ids from one instant do not sort in insertion order, so the parser's own row order is
    # stored beside the line. The gate's replay and the transfer suggestions read it.
    ids = await conn.fetch("SELECT new_id('stl') AS id FROM generate_series(1, $1)", len(rows))
    batch: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for index, row in enumerate(rows):
        debit = int(row.get("debit") or 0)
        credit = int(row.get("credit") or 0)
        amount = credit - debit
        raw = str(row.get("description") or "")
        safe_raw, hashes = scrub(raw, own_last4=own_last4)
        upi_ref = row.get("upi_ref") or find_upi_ref(raw)
        day = row["date"]
        if not isinstance(day, date):
            day = date.fromisoformat(str(day))
        digest = content_hash(str(row["date"]), amount, safe_raw or "")
        # A statement can contain two identical lines. Reimports keep order, so a per-import
        # ordinal keeps them distinct without breaking the cross-import dedupe key.
        occurrence = seen.get(digest, 0)
        seen[digest] = occurrence + 1
        if occurrence:
            digest = content_hash(str(row["date"]), amount, f"{safe_raw or ''}#{occurrence}")
        detail: dict[str, Any] = {"identifier_hashes": sorted(hashes), "row": index}
        counterparty = str(row.get("counterparty") or "").strip()[:120]
        if counterparty:
            detail["counterparty"] = counterparty
        batch.append(
            {
                "id": ids[index]["id"],
                "date": day.isoformat(),
                "description_raw": safe_raw or "",
                "amount_paise": amount,
                "debit_paise": debit,
                "credit_paise": credit,
                "balance_paise": row.get("balance"),
                "upi_ref": upi_ref,
                "content_hash": digest,
                "match_detail": detail,
            }
        )
    created = await conn.fetch(
        """
        INSERT INTO statement_lines
            (id, import_id, date, description_raw, amount_paise, debit_paise,
             credit_paise, balance_paise, upi_ref, content_hash, match_state,
             outside_text, match_detail)
        SELECT b.id, $1, b.date, b.description_raw, b.amount_paise, b.debit_paise,
               b.credit_paise, b.balance_paise, b.upi_ref, b.content_hash, 'new', true,
               b.match_detail
        FROM jsonb_to_recordset($2::jsonb) AS b(
            id text, date date, description_raw text, amount_paise bigint, debit_paise bigint,
            credit_paise bigint, balance_paise bigint, upi_ref text, content_hash text,
            match_detail jsonb
        )
        ON CONFLICT (import_id, content_hash) DO NOTHING
        RETURNING *
        """,
        import_id,
        batch,
    )
    return sorted(created, key=lambda line: line["match_detail"]["row"])


@dataclass
class _Ledger:
    """What one import's lines match against, read once: no query per line."""

    imported: set[str]
    movements: list[dict[str, Any]]
    claimed: dict[str, set[str]]
    claim_of: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        self.claim_of = {line: m for m, lines in self.claimed.items() for line in lines}

    def claim(self, line_id: str, state: str, movement_id: str | None) -> None:
        """Follow a line's new state, so a later line sees what an earlier one took."""
        old = self.claim_of.pop(line_id, None)
        if old is not None:
            self.claimed[old].discard(line_id)
        if state in ("certain", "merged") and movement_id:
            self.claimed.setdefault(movement_id, set()).add(line_id)
            self.claim_of[line_id] = movement_id


async def _preload(
    conn: Connection, *, import_id: str, account: dict[str, Any], lines: list[dict[str, Any]]
) -> _Ledger:
    """Two bulk reads for the whole import, in place of two queries per line.

    The first is the content hashes the account already holds in committed or review imports,
    the second the effective movements on the account around the statement's dates, with the
    UPI references, payee hashes, and claiming lines the match needs.
    """
    if not lines:
        return _Ledger(imported=set(), movements=[], claimed={})
    imported = {
        str(row["content_hash"])
        for row in await conn.fetch(
            """
            SELECT DISTINCT sl.content_hash
            FROM statement_lines sl
            JOIN imports i ON i.id = sl.import_id
            WHERE sl.content_hash = ANY($1::text[])
              AND sl.import_id <> $2
              AND i.account_id IS NOT DISTINCT FROM $3
              AND i.status IN ('committed', 'review')
              AND sl.match_state NOT IN ('ignored')
            """,
            [line["content_hash"] for line in lines],
            import_id,
            account["id"],
        )
    }
    days = [_as_date(line["date"]) for line in lines]
    rows = await conn.fetch(
        """
        SELECT
            m.id,
            m.date,
            m.description,
            m.kind,
            sum(
                CASE
                    WHEN ml.from_account_id = $1 THEN -ml.amount_paise
                    WHEN ml.to_account_id = $1 THEN ml.amount_paise
                    ELSE 0
                END
            )::bigint AS net_paise,
            coalesce((
                SELECT array_agg(c.upi_ref ORDER BY c.id) FROM captures c
                WHERE c.movement_id = m.id AND c.upi_ref IS NOT NULL
            ), '{}') AS upi_refs,
            coalesce((
                SELECT array_agg(DISTINCT p.match_hash)
                FROM movement_legs l JOIN payees p ON p.id = l.payee_id
                WHERE l.movement_id = m.id AND p.match_hash IS NOT NULL
            ), '{}') AS match_hashes,
            coalesce((
                SELECT array_agg(sl.id) FROM statement_lines sl
                JOIN imports i ON i.id = sl.import_id
                WHERE sl.movement_id = m.id
                  AND sl.match_state IN ('certain', 'merged')
                  AND i.account_id = $4
            ), '{}') AS claimed_by
        FROM movements m
        JOIN movement_legs ml ON ml.movement_id = m.id
        WHERE m.date BETWEEN $2 AND $3
          AND (ml.from_account_id = $1 OR ml.to_account_id = $1)
        GROUP BY m.id, m.date, m.description, m.kind
        ORDER BY m.date, m.id
        """,
        account["id"],
        min(days) - timedelta(days=3),
        max(days) + timedelta(days=3),
        account["id"],
    )
    return _Ledger(
        imported=imported,
        movements=rows,
        claimed={str(row["id"]): set(row["claimed_by"]) for row in rows},
    )


def _as_date(day: Any) -> date:
    return day if isinstance(day, date) else date.fromisoformat(str(day))


def _candidates(pre: _Ledger, *, line: dict[str, Any], used: set[str]) -> list[dict[str, Any]]:
    day = _as_date(line["date"])
    candidates: list[dict[str, Any]] = []
    for row in pre.movements:
        movement_id = str(row["id"])
        if (
            abs((row["date"] - day).days) > 3
            or movement_id in used
            or pre.claimed.get(movement_id, set()) - {line["id"]}
        ):
            continue
        upi_refs = [str(ref) for ref in row["upi_refs"]]
        candidates.append(
            {
                "movement_id": movement_id,
                "date": row["date"].isoformat(),
                "description": row["description"],
                "kind": row["kind"],
                "amount_paise": abs(int(row["net_paise"])),
                "net_paise": int(row["net_paise"]),
                "upi_refs": upi_refs,
                "upi_match": bool(line.get("upi_ref") and line["upi_ref"] in upi_refs),
                "match_hashes": [str(item) for item in row["match_hashes"]],
            }
        )
    return candidates


def _settle_line(pre: _Ledger, *, line: dict[str, Any], used: set[str]) -> dict[str, Any]:
    """One line's match state: its detail, and the movement it matched, if any."""
    if line["content_hash"] in pre.imported:
        return {
            "state": "ignored",
            "movement_id": None,
            "detail": {"reason": DUPLICATE_REASON},
        }
    candidates = _candidates(pre, line=line, used=used)
    want_debit = int(line["amount_paise"]) < 0
    line_hashes = set((line.get("match_detail") or {}).get("identifier_hashes") or [])
    exact = [
        candidate
        for candidate in candidates
        if candidate["amount_paise"] == abs(line["amount_paise"])
        and (candidate["net_paise"] < 0) == want_debit
    ]
    upi = [candidate for candidate in exact if candidate["upi_match"]]
    by_hash = [
        candidate
        for candidate in exact
        if line_hashes and line_hashes.intersection(candidate["match_hashes"])
    ]
    chosen = None
    state = "new"
    detail: dict[str, Any] = dict(line.get("match_detail") or {})
    detail["candidates"] = candidates
    detail["identifier_hashes"] = sorted(line_hashes)
    if len(upi) == 1:
        chosen = upi[0]
        state = "certain"
        detail["rule"] = "upi_ref"
    elif len(by_hash) == 1:
        chosen = by_hash[0]
        state = "certain"
        detail["rule"] = "identifier-hash"
    elif len(exact) == 1:
        chosen = exact[0]
        state = "certain"
        detail["rule"] = "amount-account-date"
    elif len(exact) > 1:
        state = "ambiguous"
        detail["rule"] = "several exact candidates"
        detail["candidates"] = exact
    else:
        tolerance = max(100, min(5000, abs(line["amount_paise"]) // 200))
        close = [
            candidate
            for candidate in candidates
            if (candidate["net_paise"] < 0) == want_debit
            and abs(candidate["amount_paise"] - abs(line["amount_paise"])) <= tolerance
        ]
        if close:
            state = "ambiguous"
            detail["rule"] = "amount within tolerance"
            detail["tolerance_paise"] = tolerance
            detail["candidates"] = close
    return {
        "state": state,
        "movement_id": chosen["movement_id"] if chosen else None,
        "detail": detail,
    }


async def reconcile(
    conn: Connection,
    *,
    import_id: str,
    account: dict[str, Any],
    period: tuple[date, date] | None,
) -> dict[str, Any]:
    lines = await conn.fetch(
        "SELECT * FROM statement_lines WHERE import_id = $1 ORDER BY date, id",
        import_id,
    )
    pre = await _preload(conn, import_id=import_id, account=account, lines=lines)
    used: set[str] = set()
    counts = {"new": 0, "certain": 0, "ambiguous": 0, "ignored": 0}
    ambiguous: list[dict[str, Any]] = []
    matched_ids: set[str] = set()
    updates: list[tuple[str, str, str | None, dict[str, Any]]] = []

    for line in lines:
        settled = _settle_line(pre, line=line, used=used)
        state = settled["state"]
        updates.append((line["id"], state, settled["movement_id"], settled["detail"]))
        pre.claim(line["id"], state, settled["movement_id"])
        # What the lines now hold: the movement each one took, and every candidate any detail
        # lists (a duplicate's detail keeps the candidates an earlier run stored).
        if settled["movement_id"]:
            matched_ids.add(str(settled["movement_id"]))
        stored = {**(line.get("match_detail") or {}), **settled["detail"]}
        for candidate in stored.get("candidates") or []:
            if candidate.get("movement_id"):
                matched_ids.add(str(candidate["movement_id"]))
        if state == "certain":
            used.add(settled["movement_id"])
            counts["certain"] += 1
        elif state == "ambiguous":
            counts["ambiguous"] += 1
            ambiguous.append(
                {
                    "line_id": line["id"],
                    "date": line["date"].isoformat(),
                    "description": line["description_raw"],
                    "amount_paise": int(line["amount_paise"]),
                    "candidates": settled["detail"]["candidates"],
                }
            )
        elif state == "ignored":
            counts["ignored"] += 1
        else:
            counts["new"] += 1
    await _set_matches(conn, updates)

    logged_not_on_statement = await _logged_not_on_statement(
        conn,
        account=account,
        period=period,
        matched=matched_ids,
    )
    counts["logged_not_on_statement"] = len(logged_not_on_statement)
    review = {
        "ambiguous": ambiguous,
        "logged_not_on_statement": logged_not_on_statement,
        "period": [period[0].isoformat(), period[1].isoformat()] if period else None,
    }
    # Merge: the review also carries what the user answered (a stated opening) and, after a
    # commit, the account's before-state and the adjustment. Re-checking rows must not lose it.
    await conn.execute(
        """
        UPDATE imports
        SET counts = $1, review = coalesce(review, '{}'::jsonb) || $2::jsonb
        WHERE id = $3
        """,
        counts,
        review,
        import_id,
    )
    return review


async def rematch_new_lines(conn: Connection, import_id: str) -> dict[str, int]:
    """Re-run the match for lines still `new`, so a row logged after the review merges.

    The review is a snapshot. A movement logged in chat, by voice, or from a photo while the
    review sat there is money the statement also prints, so booking the line as new would count
    it twice and the closing adjustment would hide the gap. The rules are the review's own. A
    line the user resolved themselves keeps their answer, and a line that now matches two
    candidates goes back to review.
    """
    counts = {"certain": 0, "ambiguous": 0, "ignored": 0}
    async with conn.transaction():
        row = await conn.fetchrow(
            "SELECT status, account_id FROM imports WHERE id = $1 FOR UPDATE", import_id
        )
        if row is None or row["status"] != "review" or row["account_id"] is None:
            return counts
        account = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", row["account_id"])
        lines = await conn.fetch(
            """
            SELECT * FROM statement_lines
            WHERE import_id = $1 AND match_state = 'new' AND movement_id IS NULL
            ORDER BY date, id
            """,
            import_id,
        )
        if not lines or account is None:
            return counts
        used = {
            str(found["movement_id"])
            for found in await conn.fetch(
                "SELECT movement_id FROM statement_lines "
                "WHERE import_id = $1 AND movement_id IS NOT NULL",
                import_id,
            )
        }
        pre = await _preload(conn, import_id=import_id, account=account, lines=lines)
        updates: list[tuple[str, str, str | None, dict[str, Any]]] = []
        for line in lines:
            if (line["match_detail"] or {}).get("resolved_by") == "user":
                continue
            settled = _settle_line(pre, line=line, used=used)
            state = settled["state"]
            if state == "new":
                continue
            if settled["movement_id"]:
                used.add(settled["movement_id"])
            updates.append((line["id"], state, settled["movement_id"], settled["detail"]))
            pre.claim(line["id"], state, settled["movement_id"])
            counts[state] += 1
        await _set_matches(conn, updates)
    if any(counts.values()):
        await _refresh_counts(conn, import_id)
    return counts


async def release_suppressed_duplicates(conn: Connection, *, account_id: str | None) -> list[str]:
    """Re-match rows another import's duplicate rule hid, now that the import is gone.

    A line is `ignored` as a duplicate only against another import of the same account that is
    committed or in review. When that import is rejected or undone, the rows it suppressed
    belong to their own import again. Only the suppressed rows are re-settled, so a decision
    the user already made on another line stays as it is.
    """
    if account_id is None:
        return []
    account = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", account_id)
    if account is None:
        return []
    rows = await conn.fetch(
        """
        SELECT DISTINCT i.id
        FROM imports i
        JOIN statement_lines sl ON sl.import_id = i.id
        WHERE i.account_id = $1
          AND i.status IN ('parsing', 'needs_password', 'needs_input', 'paused', 'review',
                           'failed')
          AND sl.match_state = 'ignored'
          AND sl.match_detail ->> 'reason' = $2
        """,
        account_id,
        DUPLICATE_REASON,
    )
    if not rows:
        return []
    released: list[str] = []
    for row in rows:
        import_id = str(row["id"])
        lines = await conn.fetch(
            "SELECT * FROM statement_lines WHERE import_id = $1 ORDER BY date, id",
            import_id,
        )
        # Read per import: the previous one's released rows now count as imported for this one.
        pre = await _preload(conn, import_id=import_id, account=account, lines=lines)
        used = {str(line["movement_id"]) for line in lines if line.get("movement_id")}
        updates: list[tuple[str, str, str | None, dict[str, Any]]] = []
        for line in lines:
            if line["match_state"] != "ignored":
                continue
            detail = line.get("match_detail") or {}
            if detail.get("resolved_by") or detail.get("reason") != DUPLICATE_REASON:
                continue
            settled = _settle_line(pre, line=line, used=used)
            if settled["state"] == "ignored":
                continue
            if settled["movement_id"]:
                used.add(settled["movement_id"])
            updates.append(
                (line["id"], settled["state"], settled["movement_id"], settled["detail"])
            )
            pre.claim(line["id"], settled["state"], settled["movement_id"])
        if updates:
            await _set_matches(conn, updates)
            await _refresh_counts(conn, import_id)
            released.append(import_id)
    return released


async def _refresh_counts(conn: Connection, import_id: str) -> None:
    """Recount an import's lines after a release."""
    current = await conn.fetchrow_required("SELECT counts FROM imports WHERE id = $1", import_id)
    counts = {
        key: 0 for key in ("new", "certain", "ambiguous", "ignored", "logged_not_on_statement")
    }
    counts.update(dict(current["counts"] or {}))
    rows = await conn.fetch(
        """
        SELECT match_state, count(*)::int AS n
        FROM statement_lines WHERE import_id = $1 GROUP BY match_state
        """,
        import_id,
    )
    for row in rows:
        counts[row["match_state"]] = int(row["n"])
    await conn.execute(
        """
        UPDATE imports
        SET counts = $1
        WHERE id = $2
        """,
        counts,
        import_id,
    )


async def _set_matches(
    conn: Connection, updates: list[tuple[str, str, str | None, dict[str, Any]]]
) -> None:
    """Write many lines' match state, movement, and merged detail in one statement."""
    if not updates:
        return
    await conn.execute(
        """
        UPDATE statement_lines sl
        SET match_state = u.state, movement_id = u.movement_id,
            match_detail = coalesce(sl.match_detail, '{}'::jsonb) || u.detail,
            updated_at = now()
        FROM jsonb_to_recordset($1::jsonb) AS u(id text, state text, movement_id text, detail jsonb)
        WHERE sl.id = u.id
        """,
        [
            {"id": line_id, "state": state, "movement_id": movement_id, "detail": detail}
            for line_id, state, movement_id, detail in updates
        ],
    )


async def _logged_not_on_statement(
    conn: Connection,
    *,
    account: dict[str, Any],
    period: tuple[date, date] | None,
    matched: set[str],
) -> list[dict[str, Any]]:
    if period is None:
        return []
    rows = await conn.fetch(
        """
        SELECT
            m.id,
            m.date,
            m.description,
            abs(sum(
                CASE
                    WHEN ml.from_account_id = $1 THEN ml.amount_paise
                    WHEN ml.to_account_id = $1 THEN -ml.amount_paise
                    ELSE 0
                END
            ))::bigint AS amount_paise
        FROM movements m
        JOIN movement_legs ml ON ml.movement_id = m.id
        WHERE m.date BETWEEN $2 AND $3
          AND (ml.from_account_id = $1 OR ml.to_account_id = $1)
          AND EXISTS (
              SELECT 1 FROM captures c
              WHERE c.movement_id = m.id AND c.kind IN ('chat', 'photo', 'voice')
          )
          AND NOT EXISTS (
              SELECT 1 FROM captures c
              WHERE c.movement_id = m.id AND c.kind = 'statement_line'
          )
        GROUP BY m.id, m.date, m.description
        ORDER BY m.date, m.id
        """,
        account["id"],
        period[0],
        period[1],
    )
    return [
        {
            "movement_id": str(row["id"]),
            "date": row["date"].isoformat(),
            "description": row["description"],
            "amount_paise": int(row["amount_paise"]),
        }
        for row in rows
        if str(row["id"]) not in matched
    ]


async def resolve_line(
    conn: Connection,
    *,
    import_id: str,
    line_id: str,
    resolution: str,
    movement_id: str | None = None,
    credit_kind: str | None = None,
    category_id: str | None = None,
    source_account_id: str | None = None,
) -> dict[str, Any]:
    line = await conn.fetchrow(
        "SELECT * FROM statement_lines WHERE id = $1 AND import_id = $2",
        line_id,
        import_id,
    )
    if line is None:
        raise ValueError("No such statement line")
    if resolution == "ignore":
        await _set_matches(conn, [(line_id, "ignored", None, {"resolved_by": "user"})])
    elif resolution == "new":
        await _set_matches(conn, [(line_id, "new", None, {"resolved_by": "user"})])
    elif resolution == "merge":
        if not movement_id:
            raise ValueError("merge needs a movement_id")
        candidate = await conn.fetchrow("SELECT id FROM movements WHERE id = $1", movement_id)
        if candidate is None:
            raise ValueError("No such movement")
        await _set_matches(conn, [(line_id, "certain", movement_id, {"resolved_by": "user"})])
    elif resolution == "confirm_transfer":
        if not (line["match_detail"] or {}).get("suggested_transfer_account_id"):
            raise ValueError("That line is not a suggested transfer")
        # A line the statement matched already has its record. Confirming the own-account move
        # keeps that record; dropping the match here would book a second transfer at commit and
        # the closing adjustment would hide the duplicate.
        state = line["match_state"] if line.get("movement_id") else "new"
        await _set_matches(
            conn, [(line_id, state, line["movement_id"], {"transfer_confirmed": True})]
        )
    elif resolution == "reject_transfer":
        # Rejecting the suggestion keeps whatever the statement already matched: only the
        # suggestion goes, so the line still merges into its one record at commit.
        await conn.execute(
            """
            UPDATE statement_lines
            SET match_state = CASE WHEN movement_id IS NULL THEN 'new' ELSE match_state END,
                match_detail = (coalesce(match_detail, '{}'::jsonb)
                                - 'suggested_transfer_account_id') || $2::jsonb,
                updated_at = now()
            WHERE id = $1
            """,
            line_id,
            {"transfer_rejected": True},
        )
    elif resolution == "classify_credit":
        await _classify_credit(
            conn,
            import_id=import_id,
            line=line,
            credit_kind=credit_kind,
            category_id=category_id,
            source_account_id=source_account_id,
        )
    else:
        raise ValueError(
            "resolution is ignore, new, merge, confirm_transfer, reject_transfer, "
            "or classify_credit"
        )
    updated = await conn.fetchrow("SELECT * FROM statement_lines WHERE id = $1", line_id)
    if updated is None:
        raise ValueError("No such statement line")
    return updated


async def _classify_credit(
    conn: Connection,
    *,
    import_id: str,
    line: dict[str, Any],
    credit_kind: str | None,
    category_id: str | None,
    source_account_id: str | None,
) -> None:
    if line["match_state"] != "new" or line.get("movement_id"):
        raise ValueError("Only an unmatched new line can be classified")
    if int(line["amount_paise"]) <= 0:
        raise ValueError("Only a credit line can be classified")
    account = await conn.fetchrow(
        """
        SELECT a.id, a.kind FROM imports i JOIN accounts a ON a.id = i.account_id
        WHERE i.id = $1
        """,
        import_id,
    )
    if account is None or account["kind"] != "card":
        raise ValueError("Only a card credit needs this classification")
    if credit_kind not in CREDIT_KINDS:
        raise ValueError("credit_kind must be refund, transfer, or income")
    changes: dict[str, Any] = {"credit_kind": credit_kind, "credit_classified_by": "user"}
    if credit_kind == "refund":
        chosen = str(category_id or "").strip()
        if chosen:
            category = await conn.fetchrow(
                "SELECT id FROM categories WHERE id = $1 AND active", chosen
            )
            if category is None:
                raise ValueError("No such category")
            changes["credit_category_id"] = str(category["id"])
    elif credit_kind == "transfer":
        source_id = str(source_account_id or "").strip()
        if not source_id:
            raise ValueError("A card repayment needs the account that paid it")
        if source_id == str(account["id"]):
            raise ValueError("Choose the account that paid the card, not the card itself")
        source = await conn.fetchrow(
            """
            SELECT id FROM accounts
            WHERE id = $1 AND active AND kind IN ('bank', 'cash', 'wallet')
            """,
            source_id,
        )
        if source is None:
            raise ValueError("Choose an active bank, cash, or wallet account")
        changes["credit_source_account_id"] = str(source["id"])
    await conn.execute(
        """
        UPDATE statement_lines
        SET match_state = 'new', movement_id = NULL,
            match_detail = ((coalesce(match_detail, '{}'::jsonb)
                            - 'credit_source_account_id' - 'credit_category_id') || $1::jsonb),
            updated_at = now()
        WHERE id = $2
        """,
        changes,
        line["id"],
    )


__all__ = [
    "DUPLICATE_REASON",
    "content_hash",
    "credit_decision_required",
    "insert_lines",
    "reconcile",
    "release_suppressed_duplicates",
    "resolve_line",
]
