"""Readers, sniffing, the checks, and the synthetic SBI/Union/Slice parsers."""

from __future__ import annotations

import io
import zipfile

import pytest

from arno.importers.checks import check_statement
from arno.importers.layout import detect_fingerprint
from arno.importers.monty import ScriptContext, normalize_accounts, run_script
from arno.importers.readers import Doc, DocError, decode_text, open_documents, sniff
from tests.import_fixtures import (
    SBI_PARSER,
    SLICE_PARSER,
    UNION_PARSER,
    minimal_pdf,
    minimal_xlsx,
    sbi_csv,
    slice_csv,
    union_tsv,
)


def test_sniff_reads_magic_bytes_not_extensions():
    assert sniff(sbi_csv()) == "text"
    assert sniff(minimal_pdf(["hello"])) == "pdf"
    assert sniff(minimal_xlsx([["Date", "Amount"], ["2026-04-01", 100]])) == "xlsx"
    assert sniff(b"OFXHEADER:100\n<OFX></OFX>") == "ofx"
    assert sniff(b":20:START\n:28C:1/1\n") == "mt940"
    assert sniff(b'{"accounts": []}') == "json"


def test_csv_tsv_and_text_arrive_as_cells():
    csv_doc = Doc(sbi_csv())
    assert csv_doc.info()["delimiter"] == ","
    assert csv_doc.cells(0)[0][0] == "State Bank of India"
    tsv_doc = Doc(union_tsv())
    assert tsv_doc.info()["delimiter"] == "\t"
    plain = Doc(b"DATE  NARRATION  AMOUNT\n01/04/2026  X  10.00\n")
    assert plain.cells(0)[0][0].startswith("DATE")


def test_cp1252_decodes_because_monty_has_no_codec():
    text, encoding = decode_text("₹ café".encode("cp1252", errors="replace"))
    assert encoding == "cp1252"
    assert "caf" in text


def test_xlsx_pdf_and_docx_readers():
    xlsx = Doc(minimal_xlsx([["Date", "Narration", "Debit", "Credit", "Balance"]]))
    assert xlsx.info()["sheets"][0]["name"] == "Statement"
    assert xlsx.cells(0)[0][:2] == ["Date", "Narration"]

    pdf = Doc(
        minimal_pdf(
            [
                "Date Narration Debit Credit Balance",
                "01/04/2026 SWIGGY 450.00 0 245228.90",
            ]
        )
    )
    assert pdf.info()["pages"] == 1
    words = pdf.words(0)
    assert any("SWIGGY" in word["text"] for word in words)
    assert detect_fingerprint(pdf)

    import docx

    document = docx.Document()
    document.add_paragraph("Date Narration Debit Credit Balance")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "01/04/2026"
    buffer = io.BytesIO()
    document.save(buffer)
    docx_doc = Doc(buffer.getvalue())
    assert docx_doc.kind == "docx"
    assert any(
        "Date" in str(cell)
        for sheet_index in range(len(docx_doc.info()["sheets"]))
        for row in docx_doc.cells(sheet_index)
        for cell in row
    )


def test_rtf_and_password_error_paths():
    rtf = b"{\\rtf1\\ansi Date Narration Debit Credit Balance\\par 01/04/2026 X 1.00 0 1.00}"
    doc = Doc(rtf)
    assert doc.kind == "rtf"
    assert "Date" in " ".join(str(cell) for row in doc.cells(0) for cell in row)
    with pytest.raises(DocError):
        Doc(b"", password=None)


