"""Synthetic non-PDF fixtures: spreadsheets, text, docx, html, ofx, mt940, camt, json, rtf, zip, broken."""
import csv
import datetime as dt
import io
import json
import zipfile
from pathlib import Path

from common import inr

HEAD = ["Date", "Value Date", "Narration", "Chq./Ref.No.", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]
EPOCH = dt.date(1899, 12, 30)


def serial(d):
    return (d - EPOCH).days


def fl(p):  # paise -> float amount (with deliberately inexact sums for balance)
    return p / 100.0


def float_balances(rows, opening):
    """Cumulative float sums, as a spreadsheet would hold them (e.g. 1234.5600000000002)."""
    out, b = [], opening / 100.0
    for r in rows:
        b = b + (r["credit"] - r["debit"]) / 100.0
        out.append(b)
    return out


# ---------------------------------------------------------------- xlsx / xls / ods
def write_xlsx(path, rows, opening, closing, acno="XXXX1234", period="01-Jan-2026 to 31-Jan-2026"):
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Statement"
    ws.merge_cells("A1:G1"); ws["A1"] = "EXAMPLE BANK LTD - ACCOUNT STATEMENT"; ws["A1"].font = Font(bold=True)
    ws["A2"] = "Account Holder"; ws["C2"] = "MR. ARJUN KRISHNAN"
    ws["A3"] = "Account No"; ws["C3"] = acno
    ws.merge_cells("A4:G4"); ws["A4"] = f"Statement Period: {period}"
    ws["A5"] = "Opening Balance"; ws["C5"] = opening / 100.0
    ws["A6"] = "Closing Balance"; ws["C6"] = closing / 100.0
    ws.merge_cells("C7:D7"); ws["C7"] = "Transaction Details"
    for i, h in enumerate(HEAD):
        ws.cell(row=8, column=i + 1, value=h).font = Font(bold=True)
    bals = float_balances(rows, opening)
    for i, r in enumerate(rows):
        rr = 9 + i
        c = ws.cell(row=rr, column=1, value=dt.datetime.combine(r["date"], dt.time())); c.number_format = "DD/MM/YYYY"
        ws.cell(row=rr, column=2, value=float(serial(r["date"])))          # bare serial, no date style
        ws.cell(row=rr, column=3, value=r["desc"])
        ws.cell(row=rr, column=4, value=r["ref"] or None)
        if r["debit"]:
            ws.cell(row=rr, column=5, value=fl(r["debit"]))
        if r["credit"]:
            ws.cell(row=rr, column=6, value=fl(r["credit"]))
        ws.cell(row=rr, column=7, value=bals[i])
    end = 9 + len(rows)
    ws.cell(row=end + 1, column=3, value="Total")
    ws.cell(row=end + 1, column=5, value=sum(r["debit"] for r in rows) / 100.0)
    ws.cell(row=end + 1, column=6, value=sum(r["credit"] for r in rows) / 100.0)
    ws.cell(row=end + 3, column=1, value="*** This is a system generated statement ***")
    ws2 = wb.create_sheet("Notes"); ws2["A1"] = "Disclaimer: please verify with your branch."
    wb.save(path)


def write_xls(path, rows, opening, closing, acno="XXXX1234"):
    import xlwt
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Statement")
    bold = xlwt.easyxf("font: bold on")
    datef = xlwt.easyxf(num_format_str="DD/MM/YYYY")
    ws.write_merge(0, 0, 0, 6, "EXAMPLE BANK LTD - ACCOUNT STATEMENT", bold)
    ws.write(1, 0, "Account No"); ws.write(1, 2, acno)
    ws.write(2, 0, "Opening Balance"); ws.write(2, 2, opening / 100.0)
    ws.write(3, 0, "Closing Balance"); ws.write(3, 2, closing / 100.0)
    for i, h in enumerate(HEAD):
        ws.write(5, i, h, bold)
    bals = float_balances(rows, opening)
    for i, r in enumerate(rows):
        rr = 6 + i
        ws.write(rr, 0, dt.datetime.combine(r["date"], dt.time()), datef)
        ws.write(rr, 1, r["date"].strftime("%d/%m/%y"))
        ws.write(rr, 2, r["desc"])
        ws.write(rr, 3, r["ref"])
        if r["debit"]:
            ws.write(rr, 4, fl(r["debit"]))
        if r["credit"]:
            ws.write(rr, 5, fl(r["credit"]))
        ws.write(rr, 6, bals[i])
    ws.write(7 + len(rows), 0, "*** End of statement ***")
    wb.save(str(path))


