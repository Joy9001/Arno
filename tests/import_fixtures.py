"""Synthetic Indian bank statement fixtures. No real statement data, ever."""

from __future__ import annotations

import io
import zipfile
from typing import Any

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

UNION_ROWS = [
    (
        "02/04/2026",
        "UPI/DR/999999999999/AMAZON/amazon@okhdfcbank",
        "999999999999",
        "1000.00 Dr",
        "99000.00",
    ),
    ("10/04/2026", "SALARY APRIL", "", "50000.00 Cr", "149000.00"),
    (
        "18/04/2026",
        "UPI/DR/888888888888/SWIGGY/swiggy@okaxis",
        "888888888888",
        "350.00 Dr",
        "148650.00",
    ),
]

SLICE_ROWS = [
    ("03/04/2026", "SWIGGY BANGALORE", "-350.00"),
    ("05/04/2026", "PAYMENT RECEIVED - THANK YOU", "5000.00"),
    ("09/04/2026", "AMAZON PAY INDIA MUMBAI", "-1200.00"),
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


def union_tsv() -> bytes:
    lines = [
        "Union Bank of India",
        "Statement Period: 01/04/2026 to 30/04/2026",
        "Opening Balance: 1,00,000.00",
        "Date\tParticulars\tRef\tAmount\tBalance",
    ]
    lines += ["\t".join(row) for row in UNION_ROWS]
    lines += ["Closing Balance: 1,48,650.00"]
    return ("\n".join(lines) + "\n").encode("utf-8")


def slice_csv() -> bytes:
    lines = [
        "Slice",
        "Credit Card Statement",
        "Statement Period: 01/04/2026 to 30/04/2026",
        "Date,Transaction Details,Amount",
    ]
    lines += [",".join(row) for row in SLICE_ROWS]
    lines += ['"Closing Balance: 4,450.00"']
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


SBI_PARSER = r"""
rows = await doc_cells(0)
header = None
header_index = -1
for i, row in enumerate(rows):
    joined = ' '.join(str(c) for c in row).lower()
    if 'narration' in joined and 'debit' in joined and 'credit' in joined:
        header = row
        header_index = i
        break
if header is None:
    result = {'not_a_statement': 'unknown'}
else:
    opening = None
    closing = None
    out = []
    for row in rows:
        text = ' '.join(str(c) for c in row)
        low = text.lower()
        if 'opening balance' in low:
            opening = money(text.split(':')[-1])
        if 'closing balance' in low:
            closing = money(text.split(':')[-1])
    for row in rows[header_index + 1:]:
        if not row or not to_iso(row[0]):
            continue
        out.append({'date': to_iso(row[0]), 'description': str(row[1]),
                    'debit': money(row[3]) or 0, 'credit': money(row[4]) or 0,
                    'balance': money(row[5])})
    result = {'accounts': [{'name': 'SBI savings', 'institution': 'SBI',
                   'account_number': '50100123456789', 'opening': opening,
                   'closing': closing, 'headers': header, 'rows': out}]}
result
"""

UNION_PARSER = r"""
rows = await doc_cells(0)
header = None
header_index = -1
for i, row in enumerate(rows):
    joined = ' '.join(str(c) for c in row).lower()
    if 'particulars' in joined and 'amount' in joined and 'balance' in joined:
        header = row
        header_index = i
        break
if header is None:
    result = {'not_a_statement': 'unknown'}
else:
    opening = None
    closing = None
    for row in rows:
        text = ' '.join(str(c) for c in row)
        low = text.lower()
        if 'opening balance' in low:
            opening = money(text.split(':')[-1])
        if 'closing balance' in low:
            closing = money(text.split(':')[-1])
    out = []
    for row in rows[header_index + 1:]:
        if not row or not to_iso(row[0]):
            continue
        amount = str(row[3]).strip()
        debit = 0
        credit = 0
        if amount.lower().endswith('dr'):
            debit = money(amount[:-2]) or 0
        elif amount.lower().endswith('cr'):
            credit = money(amount[:-2]) or 0
        out.append({'date': to_iso(row[0]), 'description': str(row[1]),
                    'debit': debit, 'credit': credit, 'balance': money(row[4])})
    result = {'accounts': [{'name': 'Union Bank', 'institution': 'Union Bank of India',
                   'opening': opening, 'closing': closing,
                   'headers': header, 'rows': out}]}
result
"""

SLICE_PARSER = r"""
rows = await doc_cells(0)
header = None
header_index = -1
for i, row in enumerate(rows):
    joined = ' '.join(str(c) for c in row).lower()
    if 'transaction details' in joined and 'amount' in joined:
        header = row
        header_index = i
        break
if header is None:
    result = {'not_a_statement': 'unknown'}
else:
    closing = None
    for row in rows:
        text = ' '.join(str(c) for c in row)
        if 'closing balance' in text.lower():
            closing = money(text.split(':')[-1])
    out = []
    for row in rows[header_index + 1:]:
        if not row or not to_iso(row[0]):
            continue
        amount = money(row[2]) or 0
        out.append({'date': to_iso(row[0]), 'description': str(row[1]),
                    'debit': -amount if amount < 0 else 0,
                    'credit': amount if amount > 0 else 0, 'balance': None})
    result = {'accounts': [{'name': 'Slice card', 'institution': 'Slice',
                   'opening': None, 'closing': closing,
                   'headers': header, 'rows': out}]}
result
"""

__all__ = [
    "SBI_PARSER",
    "SLICE_PARSER",
    "UNION_PARSER",
    "minimal_pdf",
    "minimal_xlsx",
    "sbi_csv",
    "slice_csv",
    "union_tsv",
]
