"""Dedupe and reconcile statement rows against rows already logged by chat, photo, or voice.

The outcomes are certain, ambiguous, or new. A certain match can be committed; an ambiguous
one waits for the review screen. Logged rows still unmatched after a statement that covers
their date are listed as "logged but not on the statement".
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any

from fin_agent.db.conn import Connection
from fin_agent.importers.identifiers import find_upi_ref, scrub
from fin_agent.ledger.audit import new_id

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
    inserted: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for index, row in enumerate(parsed_account.get("rows") or []):
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
        row_id = await new_id(conn, "stl")
        # Ids from one instant do not sort in insertion order, so the parser's own row order is
        # stored beside the line. The gate's replay and the transfer suggestions read it.
        detail = {"identifier_hashes": sorted(hashes), "row": index}
        counterparty = str(row.get("counterparty") or "").strip()[:120]
        if counterparty:
            detail["counterparty"] = counterparty
        created = await conn.fetchrow(
            """
            INSERT INTO statement_lines
                (id, import_id, date, description_raw, amount_paise, debit_paise,
                 credit_paise, balance_paise, upi_ref, content_hash, match_state,
                 outside_text, match_detail)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'new', true, $11)
            ON CONFLICT (import_id, content_hash) DO NOTHING
            RETURNING *
            """,
            row_id,
            import_id,
            day,
            safe_raw or "",
            amount,
            debit,
            credit,
            row.get("balance"),
            upi_ref,
            digest,
            detail,
        )
        if created is not None:
            inserted.append(dict(created))
    return inserted


async def _duplicate_exists(conn: Connection, line: dict[str, Any], account_id: str | None) -> bool:
    """A row already imported for the same account. Another account keeps its own copy."""
    row = await conn.fetchrow(
        """
        SELECT 1
        FROM statement_lines sl
        JOIN imports i ON i.id = sl.import_id
        WHERE sl.content_hash = $1
          AND sl.import_id <> $2
          AND i.account_id IS NOT DISTINCT FROM $3
          AND i.status IN ('committed', 'review')
          AND sl.match_state NOT IN ('ignored')
        LIMIT 1
        """,
        line["content_hash"],
        line["import_id"],
        account_id,
    )
    return row is not None


async def _candidates(
    conn: Connection,
    *,
    line: dict[str, Any],
    account: dict[str, Any],
    used: set[str],
    hidden: set[str],
) -> list[dict[str, Any]]:
    day = line["date"]
    if not isinstance(day, date):
        day = date.fromisoformat(str(day))
    ledger_id = account["ledger_account_id"]
    rows = await conn.fetch(
        """
        SELECT
            m.id,
            m.date,
            m.description,
            m.kind,
            sum(
                CASE
                    WHEN t.from_account_id = $1 THEN -t.amount
                    WHEN t.to_account_id = $2 THEN t.amount
                    ELSE 0
                END
            )::bigint AS net_paise
        FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        WHERE m.kind <> 'reversal'
          AND m.reverses_id IS NULL
          AND m.date BETWEEN $3 AND $4
          AND (t.from_account_id = $5 OR t.to_account_id = $6)
          AND NOT EXISTS (
              SELECT 1 FROM statement_lines sl
              JOIN imports i ON i.id = sl.import_id
              WHERE sl.movement_id = m.id
                AND sl.id <> $7
                AND sl.match_state IN ('certain', 'merged')
                AND i.account_id = $8
          )
        GROUP BY m.id, m.date, m.description, m.kind
        ORDER BY m.date, m.id
        """,
        ledger_id,
        ledger_id,
        day - timedelta(days=3),
        day + timedelta(days=3),
        ledger_id,
        ledger_id,
        line["id"],
        account["id"],
    )
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if str(row["id"]) in used or str(row["id"]) in hidden:
            continue
        captures = await conn.fetch(
            "SELECT upi_ref FROM captures WHERE movement_id = $1 AND upi_ref IS NOT NULL",
            row["id"],
        )
        upi_refs = [str(capture["upi_ref"]) for capture in captures]
        payee_hashes = [
            str(payee["match_hash"])
            for payee in await conn.fetch(
                """
                SELECT DISTINCT p.match_hash
                FROM transfer_labels tl
                JOIN payees p ON p.id = tl.payee_id
                WHERE tl.movement_id = $1 AND p.match_hash IS NOT NULL
                """,
                row["id"],
            )
        ]
        candidates.append(
            {
                "movement_id": str(row["id"]),
                "date": row["date"].isoformat(),
                "description": row["description"],
                "kind": row["kind"],
                "amount_paise": abs(int(row["net_paise"])),
                "net_paise": int(row["net_paise"]),
                "upi_refs": upi_refs,
                "upi_match": bool(line.get("upi_ref") and line["upi_ref"] in upi_refs),
                "match_hashes": payee_hashes,
            }
        )
    return candidates


async def _settle_line(
    conn: Connection,
    *,
    line: dict[str, Any],
    account: dict[str, Any],
    used: set[str],
    hidden: set[str],
) -> dict[str, Any]:
    """One line's match state: its detail, and the movement it matched, if any."""
    if await _duplicate_exists(conn, line, account["id"]):
        return {
            "state": "ignored",
            "movement_id": None,
            "detail": {"reason": DUPLICATE_REASON},
        }
    candidates = await _candidates(conn, line=line, account=account, used=used, hidden=hidden)
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
    # An undone movement is not money the statement can match: merging into it would hide the
    # row behind a reversal and let the closing adjustment absorb the difference.
    from fin_agent.ledger.movements import _effectively_reversed_ids

    hidden = set(await _effectively_reversed_ids(conn))
    used: set[str] = set()
    counts = {"new": 0, "certain": 0, "ambiguous": 0, "ignored": 0}
    ambiguous: list[dict[str, Any]] = []

    for line in lines:
        settled = await _settle_line(conn, line=line, account=account, used=used, hidden=hidden)
        state = settled["state"]
        await _set_match(conn, line["id"], state, settled["movement_id"], settled["detail"])
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

    lines = await conn.fetch(
        "SELECT * FROM statement_lines WHERE import_id = $1 ORDER BY date, id",
        import_id,
    )
    matched_ids = {str(line["movement_id"]) for line in lines if line.get("movement_id")}
    for line in lines:
        for candidate in (line.get("match_detail") or {}).get("candidates") or []:
            if candidate.get("movement_id"):
                matched_ids.add(str(candidate["movement_id"]))
    logged_not_on_statement = await _logged_not_on_statement(
        conn,
        account=account,
        period=period,
        matched=matched_ids,
        hidden=hidden,
    )
    counts["logged_not_on_statement"] = len(logged_not_on_statement)
    review = {
        "counts": counts,
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
    from fin_agent.ledger.movements import _effectively_reversed_ids

    hidden = set(await _effectively_reversed_ids(conn))
    released: list[str] = []
    for row in rows:
        import_id = str(row["id"])
        lines = await conn.fetch(
            "SELECT * FROM statement_lines WHERE import_id = $1 ORDER BY date, id",
            import_id,
        )
        used = {str(line["movement_id"]) for line in lines if line.get("movement_id")}
        changed = False
        for line in lines:
            if line["match_state"] != "ignored":
                continue
            detail = line.get("match_detail") or {}
            if detail.get("resolved_by") or detail.get("reason") != DUPLICATE_REASON:
                continue
            settled = await _settle_line(conn, line=line, account=account, used=used, hidden=hidden)
            if settled["state"] == "ignored":
                continue
            changed = True
            if settled["movement_id"]:
                used.add(settled["movement_id"])
            await _set_match(
                conn,
                line["id"],
                settled["state"],
                settled["movement_id"],
                settled["detail"],
            )
        if changed:
            await _refresh_counts(conn, import_id)
            released.append(import_id)
    return released


async def _refresh_counts(conn: Connection, import_id: str) -> None:
    """Recount an import's lines after a release, keeping its other review facts."""
    current = await conn.fetchrow("SELECT counts FROM imports WHERE id = $1", import_id)
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
        SET counts = $1,
            review = coalesce(review, '{}'::jsonb) || jsonb_build_object('counts', $1::jsonb)
        WHERE id = $2
        """,
        counts,
        import_id,
    )


async def _set_match(
    conn: Connection,
    line_id: str,
    state: str,
    movement_id: str | None,
    detail: dict[str, Any],
) -> None:
    await conn.execute(
        """
        UPDATE statement_lines
        SET match_state = $1, movement_id = $2,
            match_detail = coalesce(match_detail, '{}'::jsonb) || $3::jsonb,
            updated_at = now()
        WHERE id = $4
        """,
        state,
        movement_id,
        detail,
        line_id,
    )


async def _logged_not_on_statement(
    conn: Connection,
    *,
    account: dict[str, Any],
    period: tuple[date, date] | None,
    matched: set[str],
    hidden: set[str] | None = None,
) -> list[dict[str, Any]]:
    if period is None:
        return []
    skip = hidden or set()
    ledger_id = account["ledger_account_id"]
    rows = await conn.fetch(
        """
        SELECT
            m.id,
            m.date,
            m.description,
            abs(sum(
                CASE
                    WHEN t.from_account_id = $1 THEN t.amount
                    WHEN t.to_account_id = $2 THEN -t.amount
                    ELSE 0
                END
            ))::bigint AS amount_paise
        FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        WHERE m.kind <> 'reversal'
          AND m.reverses_id IS NULL
          AND m.date BETWEEN $3 AND $4
          AND (t.from_account_id = $5 OR t.to_account_id = $6)
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
        ledger_id,
        ledger_id,
        period[0],
        period[1],
        ledger_id,
        ledger_id,
    )
    return [
        {
            "movement_id": str(row["id"]),
            "date": row["date"].isoformat(),
            "description": row["description"],
            "amount_paise": int(row["amount_paise"]),
        }
        for row in rows
        if str(row["id"]) not in matched and str(row["id"]) not in skip
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
        await _set_match(conn, line_id, "ignored", None, {"resolved_by": "user"})
    elif resolution == "new":
        await _set_match(conn, line_id, "new", None, {"resolved_by": "user"})
    elif resolution == "merge":
        if not movement_id:
            raise ValueError("merge needs a movement_id")
        candidate = await conn.fetchrow("SELECT id FROM movements WHERE id = $1", movement_id)
        if candidate is None:
            raise ValueError("No such movement")
        await _set_match(conn, line_id, "certain", movement_id, {"resolved_by": "user"})
    elif resolution == "confirm_transfer":
        if not (line["match_detail"] or {}).get("suggested_transfer_account_id"):
            raise ValueError("That line is not a suggested transfer")
        await _set_match(conn, line_id, "new", None, {"transfer_confirmed": True})
    elif resolution == "reject_transfer":
        await conn.execute(
            """
            UPDATE statement_lines
            SET match_state = 'new',
                match_detail = (coalesce(match_detail, '{}'::jsonb)
                                - 'suggested_transfer_account_id'),
                updated_at = now()
            WHERE id = $1
            """,
            line_id,
        )
        await _set_match(conn, line_id, "new", None, {"transfer_rejected": True})
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
    return await conn.fetchrow("SELECT * FROM statement_lines WHERE id = $1", line_id)


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
