"""The submitted parser script's sandbox contract: `import_id` is optional there."""

from __future__ import annotations

import hashlib
import io
from datetime import date

import pypdfium2 as pdfium

from arno.agent.parser import submit_parser_code
from arno.importers.monty import ScriptContext, run_script
from arno.importers.pipeline import create_import
from arno.importers.readers import Doc
from arno.ledger.accounts import create_account
from arno.ledger.audit import new_id
from tests.import_fixtures import SBI_PARSER, sbi_csv


def _blank_pdf(pages: int) -> bytes:
    """A PDF of blank pages; only the page count matters here."""
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(595, 842)
    buffer = io.BytesIO()
    pdf.save(buffer)
    return buffer.getvalue()


async def _att(conn, payload: bytes, name: str = "statement.csv") -> str:
    attachment_id = await new_id(conn, "att")
    await conn.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, 'statement', $2, 'application/pdf', $3, $4, $5)
        """,
        attachment_id,
        name,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        payload,
    )
    return attachment_id


async def _import(conn, payload: bytes, *, account: dict | None = None) -> dict:
    attachment_id = await _att(conn, payload)
    return await create_import(
        conn, attachment_id=attachment_id, account_id=account["id"] if account else None
    )


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


async def test_a_submitted_script_may_name_its_own_import():
    doc = Doc(sbi_csv())
    same = await run_script(
        doc,
        'cells = await doc_cells(import_id="imp_1", sheet=0)\nlen(cells)',
        ScriptContext(import_id="imp_1"),
    )
    assert same.error is None, same.error
    assert same.output > 0

    wrong = await run_script(
        doc, 'await doc_info(import_id="imp_2")', ScriptContext(import_id="imp_1")
    )
    assert wrong.error is not None
    assert "import_id must be imp_1 or omitted" in wrong.error

    # A replay has no import of its own, so any value is accepted.
    replay = await run_script(doc, 'await doc_info(import_id="imp_9")')
    assert replay.error is None, replay.error


def test_words_range_reads_every_page_of_a_long_statement():
    doc = Doc(_blank_pdf(74))
    assert doc.page_count() == 74
    assert len(doc.words_range(0, 73)) == 74


async def test_a_failed_gate_leaves_the_import_parsing_with_the_reason(ledger):
    account = await _account(ledger)
    row = await _import(ledger, sbi_csv(), account=account)
    broken = SBI_PARSER.replace("'closing': closing", "'closing': 1")

    result = await submit_parser_code(ledger, import_ids=[row["id"]], code=broken)

    assert result["status"] == "failed"
    stored = await ledger.fetchrow("SELECT status, reason FROM imports WHERE id = $1", row["id"])
    assert stored["status"] == "parsing"
    assert stored["reason"] == result["reason"]


async def test_an_input_reason_still_sets_needs_input(ledger):
    account = await create_account(
        ledger, kind="bank", name="SBI savings", institution="SBI", last4="6789"
    )
    row = await _import(ledger, sbi_csv(), account=account)
    code = (
        "{'accounts': [{'name': 'SBI savings', 'institution': 'SBI', 'opening': None,"
        " 'closing': 34402890, 'rows': [{'date': '2026-04-01', 'debit': 100, 'credit': 0}]}]}"
    )

    await submit_parser_code(ledger, import_ids=[row["id"]], code=code)

    stored = await ledger.fetchrow("SELECT status, reason FROM imports WHERE id = $1", row["id"])
    assert stored["status"] == "needs_input"
    assert "opening balance" in stored["reason"]