def write_html_xls(path, rows, opening, closing):
    s = ["<html><head><meta http-equiv='Content-Type' content='text/html; charset=utf-8'></head><body>",
         "<table><tr><td colspan=7><b>EXAMPLE BANK LTD</b></td></tr>",
         f"<tr><td>Opening Balance</td><td>{inr(opening)}</td></tr>",
         f"<tr><td>Closing Balance</td><td>{inr(closing)}</td></tr></table><br>",
         "<table border=1><tr>" + "".join(f"<th>{h}</th>" for h in HEAD) + "</tr>"]
    for r in rows:
        s.append(f"<tr><td>{r['date']:%d/%m/%Y}</td><td>{r['date']:%d/%m/%y}</td><td>{r['desc']}</td><td>{r['ref']}</td>"
                 f"<td align=right>{inr(r['debit']) if r['debit'] else ''}</td>"
                 f"<td align=right>{inr(r['credit']) if r['credit'] else ''}</td><td align=right>{inr(r['balance'])}</td></tr>")
    s.append("</table></body></html>")
    Path(path).write_text("\n".join(s), encoding="utf-8")


def write_tsv_xls(path, rows, opening, closing):
    lines = ["EXAMPLE BANK LTD", f"Opening Balance\t{inr(opening)}", f"Closing Balance\t{inr(closing)}", "", "\t".join(HEAD)]
    for r in rows:
        lines.append("\t".join([f"{r['date']:%d/%m/%Y}", f"{r['date']:%d/%m/%y}", r["desc"], r["ref"],
                                inr(r["debit"]) if r["debit"] else "", inr(r["credit"]) if r["credit"] else "",
                                inr(r["balance"])]))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_ods(path, rows, opening, closing):
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.table import Table, TableRow, TableCell
    from odf.text import P
    doc = OpenDocumentSpreadsheet()
    t = Table(name="Statement")
    doc.spreadsheet.addElement(t)

    def mk(row, cells):
        tr = TableRow()
        for c in cells:
            if c is None or c == "":
                tc = TableCell()
            elif isinstance(c, dt.date):
                tc = TableCell(valuetype="date", datevalue=c.isoformat()); tc.addElement(P(text=c.isoformat()))
            elif isinstance(c, float):
                tc = TableCell(valuetype="float", value=repr(c)); tc.addElement(P(text=str(c)))
            else:
                tc = TableCell(valuetype="string"); tc.addElement(P(text=str(c)))
            tr.addElement(tc)
        t.addElement(tr)
    mk(0, ["EXAMPLE BANK LTD - ACCOUNT STATEMENT"])
    mk(0, ["Opening Balance", "", opening / 100.0])
    mk(0, ["Closing Balance", "", closing / 100.0])
    mk(0, [])
    mk(0, HEAD)
    bals = float_balances(rows, opening)
    for i, r in enumerate(rows):
        mk(0, [r["date"], r["date"].strftime("%d/%m/%y"), r["desc"], r["ref"],
               fl(r["debit"]) if r["debit"] else None, fl(r["credit"]) if r["credit"] else None, bals[i]])
    doc.save(str(path))


def encrypt_xlsx(src, dst, password):
    import msoffcrypto
    with open(src, "rb") as f:
        of = msoffcrypto.OfficeFile(f)
        with open(dst, "wb") as out:
            of.encrypt(password, out)


# ---------------------------------------------------------------- csv / txt
def write_csv_bom(path, rows, opening, closing):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Narration", "Ref No", "Debit", "Credit", "Balance"])
        for r in rows:
            w.writerow([r["date"].strftime("%d-%b-%Y"), r["desc"], r["ref"],
                        f"{r['debit'] / 100:.2f}" if r["debit"] else "", f"{r['credit'] / 100:.2f}" if r["credit"] else "",
                        f"{r['balance'] / 100:.2f}"])


def write_csv_cp1252(path, rows, opening, closing):
    lines = ["Account Statement;;;;;", "Customer;ARJUN KRISHNAN;;;;", f"Opening Balance;\"{inr(opening)}\";;;;",
             f"Closing Balance;\"{inr(closing)}\";;;;", ";;;;;", "Date;Narration;Ref;Withdrawals;Deposits;Balance"]
    for i, r in enumerate(rows):
        desc = r["desc"]
        lines.append(f"{r['date']:%d.%m.%Y};{desc};{r['ref']};"
                     f"{'\"' + inr(r['debit']) + '\"' if r['debit'] else ''};{'\"' + inr(r['credit']) + '\"' if r['credit'] else ''};"
                     f"\"{inr(r['balance'])}\"")
    lines += [";;;;;", "Total;;;" + f"\"{inr(sum(r['debit'] for r in rows))}\";\"{inr(sum(r['credit'] for r in rows))}\";", "*** End of Statement ***"]
    Path(path).write_bytes("\r\n".join(lines).encode("cp1252", errors="replace"))
    return lines


