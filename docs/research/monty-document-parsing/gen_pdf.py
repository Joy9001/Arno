"""Synthetic PDF statement fixtures (A1-A9, size cases, non-statements). reportlab, test-only."""
import io
import textwrap

from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.pdfencrypt import StandardEncryption

from common import inr

_FONTS = {}


def font(name):
    if name in ("Helvetica", "Helvetica-Bold", "Courier"):
        return name
    if name not in _FONTS:
        if name == "Arial":
            pdfmetrics.registerFont(TTFont("Arial", r"C:\Windows\Fonts\arial.ttf"))
        elif name == "Nirmala":
            pdfmetrics.registerFont(TTFont("Nirmala", r"C:\Windows\Fonts\Nirmala.ttc", subfontIndex=0))
        _FONTS[name] = True
    return name


class Cols:
    """x positions for a table. Right-aligned numeric columns use right edges."""
    def __init__(self, W, style):
        s = W / 595.0
        if style == "sep":
            self.date, self.narr, self.ref = 30 * s, 92 * s, 282 * s
            self.deb, self.cre, self.bal = 392 * s, 462 * s, 545 * s
            self.heads = [("Date", self.date, "l"), ("Narration", self.narr, "l"), ("Chq/Ref No", self.ref, "l"),
                          ("Debit", self.deb, "r"), ("Credit", self.cre, "r"), ("Balance", self.bal, "r")]
        elif style in ("drcr", "signed"):
            self.date, self.narr, self.ref = 30 * s, 92 * s, 300 * s
            self.amt, self.bal = 450 * s, 545 * s
            self.heads = [("Date", self.date, "l"), ("Particulars", self.narr, "l"), ("Ref", self.ref, "l"),
                          ("Amount", self.amt, "r"), ("Balance", self.bal, "r")]
        elif style == "card":
            self.date, self.narr = 30 * s, 100 * s
            self.amt = 540 * s
            self.heads = [("Date", self.date, "l"), ("Transaction Details", self.narr, "l"),
                          ("Amount (Rs.)", self.amt, "r")]
        self.narr_w = {"sep": 190 * s, "drcr": 205 * s, "signed": 205 * s, "card": 330 * s}[style]


def fit_rows(rows, style, page_size=A4, fontname="Helvetica", fsize=8, wrap=False):
    """Single-line layouts: cut each narration to its column width (no ellipsis); returns new row dicts."""
    if wrap:
        return rows
    from reportlab.pdfbase.pdfmetrics import stringWidth
    cols = Cols(page_size[0], style)
    fn = font(fontname)
    out = []
    for r in rows:
        d = r["desc"]
        while d and stringWidth(d, fn, fsize) > cols.narr_w - 6:
            d = d[:-1]
        out.append(dict(r, desc=d.rstrip()))
    return out


_DATE_FMT = ["%d/%m/%Y"]


def _dmy(d):
    return d.strftime(_DATE_FMT[0])


