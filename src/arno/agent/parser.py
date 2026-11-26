"""Statement import gate: run a parser script over a stored file and finalize the import."""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from pydantic_ai import RunContext

from arno.agent.deps import AgentDeps
from arno.db.conn import Connection, connect
from arno.importers.checks import INPUT_REASONS, OPENING_MISSING, account_check
from arno.importers.gate import run_gate
from arno.importers.helpers import HELPER_VERSION
from arno.importers.layout import detect_fingerprint, normalize_headers
from arno.importers.layouts import layout_for
from arno.importers.periods import (
    derive_period,
    ledger_last_date,
)
from arno.importers.periods import ledger_opening as opening_before
from arno.importers.pipeline import (
    _account_fingerprint,
    _finalize,
    _infer_account,
    _resolve_parsed_account,
    _set_status,
    set_progress,
)
from arno.importers.readers import Doc, DocError, in_doc_worker, open_documents
from arno.ledger.common import today as ledger_today

logger = logging.getLogger("arno.agent.parser")


def _report(
    status: str,
    *,
    reason: str | None = None,
    imports: list[dict[str, Any]] | None = None,
    blocking: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "imports": imports or [],
        "blocking": blocking,
    }


async def _row(conn: Connection, import_id: str) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM imports WHERE id = $1", import_id)
    if row is None:
        raise ValueError(f"No import {import_id}")
    return dict(row)


async def _data(conn: Connection, import_id: str) -> tuple[str, bytes]:
    row = await conn.fetchrow(
        """
        SELECT a.id, a.data FROM imports i
        JOIN attachments a ON a.id = i.attachment_id
        WHERE i.id = $1 AND a.data IS NOT NULL AND a.purged_at IS NULL
        """,
        import_id,
    )
    if row is None:
        raise ValueError(f"Import {import_id} has no stored statement file")
    return str(row["id"]), bytes(row["data"])


