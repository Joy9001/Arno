"""Dedupe and reconcile statement rows against rows already logged by chat, photo, or voice.

The outcomes are certain, ambiguous, or new. A certain match can be committed; an ambiguous
one waits for the review screen. Logged rows still unmatched after a statement that covers
their date are listed as "logged but not on the statement".
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from fin_agent.importers.identifiers import find_upi_ref, scrub
from fin_agent.ledger.audit import new_id


def content_hash(day: str, amount_paise: int, description: str) -> str:
    normalized = " ".join(str(description or "").lower().split())
    return hashlib.sha256(f"{day}|{amount_paise}|{normalized}".encode()).hexdigest()


def insert_lines(
    conn: psycopg.Connection,
    *,
    import_id: str,
    account: dict[str, Any],
    parsed_account: dict[str, Any],
) -> list[dict[str, Any]]:
    own_last4 = {account["last4"]} if account.get("last4") else set()
    inserted: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for row in parsed_account.get("rows") or []:
        debit = int(row.get("debit") or 0)
        credit = int(row.get("credit") or 0)
        amount = credit - debit
        raw = str(row.get("description") or "")
        safe_raw, hashes = scrub(raw, own_last4=own_last4)
        upi_ref = row.get("upi_ref") or find_upi_ref(raw)
        digest = content_hash(str(row["date"]), amount, safe_raw or "")
        # A statement can contain two identical lines. Reimports keep order, so a per-import
        # ordinal keeps them distinct without breaking the cross-import dedupe key.
        occurrence = seen.get(digest, 0)
        seen[digest] = occurrence + 1
        if occurrence:
            digest = content_hash(str(row["date"]), amount, f"{safe_raw or ''}#{occurrence}")
        row_id = new_id(conn, "stl")
        detail = {"identifier_hashes": sorted(hashes)}
        counterparty = str(row.get("counterparty") or "").strip()[:120]
        if counterparty:
            detail["counterparty"] = counterparty
        created = conn.execute(
            """
            INSERT INTO statement_lines
                (id, import_id, date, description_raw, amount_paise, debit_paise,
                 credit_paise, balance_paise, upi_ref, content_hash, match_state,
                 outside_text, match_detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'new', true, %s)
            ON CONFLICT (import_id, content_hash) DO NOTHING
            RETURNING *
            """,
            (
                row_id,
                import_id,
                row["date"],
                safe_raw or "",
                amount,
                debit,
                credit,
                row.get("balance"),
                upi_ref,
                digest,
                Jsonb(detail),
            ),
        ).fetchone()
        if created is not None:
            inserted.append(dict(created))
    return inserted


def _duplicate_exists(conn: psycopg.Connection, line: dict[str, Any]) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM statement_lines sl
        JOIN imports i ON i.id = sl.import_id
        WHERE sl.content_hash = %s
          AND sl.import_id <> %s
          AND i.status IN ('committed', 'review')
          AND sl.match_state NOT IN ('ignored')
        LIMIT 1
        """,
        (line["content_hash"], line["import_id"]),
    ).fetchone()
    return row is not None


def _candidates(
    conn: psycopg.Connection,
    *,
    line: dict[str, Any],
    account: dict[str, Any],
    used: set[str],
) -> list[dict[str, Any]]:
    day = line["date"]
    if not isinstance(day, date):
        day = date.fromisoformat(str(day))
    ledger_id = account["ledger_account_id"]
    rows = conn.execute(
        """
        SELECT
            m.id,
            m.date,
            m.description,
            m.kind,
            sum(
                CASE
                    WHEN t.from_account_id = %s THEN -t.amount
                    WHEN t.to_account_id = %s THEN t.amount
                    ELSE 0
                END
            )::bigint AS net_paise
        FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        WHERE m.kind <> 'reversal'
          AND m.reverses_id IS NULL
          AND m.date BETWEEN %s AND %s
          AND (t.from_account_id = %s OR t.to_account_id = %s)
          AND NOT EXISTS (
              SELECT 1 FROM statement_lines sl
              JOIN imports i ON i.id = sl.import_id
              WHERE sl.movement_id = m.id
                AND sl.id <> %s
                AND sl.match_state IN ('certain', 'merged')
                AND i.account_id = %s
          )
        GROUP BY m.id, m.date, m.description, m.kind
        ORDER BY m.date, m.id
        """,
        (
            ledger_id,
            ledger_id,
            day - timedelta(days=3),
            day + timedelta(days=3),
            ledger_id,
            ledger_id,
            line["id"],
            account["id"],
        ),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if str(row["id"]) in used:
            continue
        captures = conn.execute(
            "SELECT upi_ref FROM captures WHERE movement_id = %s AND upi_ref IS NOT NULL",
            (row["id"],),
        ).fetchall()
        upi_refs = [str(capture["upi_ref"]) for capture in captures]
        payee_hashes = [
            str(payee["match_hash"])
            for payee in conn.execute(
                """
                SELECT DISTINCT p.match_hash
                FROM transfer_labels tl
                JOIN payees p ON p.id = tl.payee_id
                WHERE tl.movement_id = %s AND p.match_hash IS NOT NULL
                """,
                (row["id"],),
            ).fetchall()
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


def reconcile(
    conn: psycopg.Connection,
    *,
    import_id: str,
    account: dict[str, Any],
    period: tuple[date, date] | None,
) -> dict[str, Any]:
    lines = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM statement_lines WHERE import_id = %s ORDER BY date, id",
            (import_id,),
        ).fetchall()
    ]
    used: set[str] = set()
    counts = {"new": 0, "certain": 0, "ambiguous": 0, "ignored": 0}
    ambiguous: list[dict[str, Any]] = []

    for line in lines:
        if _duplicate_exists(conn, line):
            _set_match(
                conn, line["id"], "ignored", None, {"reason": "content hash already imported"}
            )
            counts["ignored"] += 1
            continue
        candidates = _candidates(conn, line=line, account=account, used=used)
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
        if chosen:
            used.add(chosen["movement_id"])
            _set_match(conn, line["id"], state, chosen["movement_id"], detail)
            counts["certain"] += 1
        elif state == "ambiguous":
            _set_match(conn, line["id"], state, None, detail)
            counts["ambiguous"] += 1
            ambiguous.append(
                {
                    "line_id": line["id"],
                    "date": line["date"].isoformat(),
                    "description": line["description_raw"],
                    "amount_paise": int(line["amount_paise"]),
                    "candidates": detail["candidates"],
                }
            )
        else:
            _set_match(conn, line["id"], "new", None, detail)
            counts["new"] += 1

    lines = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM statement_lines WHERE import_id = %s ORDER BY date, id",
            (import_id,),
        ).fetchall()
    ]
    matched_ids = {str(line["movement_id"]) for line in lines if line.get("movement_id")}
    for line in lines:
        for candidate in (line.get("match_detail") or {}).get("candidates") or []:
            if candidate.get("movement_id"):
                matched_ids.add(str(candidate["movement_id"]))
    logged_not_on_statement = _logged_not_on_statement(
        conn,
        account=account,
        period=period,
        matched=matched_ids,
    )
    counts["logged_not_on_statement"] = len(logged_not_on_statement)
    review = {
        "counts": counts,
        "ambiguous": ambiguous,
        "logged_not_on_statement": logged_not_on_statement,
        "period": [period[0].isoformat(), period[1].isoformat()] if period else None,
    }
    conn.execute(
        "UPDATE imports SET counts = %s, review = %s WHERE id = %s",
        (Jsonb(counts), Jsonb(review), import_id),
    )
    return review