def test_camt_ofx_mt940_and_json_arrive_as_text_lines():
    camt = (
        b'<?xml version="1.0"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">'
        b'<BkToCstmrStmt><Stmt><Ntry><Amt Ccy="INR">450.00</Amt>'
        b"<CdtDbtInd>DBIT</CdtDbtInd><NtryDtls><TxDtls><RmtInf><Ustrd>SWIGGY</Ustrd>"
        b"</RmtInf></TxDtls></NtryDtls></Ntry></Stmt></BkToCstmrStmt></Document>"
    )
    doc = Doc(camt)
    assert doc.kind == "camt"
    text = " ".join(str(cell) for row in doc.cells(0) for cell in row)
    assert "SWIGGY" in text and "Amt" in text

    ofx = (
        b"OFXHEADER:100\n<OFX><BANKTRANLIST><STMTTRN><TRNAMT>-450.00</TRNAMT>"
        b"</STMTTRN></BANKTRANLIST></OFX>"
    )
    assert Doc(ofx).kind == "ofx"
    mt940 = b":20:START\n:28C:1/1\n:60F:C260401INR245678,90\n:61:2604010401D450,00NTRF\n"
    assert Doc(mt940).kind == "mt940"
    assert Doc(b'{"accounts": []}').kind == "json"


def test_pdf_page_png_is_a_png():
    doc = Doc(minimal_pdf(["Date Narration Debit Credit Balance"]))
    assert doc.page_png(0).startswith(b"\x89PNG\r\n\x1a\n")


def test_zip_is_safe_and_opens_each_member():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("one.csv", sbi_csv())
        archive.writestr("two.csv", slice_csv())
    docs = open_documents(buffer.getvalue())
    assert [doc.name for doc in docs] == ["one.csv", "two.csv"]

    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w") as archive:
        archive.writestr("../escape.csv", sbi_csv())
    with pytest.raises(DocError) as exc:
        open_documents(bomb.getvalue())
    assert exc.value.code == "zip_path"


def test_check_statement_catches_a_day_month_swap_and_a_short_chain():
    rows = [
        {"date": "2026-04-01", "debit": 100, "credit": 0, "balance": 900},
        {"date": "2026-04-02", "debit": 200, "credit": 0, "balance": 700},
    ]
    good = check_statement(rows, 1000, 700)
    assert good["status"] == "VERIFIED"
    assert good["chain_ok"] is True
    swapped = check_statement(rows, 1000, 700, period=("2026-05-01", "2026-05-31"))
    assert swapped["status"] == "DISCREPANCY"
    broken = check_statement(
        [{"date": "2026-04-01", "debit": 100, "credit": 0, "balance": 950}],
        1000,
        900,
    )
    assert broken["status"] == "DISCREPANCY"
    assert broken["chain_breaks"] == [0]


def test_checks_require_rows_unless_empty_and_compare_ledger_opening():
    empty = check_statement([], 1000, 1000, empty_statement=True)
    assert empty["status"] == "VERIFIED"
    assert empty["opening_source"] == "printed"
    manual_zero = check_statement([], None, 0, empty_statement=True, manual_opening=0)
    assert manual_zero["status"] == "VERIFIED"
    assert manual_zero["opening_source"] == "manual"
    assert manual_zero["opening_paise"] == 0
    assert check_statement([], 1000, 900, empty_statement=True)["status"] == "FAILED"
    rows = [{"date": "2026-04-01", "debit": 100, "credit": 0, "balance": 900}]
    disagreement = check_statement(rows, 1000, 900, ledger_opening=1200)
    assert disagreement["status"] == "DISCREPANCY"
    assert disagreement["opening_matches_ledger"] is False


def test_a_printed_opening_without_running_balances_still_verifies():
    rows = [{"date": "2026-04-01", "debit": 100, "credit": 0, "balance": None}]
    result = check_statement(rows, 1000, 900)
    assert result["status"] == "VERIFIED"
    assert result["opening_source"] == "printed"
    assert result["opening_paise"] == 1000


def test_a_parser_zero_is_never_labeled_printed():
    rows = [{"date": "2026-04-01", "debit": 100, "credit": 0, "balance": None}]
    result = check_statement(rows, 0, -100)
    assert result["status"] == "VERIFIED"
    assert result["opening_source"] == "parser-zero"


