"""Documents that are NOT bank statements (A36-A38): salary slip, loan sanction letter, MF consolidated account statement."""
import json
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from common import FIX, inr


def _c(path):
    c = canvas.Canvas(str(path), pagesize=A4)
    return c, A4[0], A4[1]


def salary_slip(path):
    c, W, H = _c(path)
    c.setFont("Helvetica-Bold", 13); c.drawString(40, H - 50, "ACME TECHNOLOGIES PRIVATE LIMITED")
    c.setFont("Helvetica", 9); c.drawString(40, H - 64, "Payslip for the month of January 2026")
    info = [("Employee Name", "ARJUN KRISHNAN"), ("Employee ID", "ACME-10492"), ("Designation", "Software Engineer"),
            ("PAN", "ABCDE1234F"), ("UAN", "100123456789"), ("Bank A/c", "XXXXXX6789"), ("Days Paid", "31")]
    y = H - 95
    for k, v in info:
        c.drawString(40, y, k); c.drawString(160, y, ": " + v); y -= 13
    y -= 10
    c.setFont("Helvetica-Bold", 9); c.drawString(40, y, "EARNINGS"); c.drawRightString(270, y, "AMOUNT (Rs.)")
    c.drawString(310, y, "DEDUCTIONS"); c.drawRightString(555, y, "AMOUNT (Rs.)"); y -= 6; c.line(40, y, 555, y); y -= 13
    c.setFont("Helvetica", 9)
    earn = [("Basic Salary", 6000000), ("House Rent Allowance", 3000000), ("Special Allowance", 3000000), ("Conveyance", 150000), ("LTA", 350000)]
    ded = [("Provident Fund", 720000), ("Professional Tax", 20000), ("Income Tax (TDS)", 1116800), ("Health Insurance", 100000)]
    ge, gd = sum(a for _, a in earn), sum(a for _, a in ded)
    for i in range(max(len(earn), len(ded))):
        if i < len(earn):
            c.drawString(40, y, earn[i][0]); c.drawRightString(270, y, inr(earn[i][1]))
        if i < len(ded):
            c.drawString(310, y, ded[i][0]); c.drawRightString(555, y, inr(ded[i][1]))
        y -= 13
    c.line(40, y + 6, 555, y + 6); y -= 6
    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, y, "Gross Earnings"); c.drawRightString(270, y, inr(ge))
    c.drawString(310, y, "Total Deductions"); c.drawRightString(555, y, inr(gd)); y -= 20
    net = ge - gd
    c.setFont("Helvetica-Bold", 11); c.drawString(40, y, "NET PAY (Rs.)"); c.drawRightString(270, y, inr(net)); y -= 14
    c.setFont("Helvetica", 8); c.drawString(40, y, "Rupees One Lakh Five Thousand Four Hundred Thirty Two Only")
    c.drawString(40, 60, "This is a system generated payslip and does not require signature.")
    c.showPage(); c.save()
    return dict(employer="ACME TECHNOLOGIES PRIVATE LIMITED", month="2026-01", gross=ge, deductions=gd, net=net)


def loan_letter(path):
    c, W, H = _c(path)
    c.setFont("Helvetica-Bold", 13); c.drawString(40, H - 50, "EXAMPLE HOUSING FINANCE LIMITED")
    c.setFont("Helvetica", 9)
    lines = ["SANCTION LETTER", "Date: 12/01/2026", "Ref No: EHFL/HL/2026/004521", "",
             "To,", "Mr. Arjun Krishnan", "Flat 42, Sunrise Towers, Bengaluru 560001", "",
             "Dear Sir,", "Sub: Sanction of Home Loan", "",
             "We are pleased to inform you that your application for a home loan has been sanctioned on the following terms:", ""]
    y = H - 80
    for ln in lines:
        c.drawString(40, y, ln); y -= 12
    terms = [("Loan Amount Sanctioned", "Rs. 40,00,000.00"), ("Rate of Interest", "8.75% p.a. floating (linked to repo rate)"),
             ("Tenure", "240 months"), ("EMI", "Rs. 35,350.00"), ("Processing Fee", "Rs. 20,000.00 plus GST"),
             ("Loan Account No", "HL004521XXXX"), ("First EMI Date", "05/03/2026"), ("Security", "Residential property at Bengaluru")]
    for k, v in terms:
        c.drawString(60, y, k); c.drawString(240, y, ": " + v); y -= 13
    y -= 10
    c.drawString(40, y, "This sanction is valid for 90 days from the date of this letter. Disbursement is subject to legal and technical verification.")
    c.showPage(); c.save()
    return dict(lender="EXAMPLE HOUSING FINANCE LIMITED", amount=400000000, rate="8.75", tenure=240, emi=3535000)


def mf_cas(path):
    c, W, H = _c(path)
    c.setFont("Helvetica-Bold", 12); c.drawString(40, H - 45, "CONSOLIDATED ACCOUNT STATEMENT")
    c.setFont("Helvetica", 8); c.drawString(40, H - 58, "Statement for the period 01-Jan-2026 to 31-Jan-2026   PAN: ABCDE1234F   Email: a***@example.com")
    c.drawString(40, H - 70, "Summary of holdings as on 31-Jan-2026")
    y = H - 95
    hdr = ["AMC", "Folio No", "Scheme", "Units", "NAV (Rs.)", "Value (Rs.)"]
    xs = [40, 120, 190, 380, 450, 555]
    c.setFont("Helvetica-Bold", 8)
    for h, x in zip(hdr, xs):
        (c.drawRightString if h in ("Units", "NAV (Rs.)", "Value (Rs.)") else c.drawString)(x, y, h)
    c.line(40, y - 3, 555, y - 3); y -= 14
    c.setFont("Helvetica", 8)
    hold = [("Example MF", "1234567/89", "Nifty 50 Index Fund - Direct Growth", 1523.456, 2845.12), ("Example MF", "7654321/00", "Flexi Cap Fund - Direct Growth", 410.222, 6120.5),
            ("Sample AMC", "5550001/11", "Liquid Fund - Direct Growth", 12.3456, 4321.0987)]
    tot = 0
    for amc, folio, scheme, units, nav in hold:
        val = round(units * nav * 100)
        tot += val
        c.drawString(40, y, amc); c.drawString(120, y, folio); c.drawString(190, y, scheme)
        c.drawRightString(380, y, f"{units:,.3f}"); c.drawRightString(450, y, f"{nav:,.4f}"); c.drawRightString(555, y, inr(val)); y -= 12
    y -= 6; c.setFont("Helvetica-Bold", 8); c.drawString(40, y, "Total market value"); c.drawRightString(555, y, inr(tot)); y -= 20
    c.setFont("Helvetica", 8); c.drawString(40, y, "Opening Unit Balance and Closing Unit Balance are shown per folio; no transactions in this period.")
    c.showPage(); c.save()
    return dict(holdings=len(hold), total_value=tot)


def main():
    out = {}
    out["a36_salary_slip.pdf"] = salary_slip(FIX / "a36_salary_slip.pdf")
    out["a37_loan_sanction_letter.pdf"] = loan_letter(FIX / "a37_loan_sanction_letter.pdf")
    out["a38_mf_cas.pdf"] = mf_cas(FIX / "a38_mf_cas.pdf")
    for name, fields in out.items():
        Path(str(FIX / name) + ".truth.json").write_text(json.dumps({"accounts": [], "fields": fields}), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