def _set_match(
    conn: psycopg.Connection,
    line_id: str,
    state: str,
    movement_id: str | None,
    detail: dict[str, Any],
) -> None:
    conn.execute(
        """
        UPDATE statement_lines
        SET match_state = %s, movement_id = %s,
            match_detail = coalesce(match_detail, '{}'::jsonb) || %s,
            updated_at = now()
        WHERE id = %s
        """,
        (state, movement_id, Jsonb(detail), line_id),
    )


def _logged_not_on_statement(
    conn: psycopg.Connection,
    *,
    account: dict[str, Any],
    period: tuple[date, date] | None,
    matched: set[str],
) -> list[dict[str, Any]]:
    if period is None:
        return []
    ledger_id = account["ledger_account_id"]
    rows = conn.execute(
        """
        SELECT
            m.id,
            m.date,
            m.description,
            abs(sum(
                CASE
                    WHEN t.from_account_id = %s THEN t.amount
                    WHEN t.to_account_id = %s THEN -t.amount
                    ELSE 0
                END
            ))::bigint AS amount_paise
        FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        WHERE m.kind <> 'reversal'
          AND m.reverses_id IS NULL
          AND m.date BETWEEN %s AND %s
          AND (t.from_account_id = %s OR t.to_account_id = %s)
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
        (ledger_id, ledger_id, period[0], period[1], ledger_id, ledger_id),
    ).fetchall()
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


def resolve_line(
    conn: psycopg.Connection,
    *,
    import_id: str,
    line_id: str,
    resolution: str,
    movement_id: str | None = None,
) -> dict[str, Any]:
    line = conn.execute(
        "SELECT * FROM statement_lines WHERE id = %s AND import_id = %s",
        (line_id, import_id),
    ).fetchone()
    if line is None:
        raise ValueError("No such statement line")
    if resolution == "ignore":
        _set_match(conn, line_id, "ignored", None, {"resolved_by": "user"})
    elif resolution == "new":
        _set_match(conn, line_id, "new", None, {"resolved_by": "user"})
    elif resolution == "merge":
        if not movement_id:
            raise ValueError("merge needs a movement_id")
        candidate = conn.execute(
            "SELECT id FROM movements WHERE id = %s", (movement_id,)
        ).fetchone()
        if candidate is None:
            raise ValueError("No such movement")
        _set_match(conn, line_id, "certain", movement_id, {"resolved_by": "user"})
    elif resolution == "confirm_transfer":
        if not (line["match_detail"] or {}).get("suggested_transfer_account_id"):
            raise ValueError("That line is not a suggested transfer")
        _set_match(conn, line_id, "new", None, {"transfer_confirmed": True})
    elif resolution == "reject_transfer":
        conn.execute(
            """
            UPDATE statement_lines
            SET match_state = 'new',
                match_detail = (coalesce(match_detail, '{}'::jsonb)
                                - 'suggested_transfer_account_id'),
                updated_at = now()
            WHERE id = %s
            """,
            (line_id,),
        )
        _set_match(conn, line_id, "new", None, {"transfer_rejected": True})
    else:
        raise ValueError("resolution is ignore, new, merge, confirm_transfer, or reject_transfer")
    conn.commit()
    return dict(conn.execute("SELECT * FROM statement_lines WHERE id = %s", (line_id,)).fetchone())


__all__ = ["content_hash", "insert_lines", "reconcile", "resolve_line"]
