"""Generate every synthetic fixture into $MONTY_SPIKE/fixtures (never into the repo).

Run:  python make_fixtures.py [only-name ...]
Each fixture gets <file>.truth.json with the ground-truth rows (paise ints, ISO dates).
Jan = month 1, Feb = month 2 in the same layout (rerun test).
"""
import datetime as dt
import sys
from pathlib import Path

import gen_other as go
import gen_pdf as gp
from common import FIX, gen_month, inr, truth_rows, write_truth
from reportlab.lib.pagesizes import A4, landscape

OPEN_JAN = 24567890      # 2,45,678.90
FIX.mkdir(parents=True, exist_ok=True)
made = {}


def two_months(seed, n=22, **kw):
    r1, c1 = gen_month(seed, 2026, 1, OPEN_JAN, n, big=True, **kw)
    r2, c2 = gen_month(seed + 1, 2026, 2, c1, n, **kw)
    return (r1, OPEN_JAN, c1), (r2, c1, c2)


def truth_for(rows, opening, closing, with_balance=True, name="main"):
    return [dict(name=name, opening=opening, closing=closing, rows=truth_rows(rows, with_balance))]


def emit(name, writer, month_data, with_balance=True, ext=""):
    for tag, (rows, op, cl) in zip(("jan", "feb"), month_data):
        p = FIX / f"{name}_{tag}{ext}"
        writer(p, rows, op, cl)
        write_truth(p, truth_for(rows, op, cl, with_balance))
        made[p.name] = p.stat().st_size


def pdf_fixture(name, style="sep", months=None, with_balance=True, acct_fn=None, **kw):
    months = months or two_months(11)
    for tag, (rows, op, cl) in zip(("jan", "feb"), months):
        p = FIX / f"{name}_{tag}.pdf"
        rows = gp.fit_rows(rows, style, kw.get("page_size", A4), kw.get("fontname", "Helvetica"), 8, kw.get("wrap", False))
        per = "01-Jan-2026 to 31-Jan-2026" if tag == "jan" else "01-Feb-2026 to 28-Feb-2026"
        acct = gp.acct_std("SAVINGS ACCOUNT STATEMENT", "50100123456789", op, cl, rows, per)
        gp.build(p, [acct], style=style, **kw)
        write_truth(p, truth_for(rows, op, cl, with_balance))
        made[p.name] = p.stat().st_size


