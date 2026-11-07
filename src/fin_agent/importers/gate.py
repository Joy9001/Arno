"""The gate: rerun a submitted parser, run the checks, then replay every stored statement.

Only this module decides whether a submission may move an import to review. It runs the script
text in a fresh sandbox, so the Code Mode session that wrote it proves nothing; it then replays
the same text on every committed statement from the same bank and statement type, and compares
the rows with what the user approved. A mismatch is the reason, and the first one is enough.
"""

from __future__ import annotations

from typing import Any

from fin_agent.db.conn import Connection, connect
from fin_agent.importers.checks import INPUT_REASONS, account_check, worst_status
from fin_agent.importers.helpers import HELPER_VERSION
from fin_agent.importers.identifiers import scrub
from fin_agent.importers.monty import ScriptContext, normalize_accounts, run_script
from fin_agent.importers.readers import Doc, open_documents


def _sample(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for account in accounts:
        for row in account.get("rows") or []:
            rows.append(
                {
                    "date": row.get("date"),
                    "description": str(row.get("description") or "")[:100],
                    "debit": row.get("debit"),
                    "credit": row.get("credit"),
                    "balance": row.get("balance"),
                }
            )
            if len(rows) >= 5:
                return rows
    return rows


def _counts(accounts: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "accounts": len(accounts),
        "rows": sum(len(account.get("rows") or []) for account in accounts),
        "debits": sum(
            1 for account in accounts for row in account.get("rows") or [] if row.get("debit")
        ),
        "credits": sum(
            1 for account in accounts for row in account.get("rows") or [] if row.get("credit")
        ),
    }


def _check_accounts(context: ScriptContext, accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        account_check(
            account,
            period=context.period,
            ledger_opening=context.ledger_opening_paise,
            manual_opening=context.manual_opening_paise,
            expected_fingerprint=context.expected_fingerprint,
        )
        for account in accounts
    ]


def _expected_row(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": line["date"].isoformat(),
        "debit_paise": int(line["debit_paise"] or 0),
        "credit_paise": int(line["credit_paise"] or 0),
        "balance_paise": line["balance_paise"],
        "description": line["description_raw"],
    }


def _got_row(row: dict[str, Any], own_last4: set[str]) -> dict[str, Any]:
    description = scrub(str(row.get("description") or ""), own_last4=own_last4)[0] or ""
    return {
        "date": str(row.get("date") or ""),
        "debit_paise": int(row.get("debit") or 0),
        "credit_paise": int(row.get("credit") or 0),
        "balance_paise": row.get("balance"),
        "description": description,
    }


def _first_mismatch(
    expected: list[dict[str, Any]],
    got: list[dict[str, Any]],
    *,
    own_last4: set[str],
) -> dict[str, Any] | None:
    for index in range(max(len(expected), len(got))):
        want = _expected_row(expected[index]) if index < len(expected) else None
        have = _got_row(got[index], own_last4) if index < len(got) else None
        if want != have:
            return {"row": index, "expected": want, "got": have}
    return None


async def _case(conn: Connection, import_id: str) -> dict[str, Any] | None:
    return await conn.fetchrow(
        """
        SELECT i.id, i.institution, i.fingerprint, i.period_start, i.period_end,
               i.opening_balance_paise, i.review,
               a.kind AS account_kind, a.name AS account_name,
               a.institution AS account_institution
        FROM imports i
        LEFT JOIN accounts a ON a.id = i.account_id
        WHERE i.id = $1
        """,
        import_id,
    )


async def _replay_rows(
    conn: Connection,
    *,
    case: dict[str, Any],
) -> list[dict[str, Any]]:
    kind = case["account_kind"]
    institution = case["account_institution"] or case["account_name"] or case["institution"]
    if not kind or not institution:
        return []
    return await conn.fetch(
        """
        SELECT i.id AS import_id, i.institution, i.fingerprint, i.period_start, i.period_end,
               i.opening_balance_paise, i.review,
               a.name AS account_name, a.last4,
               att.filename, att.data
        FROM imports i
        JOIN accounts a ON a.id = i.account_id
        JOIN LATERAL (
            SELECT filename, data
            FROM attachments x
            WHERE x.import_id = i.id AND x.data IS NOT NULL AND x.purged_at IS NULL
            ORDER BY x.created_at DESC
            LIMIT 1
        ) att ON true
        WHERE i.status = 'committed'
          AND i.undone_at IS NULL
          AND i.id <> $1
          AND a.kind = $2
          AND lower(coalesce(a.institution, a.name, i.institution)) = lower($3)
        ORDER BY i.created_at, i.id
        """,
        case["id"],
        kind,
        institution,
    )


def _replay_context(row: dict[str, Any]) -> ScriptContext:
    period = None
    if row["period_start"] and row["period_end"]:
        period = (row["period_start"].isoformat(), row["period_end"].isoformat())
    return ScriptContext(
        period=period,
        ledger_opening_paise=row["opening_balance_paise"],
        manual_opening_paise=(row["review"] or {}).get("manual_opening_paise"),
        account_name=row["account_name"],
        fingerprint=row["fingerprint"],
        expected_fingerprint=row["fingerprint"],
    )


async def _needs_repair(
    conn: Connection,
    *,
    case: dict[str, Any],
    fingerprint: str | None,
) -> bool:
    if not fingerprint or not case["account_kind"]:
        return False
    institution = case["account_institution"] or case["account_name"] or case["institution"]
    row = await conn.fetchrow(
        """
        SELECT v.helper_version
        FROM statement_layouts l
        JOIN parser_versions v ON v.id = l.active_version_id
        WHERE lower(l.institution) = lower($1)
          AND lower(l.kind) = lower($2)
          AND $3 = ANY(l.variants)
        """,
        institution,
        case["account_kind"],
        fingerprint,
    )
    return bool(row and row["helper_version"] != HELPER_VERSION)


async def _replay(
    conn: Connection,
    *,
    context: ScriptContext,
    code: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "checked": 0,
        "passed": 0,
        "skipped": 0,
        "needs_repair": False,
        "mismatch": None,
    }
    if not context.import_id:
        return result
    case = await _case(conn, context.import_id)
    if case is None:
        return result
    result["needs_repair"] = await _needs_repair(
        conn, case=case, fingerprint=context.fingerprint or case["fingerprint"]
    )
    for row in await _replay_rows(conn, case=case):
        try:
            docs = open_documents(bytes(row["data"]))
        except Exception:  # noqa: BLE001 - a stored file that will not open is skipped
            result["skipped"] += 1
            continue
        if not docs:
            result["skipped"] += 1
            continue
        result["checked"] += 1
        replayed = await run_script(docs[0], code, _replay_context(row), stage="gate_replay")
        if replayed.error:
            result["mismatch"] = {
                "import_id": row["import_id"],
                "filename": row["filename"],
                "row": 0,
                "reason": replayed.error,
                "expected": None,
                "got": None,
            }
            return result
        accounts, reason = normalize_accounts(replayed.output)
        if not accounts:
            result["mismatch"] = {
                "import_id": row["import_id"],
                "filename": row["filename"],
                "row": 0,
                "reason": reason or "no accounts",
                "expected": None,
                "got": None,
            }
            return result
        expected = await conn.fetch(
            "SELECT * FROM statement_lines WHERE import_id = $1 ORDER BY id",
            row["import_id"],
        )
        own_last4 = {row["last4"]} if row["last4"] else set()
        mismatch = _first_mismatch(
            expected,
            accounts[0].get("rows") or [],
            own_last4=own_last4,
        )
        if mismatch is not None:
            result["mismatch"] = {
                "import_id": row["import_id"],
                "filename": row["filename"],
                **mismatch,
            }
            return result
        result["passed"] += 1
    return result


def _failure(
    reason: str | None,
    *,
    counts: dict[str, int] | None = None,
    sample: list[dict[str, Any]] | None = None,
    checks: list[dict[str, Any]] | None = None,
    replay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "reason": reason,
        "counts": counts or {},
        "sample": sample or [],
        "checks": checks or [],
        "replay": replay,
    }


async def run_gate(
    *,
    doc: Doc,
    context: ScriptContext,
    code: str,
) -> dict[str, Any]:
    """The verdict on one submitted script: fresh sandbox, checks, then every stored replay."""
    fresh = await run_script(doc, code, context, stage="gate")
    if fresh.error:
        return _failure(fresh.error)
    output = fresh.output
    if isinstance(output, dict) and output.get("not_a_statement"):
        return {
            "status": "not_a_statement",
            "kind": str(output["not_a_statement"])[:80],
            "script": code,
        }
    if isinstance(output, dict) and output.get("needs_input"):
        return {
            "status": "needs_input",
            "question": str(output["needs_input"])[:500],
            "script": code,
        }
    accounts, reason = normalize_accounts(output)
    if not accounts:
        return _failure(reason or "no accounts")
    checks = _check_accounts(context, accounts)
    counts = _counts(accounts)
    sample = _sample(accounts)
    input_only = all(
        check.get("status") == "VERIFIED" or check.get("reason") in INPUT_REASONS
        for check in checks
    )
    if not input_only:
        reason = next((check.get("reason") for check in checks if check.get("reason")), None)
        return _failure(
            reason or worst_status([check.get("status", "FAILED") for check in checks]),
            counts=counts,
            sample=sample,
            checks=checks,
        )
    async with connect() as conn:
        replay = await _replay(conn, context=context, code=code)
    if replay["mismatch"] is not None:
        mismatch = replay["mismatch"]
        return _failure(
            f"replay mismatch on import {mismatch['import_id']} row {mismatch['row']}",
            counts=counts,
            sample=sample,
            checks=checks,
            replay=replay,
        )
    return {
        "status": "parser_submitted",
        "reason": None,
        "counts": counts,
        "sample": sample,
        "checks": checks,
        "accounts": accounts,
        "script": code,
        "helper_version": HELPER_VERSION,
        "replay": replay,
    }


__all__ = ["run_gate"]