def draw_pdf(accounts, *, style="sep", wrap=False, page_size=A4, per_page=46, repeat_head=True,
             footers=False, summary=False, bf_rows=False, fontname="Helvetica", rotate=0,
             encrypt=None, title="EXAMPLE BANK LIMITED", amount_sym="", buf=None, total_pages=None,
             new_page_per_account=False, fsize=8, upright_rot=False, canvas_pagesize=None, variant=None, date_fmt="%d/%m/%Y"):
    """accounts: list of dict(name, acno, opening, closing, period, rows[date,desc,ref,debit,credit,balance]).
    Returns (bytes, pages)."""
    buf = buf or io.BytesIO()
    _DATE_FMT[0] = date_fmt
    kw = {}
    if encrypt:
        kw["encrypt"] = StandardEncryption(userPassword=encrypt, ownerPassword=encrypt + "-owner", canPrint=1)
    W, H = page_size
    c = canvas.Canvas(buf, pagesize=canvas_pagesize or ((H, W) if upright_rot else page_size), **kw)
    cols = Cols(W, style)
    if variant == "swap":              # Credit column now comes before Debit (credit at the left slot, debit at the right)
        cols.deb, cols.cre = cols.cre, cols.deb
        cols.heads = [("Date", cols.date, "l"), ("Narration", cols.narr, "l"), ("Chq/Ref No", cols.ref, "l"),
                      ("Credit", cols.cre, "r"), ("Debit", cols.deb, "r"), ("Balance", cols.bal, "r")]
    if variant == "valuedate":         # a 'Value Date' column is inserted after Date; narration moves right
        cols.narr = 150 * W / 595.0
        cols.heads = [("Date", cols.date, "l"), ("Value Date", 92 * W / 595.0, "l"), ("Narration", cols.narr, "l"),
                      ("Chq/Ref No", cols.ref, "l"), ("Debit", cols.deb, "r"), ("Credit", cols.cre, "r"), ("Balance", cols.bal, "r")]
        cols.narr_w = 128 * W / 595.0
    fn = font(fontname)
    fb = "Helvetica-Bold" if fontname == "Helvetica" else fn
    state = {"page": 1}
    LH = 11

    def page_header(first):
        if rotate:
            c.setPageRotation(rotate)
        if upright_rot:                      # landscape layout drawn sideways on a portrait box, /Rotate 90 puts it upright
            c.setPageRotation(90)
            c.translate(H, 0)
            c.rotate(90)
        c.setFont(fb, 11)
        c.drawString(30, H - 36, title)
        c.setFont(fn, 7)
        c.drawRightString(W - 30, H - 36, f"Page {state['page']}" + (f" of {total_pages}" if total_pages else ""))
        return H - 52

    def page_footer():
        if footers:
            c.setFont(fn, 6.5)
            c.drawString(30, 40, "Registered Office: 1, Example Tower, MG Road, Bengaluru 560001. CIN L00000KA1994PLC000000")
            c.drawString(30, 31, "This is a computer generated statement and does not require a signature.")
            c.drawRightString(W - 30, 31, "Customer care 1800 000 0000")

    def new_page():
        page_footer()
        c.showPage()
        state["page"] += 1
        y = page_header(False)
        return y

    def table_head(y):
        c.setFont(fb, fsize)
        for label, x, al in cols.heads:
            (c.drawRightString if al == "r" else c.drawString)(x, y, label)
        c.line(30, y - 3, W - 30, y - 3)
        return y - 14

    y = page_header(True)
    first_account = True
    for acct in accounts:
        if not first_account and new_page_per_account:
            y = new_page()
        first_account = False
        c.setFont(fb, 9)
        c.drawString(30, y, acct["name"]); y -= 12
        c.setFont(fn, 8)
        for line in acct.get("header", []):
            c.drawString(30, y, line); y -= 11
        if acct.get("opening_label"):
            c.drawString(30, y, f"{acct['opening_label']}: {amount_sym}{inr(acct['opening'])}"); y -= 11
        if summary:
            c.setFont(fb, 8); c.drawString(30, y, "STATEMENT SUMMARY"); y -= 11
            c.setFont(fn, 8)
            tdr = sum(r["debit"] for r in acct["rows"]); tcr = sum(r["credit"] for r in acct["rows"])
            ndr = sum(1 for r in acct["rows"] if r["debit"]); ncr = sum(1 for r in acct["rows"] if r["credit"])
            c.drawString(30, y, "Opening Balance"); c.drawString(150, y, "Dr Count"); c.drawString(230, y, "Cr Count")
            c.drawString(310, y, "Debits"); c.drawString(400, y, "Credits"); c.drawString(490, y, "Closing Bal"); y -= 10
            c.drawString(30, y, inr(acct["opening"])); c.drawString(150, y, str(ndr)); c.drawString(230, y, str(ncr))
            c.drawString(310, y, inr(tdr)); c.drawString(400, y, inr(tcr)); c.drawString(490, y, inr(acct["closing"])); y -= 16
        y -= 4
        y = table_head(y)
        n_on_page = 0
        for r in acct["rows"]:
            lines = [r["desc"]]
            if wrap:
                lines = textwrap.wrap(r["desc"], width=int(cols.narr_w / (fsize * 0.56)) , break_long_words=True) or [""]
            need = LH * len(lines) + 2
            if y - need < 56:
                y = new_page()
                y = table_head(y) if repeat_head else y
                if bf_rows and acct["rows"] and style != "card":
                    c.setFont(fn, fsize)
                    c.drawString(cols.narr, y, "BROUGHT FORWARD")
                    c.drawRightString(cols.bal, y, inr(r["balance"] - r["credit"] + r["debit"]))
                    y -= LH + 2
            c.setFont(fn, fsize)
            c.drawString(cols.date, y, _dmy(r["date"]))
            if variant == "valuedate":
                c.drawString(92 * W / 595.0, y, _dmy(r["date"]))
            c.drawString(cols.narr, y, lines[0])
            if style == "sep":
                if r.get("ref"):
                    c.drawString(cols.ref, y, r["ref"])
                if r["debit"]:
                    c.drawRightString(cols.deb, y, inr(r["debit"]))
                if r["credit"]:
                    c.drawRightString(cols.cre, y, inr(r["credit"]))
                c.drawRightString(cols.bal, y, inr(r["balance"]))
            elif style == "drcr":
                if r.get("ref"):
                    c.drawString(cols.ref, y, r["ref"])
                amt = r["debit"] or r["credit"]
                c.drawRightString(cols.amt, y, inr(amt) + (" Dr" if r["debit"] else " Cr"))
                c.drawRightString(cols.bal, y, inr(abs(r["balance"])) + (" Cr" if r["balance"] >= 0 else " Dr"))
            elif style == "signed":
                if r.get("ref"):
                    c.drawString(cols.ref, y, r["ref"])
                amt = ("-" + inr(r["debit"])) if r["debit"] else ("+" + inr(r["credit"]))
                c.drawRightString(cols.amt, y, amt)
                c.drawRightString(cols.bal, y, inr(r["balance"]))
            elif style == "card":
                amt = r["debit"] or r["credit"]
                c.drawRightString(cols.amt, y, inr(amt) + (" Cr" if r["credit"] else ""))
            for extra in lines[1:]:
                y -= LH
                c.drawString(cols.narr, y, extra)
            y -= LH + 2
        y -= 6
        if acct.get("closing_label"):
            c.setFont(fb, 8)
            c.drawString(30, y, f"{acct['closing_label']}: {amount_sym}{inr(acct['closing'])}"); y -= 11
        for line in acct.get("footer", []):
            c.setFont(fn, 8); c.drawString(30, y, line); y -= 11
        y -= 14
    page_footer()
    c.showPage()
    c.save()
    return buf.getvalue(), state["page"]


def build(path, accounts, **kw):
    """Two-pass so 'Page x of N' is right when footers are on."""
    if kw.get("footers"):
        _, n = draw_pdf(accounts, **kw)
        kw["total_pages"] = n
    data, n = draw_pdf(accounts, **kw)
    path.write_bytes(data)
    return n


def acct_std(name, acno, opening, closing, rows, period):
    return dict(name=name, rows=rows, opening=opening, closing=closing,
                header=[f"Account No: {acno}   IFSC: EXMP0001234   Branch: BENGALURU MG ROAD",
                        f"Statement Period: {period}",
                        "Customer: MR. ARJUN KRISHNAN   Currency: INR"],
                opening_label="Opening Balance", closing_label="Closing Balance")
