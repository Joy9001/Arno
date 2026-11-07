"""The gate: fresh sandbox, checks, and a replay on every approved stored statement."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from fin_agent.importers.commit import commit_import
from fin_agent.importers.gate import run_gate
from fin_agent.importers.layout import detect_fingerprint
from fin_agent.importers.layouts import layout_for, rollback_layout, save_version
from fin_agent.importers.monty import ScriptContext
from fin_agent.importers.readers import Doc, open_documents
from fin_agent.importers.reconcile import insert_lines
from fin_agent.ledger.accounts import create_account
from fin_agent.ledger.audit import new_id
from tests.import_fixtures import SBI_PARSER, sbi_csv

ROWS = [
    {
        "date": "2026-04-01",
        "description": "UPI/DR/123456789012/SWIGGY BANGALORE/ramesh@okaxis",
        "debit": 45000,
        "credit": 0,
        "balance": 24522890,
    },
    {
        "date": "2026-04-05",
        "description": "NEFT CR/ACME PAYROLL/SALARY APRIL",
        "debit": 0,
        "credit": 10000000,
        "balance": 34522890,
    },
    {
        "date": "2026-04-12",
        "description": "UPI/DR/223456789012/AMAZON PAY INDIA/amazon@okhdfcbank",
        "debit": 120000,
        "credit": 0,
        "balance": 34402890,
    },
]


async def _attachment(conn, payload: bytes) -> str:
    attachment_id = await new_id(conn, "att")
    await conn.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, 'statement', 'sbi.csv', 'text/csv', $2, $3, $4)
        """,
        attachment_id,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        payload,
    )
    return attachment_id


async def _account(conn) -> dict:
    return await create_account(
        conn,
        kind="bank",
        name="SBI savings",
        institution="SBI",
        last4="6789",
        opening_balance_paise=24567890,
        opening_balance_as_of=date(2026, 3, 31),
    )


async def _import(
    conn,
    *,
    account: dict,
    attachment_id: str,
    status: str,
    fingerprint: str,
) -> str:
    import_id = await new_id(conn, "imp")
    await conn.execute(
        """
        INSERT INTO imports
            (id, file_hash, status, attachment_id, account_id, institution, fingerprint,
             period_start, period_end, opening_balance_paise, closing_balance_paise,
             commit_groups)
        VALUES ($1, $2, $3, $4, $5, 'SBI', $6, '2026-04-01', '2026-04-30',
                24567890, 34402890, '[]'::jsonb)
        """,
        import_id,
        f"hash-{import_id}",
        status,
        attachment_id,
        account["id"],
        fingerprint,
    )
    await conn.execute(
        "UPDATE attachments SET import_id = $1 WHERE id = $2", import_id, attachment_id
    )
    return import_id


async def _committed(conn, account: dict, fingerprint: str) -> str:
    attachment_id = await _attachment(conn, sbi_csv())
    import_id = await _import(
        conn,
        account=account,
        attachment_id=attachment_id,
        status="committed",
        fingerprint=fingerprint,
    )
    raw_account = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    await insert_lines(
        conn,
        import_id=import_id,
        account=raw_account,
        parsed_account={"rows": ROWS},
    )
    return import_id


def _context(import_id: str, fingerprint: str) -> ScriptContext:
    return ScriptContext(
        period=("2026-04-01", "2026-04-30"),
        ledger_opening_paise=24567890,
        fingerprint=fingerprint,
        expected_fingerprint=fingerprint,
        allow_vision=False,
        import_id=import_id,
    )


async def test_the_gate_replays_a_committed_statement_and_passes(ledger):
    fingerprint = detect_fingerprint(Doc(sbi_csv()))
    assert fingerprint
    account = await _account(ledger)
    committed = await _committed(ledger, account, fingerprint)
    current = await _import(
        ledger,
        account=account,
        attachment_id=await _attachment(ledger, sbi_csv()),
        status="parsing",
        fingerprint=fingerprint,
    )

    result = await run_gate(
        doc=Doc(sbi_csv()), context=_context(current, fingerprint), code=SBI_PARSER
    )

    assert result["status"] == "parser_submitted", result
    assert result["replay"]["checked"] == 1
    assert result["replay"]["passed"] == 1
    assert result["replay"]["mismatch"] is None
    assert committed != current


async def test_a_replay_mismatch_blocks_and_reports_the_first_row(ledger):
    fingerprint = detect_fingerprint(Doc(sbi_csv()))
    assert fingerprint
    account = await _account(ledger)
    committed = await _committed(ledger, account, fingerprint)
    current = await _import(
        ledger,
        account=account,
        attachment_id=await _attachment(ledger, sbi_csv()),
        status="parsing",
        fingerprint=fingerprint,
    )
    changed = SBI_PARSER.replace(
        "'description': str(row[1])", "'description': str(row[1]) + ' MUT'"
    )

    result = await run_gate(
        doc=Doc(sbi_csv()), context=_context(current, fingerprint), code=changed
    )

    assert result["status"] == "failed", result
    mismatch = result["replay"]["mismatch"]
    assert mismatch["import_id"] == committed
    assert mismatch["row"] == 0
    assert mismatch["expected"]["description"].startswith("UPI/DR/123456789012")
    assert mismatch["got"]["description"].endswith(" MUT")