def write_tsv(path, rows, opening, closing):
    lines = ["Date\tDescription\tDebit\tCredit\tBalance"]
    for r in rows:
        lines.append(f"{r['date']:%Y-%m-%d}\t{r['desc']}\t{r['debit'] / 100:.2f}\t{r['credit'] / 100:.2f}\t{r['balance'] / 100:.2f}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_fixed(path, rows, opening, closing):
    out = ["EXAMPLE BANK LTD            STATEMENT OF ACCOUNT", f"Opening Balance : {inr(opening):>15}", ""]
    out.append(f"{'Date':<11}{'Particulars':<42}{'Debit':>14}{'Credit':>14}{'Balance':>16}")
    out.append("-" * 97)
    for r in rows:
        out.append(f"{r['date']:%d/%m/%Y}  {r['desc'][:40]:<40}{inr(r['debit']) if r['debit'] else '':>14}"
                   f"{inr(r['credit']) if r['credit'] else '':>14}{inr(r['balance']):>16}")
    out += ["-" * 97, f"Closing Balance : {inr(closing):>15}"]
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- docx / html / rtf
def write_docx_table(path, rows, opening, closing):
    import docx
    d = docx.Document()
    d.add_heading("Example Bank Ltd - Statement of Account", 1)
    d.add_paragraph(f"Opening Balance: {inr(opening)}")
    d.add_paragraph(f"Closing Balance: {inr(closing)}")
    t = d.add_table(rows=1, cols=5)
    for i, h in enumerate(["Date", "Narration", "Debit", "Credit", "Balance"]):
        t.rows[0].cells[i].text = h
    for r in rows:
        c = t.add_row().cells
        c[0].text = f"{r['date']:%d/%m/%Y}"; c[1].text = r["desc"]
        c[2].text = inr(r["debit"]) if r["debit"] else ""; c[3].text = inr(r["credit"]) if r["credit"] else ""
        c[4].text = inr(r["balance"])
    d.save(path)


def write_docx_paras(path, rows, opening, closing):
    import docx
    d = docx.Document()
    d.add_heading("Example Bank Ltd - Statement of Account", 1)
    d.add_paragraph(f"Opening Balance: {inr(opening)}")
    for r in rows:
        amt = r["debit"] or r["credit"]
        d.add_paragraph(f"{r['date']:%d/%m/%Y}  {r['desc']}  {inr(amt)} {'Dr' if r['debit'] else 'Cr'}  Bal {inr(r['balance'])}")
    d.add_paragraph(f"Closing Balance: {inr(closing)}")
    d.save(path)


def write_html(path, rows, opening, closing):
    s = ["<!DOCTYPE html><html><head><meta charset='utf-8'><title>Account Statement</title>",
         "<style>.hdr{color:#036}</style><script>var x = '<tr><td>FAKE ROW 99/99/9999</td></tr>';</script></head><body>",
         "<div id='nav'><a href='#'>Home</a> | <a href='#'>Logout</a></div>",
         "<table class='layout'><tr><td><div class='hdr'>Account Statement</div>",
         f"<span>Opening Balance:</span> <b>&#8377;&nbsp;{inr(opening)}</b><br>",
         f"<span>Closing Balance:</span> <b>&#8377;&nbsp;{inr(closing)}</b></td></tr><tr><td>",
         "<table id='txn' class='grid'><thead><tr><th>Txn Date</th><th>Description</th><th>Dr/Cr</th><th>Amount</th><th>Balance</th></tr></thead><tbody>"]
    for r in rows:
        amt = r["debit"] or r["credit"]
        s.append(f"<tr><td>{r['date']:%d %b %Y}</td><td>{r['desc'].replace('&', '&amp;')}</td>"
                 f"<td>{'DR' if r['debit'] else 'CR'}</td><td class='n'>&#8377;&nbsp;{inr(amt)}</td><td class='n'>{inr(r['balance'])}</td></tr>")
    s.append("</tbody></table></td></tr></table><div>Page 1 of 1 <a href='#'>Next</a></div></body></html>")
    Path(path).write_text("\n".join(s), encoding="utf-8")


def write_rtf(path, rows, opening, closing):
    def esc(t):
        return t.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    out = [r"{\rtf1\ansi\deff0{\fonttbl{\f0 Arial;}}", r"\f0\fs20 EXAMPLE BANK LTD STATEMENT\par",
           f"Opening Balance: {inr(opening)}\\par"]
    def row(cells):
        s = r"\trowd\trgaph108"
        for i in range(len(cells)):
            s += rf"\cellx{1500 * (i + 1) + (3000 if i >= 1 else 0)}"
        return s + "".join(f"\\intbl {esc(c)}\\cell" for c in cells) + r"\row"
    out.append(row(["Date", "Narration", "Debit", "Credit", "Balance"]))
    for r in rows:
        out.append(row([f"{r['date']:%d/%m/%Y}", r["desc"], inr(r["debit"]) if r["debit"] else "",
                        inr(r["credit"]) if r["credit"] else "", inr(r["balance"])]))
    out.append(f"\\pard Closing Balance: {inr(closing)}\\par" + "}")
    Path(path).write_text("\n".join(out), encoding="ascii", errors="replace")


# ---------------------------------------------------------------- bank interchange formats
def write_ofx(path, rows, opening, closing, v2=False):
    ext = ".qfx" if v2 else ".ofx"
    def tag(t, v, close=False):
        return f"<{t}>{v}</{t}>" if close else f"<{t}>{v}"
    c = v2
    o = []
    if v2:
        o += ['<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
              '<?OFX OFXHEADER="200" VERSION="220" SECURITY="NONE" OLDFILEUID="NONE" NEWFILEUID="NONE"?>']
    else:
        o += ["OFXHEADER:100", "DATA:OFXSGML", "VERSION:102", "SECURITY:NONE", "ENCODING:USASCII", "CHARSET:1252",
              "COMPRESSION:NONE", "OLDFILEUID:NONE", "NEWFILEUID:NONE", ""]
    o += ["<OFX>", "<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS><DTSERVER>20260201000000[+5.5:IST]<LANGUAGE>ENG</SONRS></SIGNONMSGSRSV1>" if not v2 else
          "<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS><DTSERVER>20260201000000</DTSERVER><LANGUAGE>ENG</LANGUAGE></SONRS></SIGNONMSGSRSV1>",
          "<BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS><STMTRS><CURDEF>INR" if not v2 else
          "<BANKMSGSRSV1><STMTTRNRS><TRNUID>1</TRNUID><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS><STMTRS><CURDEF>INR</CURDEF>",
          "<BANKACCTFROM><BANKID>EXMP0001234<ACCTID>XXXX1234<ACCTTYPE>SAVINGS</BANKACCTFROM>" if not v2 else
          "<BANKACCTFROM><BANKID>EXMP0001234</BANKID><ACCTID>XXXX1234</ACCTID><ACCTTYPE>SAVINGS</ACCTTYPE></BANKACCTFROM>",
          f"<BANKTRANLIST><DTSTART>{rows[0]['date']:%Y%m%d}<DTEND>{rows[-1]['date']:%Y%m%d}" if not v2 else
          f"<BANKTRANLIST><DTSTART>{rows[0]['date']:%Y%m%d}</DTSTART><DTEND>{rows[-1]['date']:%Y%m%d}</DTEND>"]
    for i, r in enumerate(rows):
        amt = (r["credit"] - r["debit"]) / 100.0
        typ = "CREDIT" if r["credit"] else "DEBIT"
        name = r["desc"][:32].replace("&", "&amp;").replace("<", "&lt;")
        memo = r["desc"].replace("&", "&amp;").replace("<", "&lt;")
        if v2:
            o.append(f"<STMTTRN><TRNTYPE>{typ}</TRNTYPE><DTPOSTED>{r['date']:%Y%m%d}120000[+5.5:IST]</DTPOSTED>"
                     f"<TRNAMT>{amt:.2f}</TRNAMT><FITID>{r['date']:%Y%m%d}{i:04d}</FITID><NAME>{name}</NAME><MEMO>{memo}</MEMO></STMTTRN>")
        else:
            o.append(f"<STMTTRN><TRNTYPE>{typ}<DTPOSTED>{r['date']:%Y%m%d}120000[+5.5:IST]<TRNAMT>{amt:.2f}"
                     f"<FITID>{r['date']:%Y%m%d}{i:04d}<NAME>{name}<MEMO>{memo}</STMTTRN>")
    if v2:
        o += ["</BANKTRANLIST>", f"<LEDGERBAL><BALAMT>{closing / 100:.2f}</BALAMT><DTASOF>{rows[-1]['date']:%Y%m%d}</DTASOF></LEDGERBAL>",
              "</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"]
    else:
        o += ["</BANKTRANLIST>", f"<LEDGERBAL><BALAMT>{closing / 100:.2f}<DTASOF>{rows[-1]['date']:%Y%m%d}</LEDGERBAL>",
              "</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"]
    Path(path).write_text("\n".join(o), encoding="ascii", errors="replace")


def _mt_amt(p):
    return f"{p // 100},{p % 100:02d}"


def write_mt940(path, rows, opening, closing):
    first, last = rows[0]["date"], rows[-1]["date"]
    o = [":20:STMT20260101", ":25:EXMP0001234/XXXX1234", ":28C:00001/001",
         f":60F:C{first:%y%m%d}INR{_mt_amt(opening)}"]
    for i, r in enumerate(rows):
        amt = r["debit"] or r["credit"]
        o.append(f":61:{r['date']:%y%m%d}{r['date']:%m%d}{'D' if r['debit'] else 'C'}{_mt_amt(amt)}NTRFNONREF//{i:08d}")
        d = r["desc"]
        o.append(f":86:{d[:65]}")
        if len(d) > 65:
            o.append(d[65:130])
    o.append(f":62F:C{last:%y%m%d}INR{_mt_amt(closing)}")
    o.append("-")
    Path(path).write_text("\n".join(o) + "\n", encoding="ascii", errors="replace")


def write_camt(path, rows, opening, closing):
    ns = "urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"
    o = ['<?xml version="1.0" encoding="UTF-8"?>', f'<Document xmlns="{ns}"><BkToCstmrStmt>',
         "<GrpHdr><MsgId>STMT1</MsgId><CreDtTm>2026-02-01T00:00:00</CreDtTm></GrpHdr><Stmt><Id>S1</Id>",
         "<Acct><Id><Othr><Id>XXXX1234</Id></Othr></Id><Ccy>INR</Ccy></Acct>",
         f'<Bal><Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp><Amt Ccy="INR">{opening / 100:.2f}</Amt><CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>{rows[0]["date"]:%Y-%m-%d}</Dt></Dt></Bal>',
         f'<Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><Amt Ccy="INR">{closing / 100:.2f}</Amt><CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>{rows[-1]["date"]:%Y-%m-%d}</Dt></Dt></Bal>']
    for r in rows:
        amt = r["debit"] or r["credit"]
        d = r["desc"].replace("&", "&amp;").replace("<", "&lt;")
        o.append(f'<Ntry><Amt Ccy="INR">{amt / 100:.2f}</Amt><CdtDbtInd>{"DBIT" if r["debit"] else "CRDT"}</CdtDbtInd><Sts>BOOK</Sts>'
                 f'<BookgDt><Dt>{r["date"]:%Y-%m-%d}</Dt></BookgDt><ValDt><Dt>{r["date"]:%Y-%m-%d}</Dt></ValDt>'
                 f'<AddtlNtryInf>{d}</AddtlNtryInf></Ntry>')
    o.append("</Stmt></BkToCstmrStmt></Document>")
    Path(path).write_text("\n".join(o), encoding="utf-8")


def write_json_aa(path, rows, opening, closing):
    txns = []
    for i, r in enumerate(rows):
        amt = r["debit"] or r["credit"]
        txns.append({"txnId": f"T{i:06d}", "type": "DEBIT" if r["debit"] else "CREDIT", "mode": "UPI" if "UPI" in r["desc"] else "OTHERS",
                     "amount": f"{amt / 100:.2f}", "currentBalance": f"{r['balance'] / 100:.2f}",
                     "transactionTimestamp": f"{r['date']:%Y-%m-%d}T10:15:00+05:30", "valueDate": f"{r['date']:%Y-%m-%d}",
                     "narration": r["desc"], "reference": r["ref"]})
    doc = {"ver": "1.1", "fip": "EXAMPLE-FIP", "account": {"type": "deposit", "maskedAccNumber": "XXXX1234",
           "summary": {"currentBalance": f"{closing / 100:.2f}", "openingBalance": f"{opening / 100:.2f}"},
           "transactions": {"startDate": f"{rows[0]['date']}", "endDate": f"{rows[-1]['date']}", "transaction": txns}}}
    Path(path).write_text(json.dumps(doc, indent=1), encoding="utf-8")


def write_zip(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, Path(f).name)
