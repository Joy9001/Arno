"""Synthetic Indian bank statement fixtures. No real statement data, ever."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from arno.importers.extract import ExtractedAccount, ExtractedRow, ExtractedStatement

SBI_ROWS = [
    (
        "01/04/2026",
        "UPI/DR/123456789012/SWIGGY BANGALORE/ramesh@okaxis",
        "123456789012",
        "450.00",
        "",
        "245228.90",
    ),
    ("05/04/2026", "NEFT CR/ACME PAYROLL/SALARY APRIL", "NEFT0426", "", "100000.00", "345228.90"),
    (
        "12/04/2026",
        "UPI/DR/223456789012/AMAZON PAY INDIA/amazon@okhdfcbank",
        "223456789012",
        "1200.00",
        "",
        "344028.90",
    ),
]


def sbi_csv() -> bytes:
    lines = [
        "State Bank of India",
        "Savings Account Statement",
        "Account Number: 50100123456789",
        "Statement Period: 01/04/2026 to 30/04/2026",
        '"Opening Balance: 2,45,678.90"',
        "Date,Narration,Chq/Ref No,Debit,Credit,Balance",
    ]
    lines += [",".join(row) for row in SBI_ROWS]
    lines += ['"Closing Balance: 3,44,028.90"', "Page 1 of 1"]
    return ("\n".join(lines) + "\n").encode("utf-8")


def minimal_xlsx(rows: list[list[Any]]) -> bytes:
    """A small xlsx with inline strings, enough for Calamine."""

    def column(index: int) -> str:
        result = ""
        index += 1
        while index:
            index, remainder = divmod(index - 1, 26)
            result = chr(65 + remainder) + result
        return result

    cells = []
    for row_index, row in enumerate(rows, 1):
        parts = []
        for column_index, value in enumerate(row):
            ref = f"{column(column_index)}{row_index}"
            if isinstance(value, (int, float)):
                parts.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                text = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                parts.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        cells.append(f'<row r="{row_index}">' + "".join(parts) + "</row>")
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>" + "".join(cells) + "</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Statement" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


def minimal_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with Helvetica text at fixed positions, built with the standard library."""
    y = 800
    operations = []
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        operations.append(f"BT /F1 8 Tf 30 {y} Td ({escaped}) Tj ET")
        y -= 12
    content = "\n".join(operations).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _paise(text: str) -> int | None:
    cleaned = text.replace(",", "").replace("Dr", "").replace("Cr", "").strip()
    try:
        return int(Decimal(cleaned) * 100)
    except InvalidOperation:
        return None


def _iso(text: str) -> str | None:
    try:
        return datetime.strptime(text.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def statement_from_csv(
    data: bytes, *, layout: str, name: str, institution: str, account_number: str | None = None
) -> ExtractedStatement:
    """What extraction returns for this module's CSV statements.

    `bank` prints its opening and closing, `wallet` prints neither, and `card` prints the amount
    owed (negated to ledger sign) with signed amounts in one column.
    """
    table = list(csv.reader(io.StringIO(data.decode("utf-8"))))
    sign = -1 if layout == "card" else 1
    opening = closing = None
    if layout != "wallet":
        for row in table:
            text = " ".join(row)
            value = _paise(text.split(":")[-1]) if ":" in text else None
            if value is None:
                continue
            if "opening balance" in text.lower():
                opening = sign * value
            if "closing balance" in text.lower():
                closing = sign * value
    rows: list[ExtractedRow] = []
    for row in table:
        day = _iso(row[0]) if row else None
        if day is None:
            continue
        description = row[1]
        if layout == "card":
            amount = _paise(row[2]) or 0
            rows.append(
                ExtractedRow(
                    date=day,
                    description=description,
                    debit=max(-amount, 0),
                    credit=max(amount, 0),
                    counterparty=description[:120],
                )
            )
            continue
        counterparty = None
        if layout == "bank":
            for part in (piece.strip() for piece in description.split("/")):
                if part and "@" not in part and not part.isdigit() and part.upper() not in _RAILS:
                    counterparty = part[:120]
                    break
        rows.append(
            ExtractedRow(
                date=day,
                description=description,
                debit=_paise(row[3]) or 0,
                credit=_paise(row[4]) or 0,
                balance=_paise(row[5]),
                counterparty=counterparty,
            )
        )
    return ExtractedStatement(
        account=ExtractedAccount(name=name, institution=institution, account_number=account_number),
        opening=opening,
        closing=closing,
        rows=rows,
    )


_RAILS = ("UPI", "DR", "CR", "NEFT", "IMPS", "NEFT CR")


def card_statement(
    rows: list[tuple[str, str, str]],
    closing: str,
    *,
    start: str = "01/04/2026",
    end: str = "30/04/2026",
) -> bytes:
    """A card statement that prints the amount owed, as a real one does."""
    lines = [
        "Slice",
        "Credit Card Statement",
        f"Statement Period: {start} to {end}",
        "Date,Transaction Details,Amount",
    ]
    lines += [",".join(row) for row in rows]
    lines += [f'"Closing Balance: {closing}"']
    return ("\n".join(lines) + "\n").encode("utf-8")


def _figure(paise: int, *, grouping: bool = False) -> str:
    value = abs(int(paise))
    whole, part = f"{value // 100}", value % 100
    if grouping:
        whole = f"{int(whole):,}"
    return f"{whole}.{part:02d}"


def wallet_statement(
    *,
    period: tuple[str, str],
    opening_paise: int,
    rows: list[tuple[str, str, int, int]],
    ref_header: str = "Chq/Ref No",
    institution: str = "PhonePe",
) -> bytes:
    """A wallet-style CSV with a printed opening and closing and running balances."""
    running = opening_paise
    lines = [
        institution,
        "Savings Account Statement",
        f"Statement Period: {period[0]} to {period[1]}",
        f'"Opening Balance: {_figure(opening_paise, grouping=True)}"',
        f"Date,Narration,{ref_header},Debit,Credit,Balance",
    ]
    for day, narration, debit, credit in rows:
        running += credit - debit
        cells = [
            day,
            narration,
            "",
            _figure(debit) if debit else "",
            _figure(credit) if credit else "",
            _figure(running),
        ]
        lines.append(",".join(cells))
    lines.append(f'"Closing Balance: {_figure(running, grouping=True)}"')
    lines.append("Page 1 of 1")
    return ("\n".join(lines) + "\n").encode("utf-8")


__all__ = [
    "card_statement",
    "minimal_pdf",
    "minimal_xlsx",
    "sbi_csv",
    "statement_from_csv",
    "wallet_statement",
]