async def test_an_undone_import_leaves_the_replay_set(ledger):
    fingerprint = detect_fingerprint(Doc(sbi_csv()))
    assert fingerprint
    account = await _account(ledger)
    committed = await _committed(ledger, account, fingerprint)
    current = await _import(
        ledger,
        account=account,
        attachment_id=await _attachment(ledger, sbi_csv()),
        status="parsing",
        fingerprint=fingerprint,
    )
    await ledger.execute(
        "UPDATE imports SET status = 'rejected', undone_at = now() WHERE id = $1", committed
    )

    result = await run_gate(
        doc=Doc(sbi_csv()), context=_context(current, fingerprint), code=SBI_PARSER
    )

    assert result["status"] == "parser_submitted", result
    assert result["replay"]["checked"] == 0


async def test_a_purged_file_leaves_the_replay_set(ledger):
    fingerprint = detect_fingerprint(Doc(sbi_csv()))
    assert fingerprint
    account = await _account(ledger)
    committed = await _committed(ledger, account, fingerprint)
    current = await _import(
        ledger,
        account=account,
        attachment_id=await _attachment(ledger, sbi_csv()),
        status="parsing",
        fingerprint=fingerprint,
    )
    await ledger.execute(
        """
        UPDATE attachments SET data = NULL, purged_at = now()
        WHERE import_id = $1
        """,
        committed,
    )

    result = await run_gate(
        doc=Doc(sbi_csv()), context=_context(current, fingerprint), code=SBI_PARSER
    )

    assert result["status"] == "parser_submitted", result
    assert result["replay"]["checked"] == 0


async def _review_with_parser(conn, account: dict, fingerprint: str, parser: str) -> str:
    attachment_id = await _attachment(conn, sbi_csv())
    import_id = await _import(
        conn,
        account=account,
        attachment_id=attachment_id,
        status="review",
        fingerprint=fingerprint,
    )
    await conn.execute(
        """
        UPDATE imports
        SET parser = $1, closing_balance_paise = NULL, check_result = $2
        WHERE id = $3
        """,
        parser,
        {
            "status": "VERIFIED",
            "opening_source": "printed",
            "opening_paise": 24567890,
            "closing_paise": 34402890,
        },
        import_id,
    )
    return import_id


async def test_two_commits_append_two_versions_and_move_the_pointer(ledger):
    fingerprint = detect_fingerprint(Doc(sbi_csv()))
    assert fingerprint
    account = await _account(ledger)
    first = await _review_with_parser(ledger, account, fingerprint, SBI_PARSER)
    await commit_import(ledger, first, categorize=False)

    active = await layout_for(ledger, fingerprint, institution="SBI", kind="bank")
    assert active["version"] == 1

    second = await _review_with_parser(ledger, account, fingerprint, SBI_PARSER + "\n# v2")
    await commit_import(ledger, second, categorize=False)

    active = await layout_for(ledger, fingerprint, institution="SBI", kind="bank")
    assert active["version"] == 2
    versions = await ledger.fetch("SELECT version FROM parser_versions ORDER BY version")
    assert [row["version"] for row in versions] == [1, 2]


async def test_a_commit_appends_a_version_and_rollback_moves_the_pointer(ledger):
    fingerprint = detect_fingerprint(Doc(sbi_csv()))
    assert fingerprint
    first = await save_version(
        ledger,
        institution="SBI",
        kind="bank",
        fingerprint=fingerprint,
        parser=SBI_PARSER,
    )
    assert first["version"] == 1

    second = await save_version(
        ledger,
        institution="SBI",
        kind="bank",
        fingerprint=fingerprint,
        parser=SBI_PARSER + "\n# second",
    )
    assert second["version"] == 2
    active = await layout_for(ledger, fingerprint, institution="SBI", kind="bank")
    assert active["version"] == 2
    assert active["parser"].endswith("# second")
    assert fingerprint in active["variants"]

    await rollback_layout(ledger, layout_id=first["id"], version=1)
    active = await layout_for(ledger, fingerprint, institution="SBI", kind="bank")
    assert active["version"] == 1
    assert active["parser"] == SBI_PARSER


async def test_a_version_from_another_helper_is_needing_repair(ledger):
    fingerprint = detect_fingerprint(Doc(sbi_csv()))
    assert fingerprint
    saved = await save_version(
        ledger,
        institution="SBI",
        kind="bank",
        fingerprint=fingerprint,
        parser=SBI_PARSER,
    )
    await ledger.execute(
        "UPDATE parser_versions SET helper_version = 'old' WHERE id = $1",
        saved["active_version_id"],
    )
    account = await _account(ledger)
    current = await _import(
        ledger,
        account=account,
        attachment_id=await _attachment(ledger, sbi_csv()),
        status="parsing",
        fingerprint=fingerprint,
    )

    result = await run_gate(
        doc=Doc(sbi_csv()), context=_context(current, fingerprint), code=SBI_PARSER
    )

    assert result["status"] == "parser_submitted", result
    assert result["replay"]["needs_repair"] is True
    # The same text under the current helper is a new version, not the stale one.
    again = await save_version(
        ledger,
        institution="SBI",
        kind="bank",
        fingerprint=fingerprint,
        parser=SBI_PARSER,
    )
    assert again["version"] == 2


def test_an_unlocked_pdf_copy_opens_without_the_password():
    locked = (Path(__file__).parent / "fixtures" / "locked-1234.pdf").read_bytes()
    doc = open_documents(locked, password="1234")[0]
    unlocked = doc.readable_bytes()
    assert unlocked != locked
    assert open_documents(unlocked)[0].kind == "pdf"