async def _context(
    conn: Connection,
    row: dict[str, Any],
    doc: Doc,
    *,
    account: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The host facts a parser cannot own: account, period, opening, fingerprint, layout."""
    if account is None and row.get("account_id"):
        account = await conn.fetchrow("SELECT * FROM v_accounts WHERE id = $1", row["account_id"])
    if account is None:
        account = await _infer_account(conn, doc)
        if account is not None:
            await conn.execute(
                "UPDATE imports SET account_id = $1 WHERE id = $2 AND account_id IS NULL",
                account["id"],
                row["id"],
            )
    ledger_last = await ledger_last_date(conn, account["id"]) if account else None
    period = derive_period(doc, ledger_last=ledger_last, today=ledger_today())
    opening = (
        await opening_before(conn, account["id"], date.fromisoformat(period["start"]))
        if account and period
        else None
    )
    manual_opening = (row.get("review") or {}).get("manual_opening_paise")
    fingerprint = detect_fingerprint(doc) or _account_fingerprint(account)
    institution = (account["institution"] or account["name"]) if account else None
    layout = await layout_for(
        conn,
        fingerprint,
        institution=institution,
        kind=account["kind"] if account else None,
    )
    if layout is not None and layout["helper_version"] != HELPER_VERSION:
        layout = None
    from arno.importers.monty import ScriptContext

    context = ScriptContext(
        period=(period["start"], period["end"]) if period else None,
        ledger_opening_paise=opening["paise"] if opening else None,
        ledger_opening_as_of=opening["as_of"] if opening else None,
        manual_opening_paise=int(manual_opening) if manual_opening is not None else None,
        account_name=account["name"] if account else None,
        fingerprint=fingerprint,
        expected_fingerprint=fingerprint if layout else None,
        allow_vision=True,
        import_id=row["id"],
    )
    return {
        "account": account,
        "institution": institution,
        "period": period,
        "opening": opening,
        "fingerprint": fingerprint,
        "layout": layout,
        "context": context,
    }


def _opening_is_the_only_gap(check: dict[str, Any]) -> dict[str, Any]:
    """Name the state the user completes: sound rows whose opening only the user can give."""
    if check.get("status") == "FAILED" and check.get("reason") == OPENING_MISSING:
        return {**check, "status": "NEEDS_OPENING"}
    return check


async def submit_parser_code(
    conn: Connection, *, import_ids: list[str], code: str
) -> dict[str, Any]:
    """Run the gate for each import, then finalize each one on a pass.

    An import whose rows pass but whose opening only the user can give is parked for the import
    card's opening answer; it is finished, not failed, and the other imports still run.
    """
    prepared: list[dict[str, Any]] = []
    parked: list[dict[str, Any]] = []
    for import_id in import_ids:
        row = await _row(conn, import_id)
        if row["status"] in ("committed", "rejected"):
            # Rewriting a committed import's lines would re-book money already in the ledger.
            await set_progress(conn, import_id, None)
            return _report(
                "failed",
                reason=f"Import {import_id} is already {row['status']}; start a new import.",
                imports=prepared,
            )
        _attachment_id, data = await _data(conn, import_id)
        try:
            docs = await in_doc_worker(lambda data=data: open_documents(data))
        except DocError as exc:
            await set_progress(conn, import_id, None)
            return _report(
                "failed",
                reason=f"could not open import {import_id}: {exc.code}",
                imports=prepared,
            )
        doc = docs[0]
        info = await _context(conn, row, doc)
        gate = await run_gate(doc=doc, context=info["context"], code=code)
        replay = gate.get("replay") or {}
        if "checked" in replay:
            await set_progress(
                conn,
                import_id,
                f"checking against {replay['checked']} stored statements",
            )
        counts = gate.get("counts") or {}
        checks = [
            {
                key: check.get(key)
                for key in ("status", "reason", "computed_closing", "chain_breaks")
            }
            for check in gate.get("checks") or []
        ]
        if gate["status"] != "parser_submitted":
            if gate["status"] == "not_a_statement":
                await _set_status(
                    conn,
                    import_id,
                    "not_a_statement",
                    reason=f"This isn't a statement: {gate.get('kind') or 'unknown document'}",
                )
            else:
                reason = gate.get("reason") or "the checks did not pass"
                # A repair is still in flight, so the card stays parsing; the run's end decides.
                await _set_status(
                    conn,
                    import_id,
                    "needs_input" if reason in INPUT_REASONS else "parsing",
                    reason=reason,
                )
            return {
                "status": gate["status"],
                "reason": gate.get("reason"),
                "counts": counts,
                "sample": gate.get("sample") or [],
                "checks": checks,
                "replay": gate.get("replay"),
                "imports": prepared,
            }
        accounts = gate.get("accounts") or []
        parsed = accounts[0]
        resolved = await _resolve_parsed_account(
            conn, parsed_account=parsed, current=info["account"], doc=doc
        )
        if resolved is None:
            await _set_status(
                conn, import_id, "needs_input", reason="Which account is this statement for?"
            )
            return {
                "status": "needs_account",
                "reason": "Which account is this statement for? Ask the user, then include the "
                "account's name or institution in the script's account.",
                "counts": counts,
                "sample": gate.get("sample") or [],
                "checks": checks,
                "replay": gate.get("replay"),
                "imports": prepared,
            }
        period = info["period"]
        opening = info["opening"]
        if period is None:
            ledger_last = await ledger_last_date(conn, resolved["id"])
            period = derive_period(doc, ledger_last=ledger_last, today=ledger_today())
        if opening is None and period:
            opening = await opening_before(
                conn, resolved["id"], date.fromisoformat(period["start"])
            )
        check = account_check(
            parsed,
            period=(period["start"], period["end"]) if period else None,
            ledger_opening=opening["paise"] if opening else None,
            manual_opening=info["context"].manual_opening_paise,
            expected_fingerprint=info["fingerprint"],
        )
        if check["status"] != "VERIFIED":
            reason = check.get("reason") or "the checks did not pass"
            if reason == OPENING_MISSING:
                # The rows are sound; only the opening is unknown. Keep them, so the review
                # card's opening answer completes the check instead of asking the parser again.
                parked.append(
                    {
                        "import_id": import_id,
                        "account": resolved,
                        "parsed": parsed,
                        "check": check,
                        "period": period,
                        "fingerprint": info["fingerprint"]
                        or (
                            normalize_headers(parsed["headers"]) if parsed.get("headers") else None
                        ),
                    }
                )
                continue
            await _set_status(
                conn,
                import_id,
                "needs_input" if reason in INPUT_REASONS else "parsing",
                reason=reason,
                check=check,
            )
            return {
                "status": "failed",
                "reason": check.get("reason") or "the checks did not pass",
                "counts": counts,
                "sample": gate.get("sample") or [],
                "checks": [check],
                "replay": gate.get("replay"),
                "imports": prepared,
            }
        prepared.append(
            {
                "import_id": import_id,
                "row": row,
                "doc": doc,
                "parsed": parsed,
                "account": resolved,
                "check": check,
                "period": period,
                # The script's printed headers name the variant when the file's own headers
                # couldn't be detected before it ran.
                "fingerprint": info["fingerprint"]
                or (normalize_headers(parsed["headers"]) if parsed.get("headers") else None),
                "counts": counts,
                "sample": gate.get("sample") or [],
                "replay": gate.get("replay"),
            }
        )

    for item in prepared:
        written = await _finalize(
            conn,
            import_id=item["import_id"],
            account=item["account"],
            parsed_account=item["parsed"],
            check=item["check"],
            script=code,
            helper_version=HELPER_VERSION,
            fingerprint=item["fingerprint"],
            period=item["period"],
        )
        if not written:
            # The user rejected this import while the parser was still reading it.
            return _report(
                "failed",
                reason=f"Import {item['import_id']} was rejected while it was being read.",
                imports=[
                    {"import_id": entry["import_id"], "status": "rejected", "reason": None}
                    for entry in prepared
                ],
            )

    for item in parked:
        await _finalize(
            conn,
            import_id=item["import_id"],
            account=item["account"],
            parsed_account=item["parsed"],
            check=item["check"],
            script=code,
            helper_version=HELPER_VERSION,
            fingerprint=item["fingerprint"],
            period=item["period"],
            status="needs_input",
            reason=OPENING_MISSING,
        )
    if parked:
        return {
            "status": "needs_opening",
            "reason": "The rows are kept. The import card asks the user for the opening balance "
            "of each import listed in `waiting`; those files are done. Do not invent an opening "
            "or repair further.",
            "waiting": [item["import_id"] for item in parked],
            "checks": [_opening_is_the_only_gap(item["check"]) for item in parked]
            + [item["check"] for item in prepared],
            "imports": [
                {"import_id": item["import_id"], "counts": item["counts"]} for item in prepared
            ],
        }
    return {
        "status": "parser_submitted",
        "reason": None,
        "counts": prepared[0]["counts"] if prepared else {},
        "sample": prepared[0]["sample"] if prepared else [],
        "checks": [item["check"] for item in prepared],
        "replay": prepared[0]["replay"] if prepared else None,
        "imports": [
            {"import_id": item["import_id"], "counts": item["counts"]} for item in prepared
        ],
    }


_GENERIC_NAME_WORDS = frozenset(
    {
        "bank",
        "card",
        "credit",
        "debit",
        "account",
        "main",
        "savings",
        "saving",
        "current",
        "the",
        "wallet",
        "cash",
        "salary",
        "and",
    }
)
_CARD_PAYMENT = re.compile(
    r"cc\s*(re)?pay|credit\s*card|card\s*(bill|pay|repay|due)|ccpay", re.IGNORECASE
)


def names_account(text: str, account: dict[str, Any], *, only_card: bool) -> bool:
    """Whether a statement line's own text names this account as its other side.

    A name word or the last four digits name it. The institution alone does not: a UPI narration
    prints the payee's bank code (`/HDFC/`), so it names a card only beside a card-payment phrase.
    A card-payment phrase alone names a card only when the user has one card. The user's own
    name names no account.
    """
    lowered = text.lower()
    words = set(re.findall(r"[a-z0-9]+", lowered))
    institution = set(re.findall(r"[a-z0-9]+", str(account.get("institution") or "").lower()))
    for word in re.findall(r"[a-z0-9]+", str(account.get("name") or "").lower()):
        if (
            len(word) >= 3
            and word not in _GENERIC_NAME_WORDS
            and word not in institution
            and word in words
        ):
            return True
    last4 = str(account.get("last4") or "")
    if len(last4) == 4 and last4 in lowered:
        return True
    if account.get("kind") != "card" or not _CARD_PAYMENT.search(text):
        return False
    return only_card or bool(institution & words)


async def suggest_transfers(
    ctx: RunContext[AgentDeps],
    import_id: str,
    suggestions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """List the user's own accounts, and suggest own-account moves for statement lines.

    With no suggestions it only lists accounts and writes nothing. `row` is the 0-based position
    in the rows the script returned, or pass `line_id`. A row's own text must name its account.
    """
    del ctx
    async with connect() as conn:
        row = await _row(conn, import_id)
        own = await conn.fetch(
            """
            SELECT id, name, kind, institution, last4
            FROM v_accounts
            WHERE active AND kind IN ('bank', 'card', 'wallet', 'cash')
              AND ($1::text IS NULL OR id <> $1)
            ORDER BY kind, name
            """,
            row["account_id"],
        )
        accounts = [dict(item) for item in own]
        # The line's stored row index is the script's own row order; ids from one instant do
        # not sort in insertion order.
        lines = await conn.fetch(
            """
            SELECT id, description_raw FROM statement_lines WHERE import_id = $1
            ORDER BY coalesce((match_detail ->> 'row')::int, 2147483647), id
            """,
            import_id,
        )
        valid = {str(item["id"]): str(item["description_raw"] or "") for item in lines}
        by_id = {str(item["id"]): dict(item) for item in own}
        own_ids = set(by_id)
        cards = sum(1 for item in own if item["kind"] == "card")
        resolved: list[tuple[str, str]] = []
        for suggestion in suggestions or []:
            line_id = str(suggestion.get("line_id") or "")
            if not line_id and suggestion.get("row") is not None:
                index = int(suggestion["row"])
                if 0 <= index < len(lines):
                    line_id = str(lines[index]["id"])
            account_id = str(suggestion.get("account_id") or "")
            if line_id not in valid:
                return {
                    "status": "error",
                    "reason": f"row {suggestion.get('row')} is not in import {import_id}",
                    "accounts": accounts,
                }
            if account_id not in own_ids:
                return {
                    "status": "error",
                    "reason": f"account {account_id} is not one of the user's own accounts",
                    "accounts": accounts,
                }
            if not names_account(valid[line_id], by_id[account_id], only_card=cards == 1):
                return {
                    "status": "error",
                    "reason": f"row {suggestion.get('row')} does not name account "
                    f"{by_id[account_id]['name']}: its text has no account name, institution, "
                    "last four digits, or card-payment phrase. Do not suggest it.",
                    "accounts": accounts,
                }
            resolved.append((line_id, account_id))
        # Checked in full first, so one bad suggestion applies none of them.
        for line_id, account_id in resolved:
            await conn.execute(
                """
                UPDATE statement_lines
                SET match_detail = coalesce(match_detail, '{}'::jsonb) || $1::jsonb,
                    updated_at = now()
                WHERE id = $2 AND import_id = $3
                """,
                {"suggested_transfer_account_id": account_id},
                line_id,
                import_id,
            )
    return {"status": "suggested", "suggested": len(resolved), "accounts": accounts}