def test_a_manual_opening_wins_over_a_parser_claim_and_is_labeled_manual():
    rows = [
        {"date": "2026-04-01", "debit": 100, "credit": 0, "balance": None},
        {"date": "2026-04-02", "debit": 200, "credit": 0, "balance": None},
    ]
    result = check_statement(rows, 0, 700, manual_opening=1000)
    assert result["status"] == "VERIFIED"
    assert result["opening_source"] == "manual"
    assert result["opening_paise"] == 1000
    assert result["opening_matches_ledger"] is True


@pytest.mark.parametrize(
    ("payload", "parser", "opening", "closing"),
    [
        (sbi_csv(), SBI_PARSER, 24567890, 34402890),
        (union_tsv(), UNION_PARSER, 10000000, 14865000),
        (slice_csv(), SLICE_PARSER, -100000, 245000),
    ],
)
async def test_synthetic_indian_parsers_verify(payload, parser, opening, closing):
    doc = Doc(payload)
    from arno.importers.periods import derive_period

    period = derive_period(doc)
    assert period is not None
    assert period == {"start": "2026-04-01", "end": "2026-04-30", "source": "header"}
    result = await run_script(
        doc,
        parser,
        ScriptContext(
            period=(period["start"], period["end"]),
            ledger_opening_paise=opening,
            expected_fingerprint=detect_fingerprint(doc),
        ),
    )
    assert result.error is None, result.error
    accounts, reason = normalize_accounts(result.output)
    assert reason is None
    account = accounts[0]
    check = check_statement(
        account["rows"],
        account["opening"],
        account["closing"],
        period=(period["start"], period["end"]),
        ledger_opening=opening,
        headers=account["headers"],
        expected_fingerprint=detect_fingerprint(doc),
    )
    assert check["status"] == "VERIFIED", check
    assert len(account["rows"]) >= 3
    assert opening == account["opening"] if account["opening"] is not None else True
    assert closing == check["computed_closing"]


def test_normalize_accounts_keeps_a_clean_counterparty():
    accounts, reason = normalize_accounts(
        {
            "accounts": [
                {
                    "name": "SBI savings",
                    "rows": [
                        {
                            "date": "2026-04-01",
                            "description": "UPI/DR/123456789012/SWIGGY/ramesh@okaxis",
                            "debit": 45000,
                            "credit": 0,
                            "balance": 24522890,
                            "counterparty": "  SWIGGY BANGALORE  ",
                        },
                        {
                            "date": "2026-04-02",
                            "description": "INTEREST CREDIT",
                            "debit": 0,
                            "credit": 100,
                            "balance": 24522990,
                            "counterparty": "",
                        },
                    ],
                }
            ]
        }
    )
    assert reason is None
    assert accounts[0]["rows"][0]["counterparty"] == "SWIGGY BANGALORE"
    assert accounts[0]["rows"][1]["counterparty"] is None


def test_derive_period_yearless_range_takes_year_from_short_token():
    from arno.importers.periods import derive_period

    doc = Doc(b"12 JUL - 11 AUG\n14 Aug '26 \xe2\x80\xa2 UPI 100.00\n")
    assert derive_period(doc) == {
        "start": "2026-07-12",
        "end": "2026-08-11",
        "source": "header",
    }
    wrap = Doc(b"15 DEC - 14 JAN\n20 Dec '26 UPI 1.00\n02 Jan \xe2\x80\x9927 UPI 2.00\n")
    assert derive_period(wrap) == {
        "start": "2026-12-15",
        "end": "2027-01-14",
        "source": "header",
    }
    assert derive_period(Doc(b"12 JUL - 11 AUG\nno year token\n")) is None


async def test_monty_has_no_file_access():
    doc = Doc(sbi_csv())
    result = await run_script(doc, "open('/etc/passwd').read()\n")
    assert result.error is not None