def main(only=None):
    want = lambda n: not only or any(n.startswith(o) or o in n for o in only)
    mm = two_months(11)
    # ---- A1-A9 PDFs
    if want("a1"): pdf_fixture("a1_sep_cols", "sep", mm)
    if want("a2"):
        wr = lambda rng, d, i: d + (" /PAID TO SHOP NEAR CITY CENTRE MALL BENGALURU" if i % 3 == 0 else "")
        pdf_fixture("a2_wrapped", "sep", two_months(12, narr_override=wr), wrap=True)
    if want("a3"):
        pdf_fixture("a3_drcr", "drcr", two_months(13))
        pdf_fixture("a3b_signed", "signed", two_months(14))
    if want("a4"):
        pdf_fixture("a4_multipage", "sep", two_months(15, n=120), footers=True, summary=True, bf_rows=True, per_page=40)
    if want("a5"):
        for tag, (rows, op, cl) in zip(("jan", "feb"), mm):
            rows = gp.fit_rows(rows, "sep")
            p = FIX / f"a5_password_{tag}.pdf"
            gp.build(p, [gp.acct_std("SAVINGS ACCOUNT STATEMENT", "50100123456789", op, cl, rows, "Jan 2026")],
                     style="sep", encrypt="ARJU0101")
            write_truth(p, truth_for(rows, op, cl)); made[p.name] = p.stat().st_size
    if want("a6"):
        pdf_fixture("a6a_landscape", "sep", mm, page_size=landscape(A4))
        # rotated: portrait mediabox, /Rotate 90 -> sideways when displayed
        pdf_fixture("a6b_rotate90", "sep", mm, rotate=90, canvas_pagesize=landscape(A4))
        pdf_fixture("a6c_landscape_rotated_upright", "sep", mm, page_size=landscape(A4), upright_rot=True)
    if want("a7"):
        card = []
        for tag, seed, pd_prev in (("jan", 21, 1234500), ("feb", 22, 4531020)):
            from common import DEBIT_NARR
            import random
            rng = random.Random(seed)
            rows, due = [], pd_prev
            for i in range(18):
                d = dt.date(2026, 1 if tag == "jan" else 2, rng.randint(1, 27))
                if i in (3, 11):
                    amt, deb, cre = rng.randint(500000, 3000000), 0, 0
                    cre = amt; deb = 0; desc = "PAYMENT RECEIVED - THANK YOU"
                elif i == 7:
                    cre = 129900; deb = 0; desc = "REFUND AMAZON PAY INDIA BANGALORE"
                else:
                    deb = rng.randint(19900, 1500000); cre = 0
                    desc = rng.choice(["SWIGGY BANGALORE IN", "AMAZON PAY INDIA MUMBAI", "UBER INDIA TECHNOLOGY",
                                       "BOOKMYSHOW MUMBAI", "RELIANCE RETAIL LTD", "NETFLIX.COM MUMBAI", "INDIGO AIRLINES GURGAON"])
                rows.append(dict(date=d, desc=desc, ref="", debit=deb, credit=cre, balance=None))
            rows.sort(key=lambda r: r["date"])
            fin = 2500 + 18000  # finance charge + GST as debit rows
            rows.append(dict(date=rows[-1]["date"], desc="FINANCE CHARGES", ref="", debit=fin, credit=0, balance=None))
            total = pd_prev + sum(r["debit"] for r in rows) - sum(r["credit"] for r in rows)
            acct = dict(name="CREDIT CARD STATEMENT", rows=rows, opening=-pd_prev, closing=-total,
                        header=["Card No: 4111 XXXX XXXX 4321   Statement Date: 28/%s/2026" % ("01" if tag == "jan" else "02"),
                                f"Payment Due Date: 15/{'02' if tag == 'jan' else '03'}/2026   Credit Limit: Rs. 3,00,000.00",
                                f"Previous Balance: Rs. {inr(pd_prev)}",
                                f"Total Amount Due: Rs. {inr(total)}   Minimum Amount Due: Rs. {inr(max(total // 20, 50000))}"])
            p = FIX / f"a7_card_{tag}.pdf"
            gp.build(p, [acct], style="card")
            write_truth(p, [dict(name="card", opening=-pd_prev, closing=-total, rows=truth_rows(rows, False))])
            made[p.name] = p.stat().st_size
    if want("a8"):
        for tag, ((r1, o1, c1), (r2, o2, c2)) in zip(("jan",), [(two_months(31)[0], two_months(41)[0])]):
            r1, r2 = gp.fit_rows(r1, "sep"), gp.fit_rows(r2, "sep")
            p = FIX / f"a8_two_accounts_{tag}.pdf"
            a = gp.acct_std("SAVINGS ACCOUNT 50100123456789", "50100123456789", o1, c1, r1, "Jan 2026")
            b = gp.acct_std("CURRENT ACCOUNT 50200987654321", "50200987654321", o2, c2, r2, "Jan 2026")
            gp.build(p, [a, b], style="sep")
            write_truth(p, truth_for(r1, o1, c1, name="savings") + truth_for(r2, o2, c2, name="current"))
            made[p.name] = p.stat().st_size
        (r1, o1, c1), (r2, o2, c2) = two_months(31)[1], two_months(41)[1]
        r1, r2 = gp.fit_rows(r1, "sep"), gp.fit_rows(r2, "sep")
        p = FIX / "a8_two_accounts_feb.pdf"
        gp.build(p, [gp.acct_std("SAVINGS ACCOUNT 50100123456789", "50100123456789", o1, c1, r1, "Feb 2026"),
                     gp.acct_std("CURRENT ACCOUNT 50200987654321", "50200987654321", o2, c2, r2, "Feb 2026")], style="sep")
        write_truth(p, truth_for(r1, o1, c1, name="savings") + truth_for(r2, o2, c2, name="current"))
        made[p.name] = p.stat().st_size
    if want("a9"):
        dv = lambda rng, d, i: (d.replace("SWIGGY", "स्विगी").replace("Rent share", "किराया — ₹2,500 हिस्सा")
                                if i % 2 == 0 else d + " Refund ₹500.00")
        pdf_fixture("a9_devanagari", "sep", two_months(16, narr_override=dv), fontname="Nirmala")
    # ---- spreadsheets
    if want("a13"):
        emit("a13_xlsx", go.write_xlsx, mm, ext=".xlsx")
    if want("a14"):
        emit("a14_xls_biff", go.write_xls, mm, ext=".xls")
    if want("a15"):
        emit("a15_html_as_xls", go.write_html_xls, mm, ext=".xls")
    if want("a16"):
        emit("a16_tsv_as_xls", go.write_tsv_xls, mm, ext=".xls")
    if want("a17"):
        emit("a17_ods", go.write_ods, mm, ext=".ods")
    if want("a19"):
        for tag, (rows, op, cl) in zip(("jan", "feb"), mm):
            plain = FIX / f"_tmp_{tag}.xlsx"
            go.write_xlsx(plain, rows, op, cl)
            p = FIX / f"a19_xlsx_password_{tag}.xlsx"
            go.encrypt_xlsx(plain, p, "ARJU0101")
            plain.unlink()
            write_truth(p, truth_for(rows, op, cl)); made[p.name] = p.stat().st_size
    # ---- text
    if want("a20"): emit("a20_csv_bom", go.write_csv_bom, mm, ext=".csv")
    if want("a21"):
        mm21 = []
        for rows, op, cl in mm:    # non-ASCII narrations that cp1252 can hold (e-acute, euro sign)
            mm21.append(([dict(r, desc=("POS CAFÉ COFFEE DAY €0 TEST" if i % 5 == 0 else r["desc"])) for i, r in enumerate(rows)], op, cl))
        emit("a21_csv_cp1252", go.write_csv_cp1252, mm21, ext=".csv")
    if want("a22"): emit("a22_tsv", go.write_tsv, mm, ext=".tsv")
    if want("a23"):
        mm23 = [([dict(r, desc=r["desc"][:40].rstrip()) for r in rows], op, cl) for rows, op, cl in mm]
        emit("a23_fixed", go.write_fixed, mm23, ext=".txt")
    # ---- other
    one = mm[0]
    for name, w, ext in (("a24_docx_table", go.write_docx_table, ".docx"), ("a25_docx_paras", go.write_docx_paras, ".docx"),
                         ("a26_html", go.write_html, ".html"), ("a27_ofx", go.write_ofx, ".ofx"),
                         ("a28_mt940", go.write_mt940, ".sta"), ("a29_camt053", go.write_camt, ".xml"),
                         ("a30_json_aa", go.write_json_aa, ".json"), ("a31_rtf", go.write_rtf, ".rtf")):
        if want(name[:3]):
            p = FIX / f"{name}{ext}"
            w(p, one[0], one[1], one[2])
            write_truth(p, truth_for(one[0], one[1], one[2], with_balance=name[:3] not in ("a27", "a28", "a29")))
            made[p.name] = p.stat().st_size
    if want("a27"):
        p = FIX / "a27_qfx_xml.qfx"
        go.write_ofx(p, one[0], one[1], one[2], v2=True); write_truth(p, truth_for(*one, with_balance=False)); made[p.name] = p.stat().st_size
    if want("a32"):
        go.write_zip(FIX / "a32_zip_of_pdfs.zip", [FIX / "a1_sep_cols_jan.pdf", FIX / "a1_sep_cols_feb.pdf"])
        import json as _j
        ta = [_j.loads((FIX / f"a1_sep_cols_{t}.pdf.truth.json").read_text(encoding="utf-8"))["accounts"][0] for t in ("jan", "feb")]
        write_truth(FIX / "a32_zip_of_pdfs.zip", ta)
    if want("a33"):
        src = (FIX / "a1_sep_cols_jan.pdf").read_bytes()
        (FIX / "a33_truncated.pdf").write_bytes(src[: int(len(src) * 0.6)])
        bad = bytearray(src); bad[len(bad) // 2: len(bad) // 2 + 300] = b"\x00" * 300
        (FIX / "a33_corrupt.pdf").write_bytes(bytes(bad))
        (FIX / "a33_empty.pdf").write_bytes(b"")
        (FIX / "a33_xlsx_named_pdf.pdf").write_bytes((FIX / "a13_xlsx_jan.xlsx").read_bytes())
        (FIX / "a33_pdf_named_xlsx.xlsx").write_bytes(src)
        (FIX / "a33_junk.xls").write_bytes(bytes(range(256)) * 20)
    # ---- size
    if want("a34"):
        rows_all, bal, d0 = [], OPEN_JAN, dt.date(2026, 1, 1)
        opening = OPEN_JAN
        for m in range(1, 13):
            r, bal = gen_month(1000 + m, 2026, m, bal, 833)
            rows_all += gp.fit_rows(r, "sep")
        p = FIX / "a34_big_200p.pdf"
        n = gp.build(p, [gp.acct_std("SAVINGS ACCOUNT STATEMENT", "50100123456789", opening, bal, rows_all, "FY 2026")],
                     style="sep", footers=True)
        write_truth(p, truth_for(rows_all, opening, bal)); made[p.name] = p.stat().st_size
        made["a34 pages"] = n
        p = FIX / "a34_big_10k.csv"
        go.write_csv_bom(p, rows_all, opening, bal); write_truth(p, truth_for(rows_all, opening, bal))
    if want("a35"):
        rows_all, bal = [], OPEN_JAN
        for m in range(1, 13):
            r, bal = gen_month(2000 + m, 2026, m, bal, 40)
            rows_all += gp.fit_rows(r, "sep")
        p = FIX / "a35_12month.pdf"
        n = gp.build(p, [gp.acct_std("SAVINGS ACCOUNT STATEMENT", "50100123456789", OPEN_JAN, bal, rows_all, "01-Jan-2026 to 31-Dec-2026")],
                     style="sep", footers=True, summary=True)
        write_truth(p, truth_for(rows_all, OPEN_JAN, bal)); made[p.name] = p.stat().st_size; made["a35 pages"] = n
        p = FIX / "a35_12month.xlsx"
        go.write_xlsx(p, rows_all, OPEN_JAN, bal, period="01-Jan-2026 to 31-Dec-2026"); write_truth(p, truth_for(rows_all, OPEN_JAN, bal))
    for k, v in made.items():
        print(f"{k:45s} {v}")


if __name__ == "__main__":
    main(sys.argv[1:] or None)
