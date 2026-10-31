"""Shared bits: paths, synthetic transaction generator, INR formatting, truth comparison.

Everything here is synthetic. Money is integer paise everywhere.
"""
import datetime as dt
import json
import os
import random
from pathlib import Path

SPIKE = Path(os.environ.get(
    "MONTY_SPIKE",
    r"C:\Users\joymr\AppData\Local\Temp\claude\J--Development-Fin-Agent"
    r"\1f23987d-01f6-476a-80de-d4d1e07948e6\scratchpad\monty-spike"))
FIX = SPIKE / "fixtures"
DOWNLOADS = SPIKE / "downloads"
HERE = Path(__file__).resolve().parent
PARSERS = HERE / "parsers"


def inr(p: int, sep=True) -> str:
    """paise -> '1,00,000.00' (Indian lakh grouping)."""
    neg = p < 0
    p = abs(p)
    rup, ps = divmod(p, 100)
    s = str(rup)
    if sep and len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if neg else "") + f"{s}.{ps:02d}"


DEBIT_NARR = [
    ("UPI/DR/{r12}/SWIGGY/swiggy@icici/Order", 12000, 85000),
    ("UPI/DR/{r12}/ZOMATO/zomato@hdfcbank/Payment", 15000, 90000),
    ("UPI/DR/{r12}/BIGBASKET/bigbasket@axisbank/Groceries", 30000, 250000),
    ("UPI/DR/{r12}/Ramesh Kumar/ramesh.k@okaxis/Rent share", 500000, 1500000),
    ("UPI/DR/{r12}/IRCTC/irctc@sbi/Ticket", 40000, 400000),
    ("ATM WDL/{r6}/MG ROAD BENGALURU", 100000, 1000000),
    ("POS {r4}XXXXXX{r4} AMAZON PAY INDIA", 49900, 1999900),
    ("NEFT-{ifsc}-HOUSING SOCIETY MAINT-{r6}", 250000, 600000),
    ("ACH D- LIC OF INDIA-{r6}", 450000, 450000),
    ("IMPS/P2A/{r12}/MOM/HDFC0001234", 500000, 2000000),
    ("UPI/DR/{r12}/Airtel Prepaid/airtel@icici/Recharge", 29900, 79900),
    ("EMI LOAN A/C {r6} HOME LOAN", 3850000, 3850000),
]
CREDIT_NARR = [
    ("NEFT-{ifsc}-ACME TECHNOLOGIES PVT LTD-SALARY", 10543200, 10543200),
    ("UPI/CR/{r12}/Priya Sharma/priya.s@okicici/Split dinner", 20000, 300000),
    ("INT.PD:{r6}", 15000, 90000),
    ("IMPS/P2A/{r12}/FRIEND/SBIN0001234/Trip share", 100000, 800000),
    ("UPI/CR/{r12}/AMAZON REFUND/amazon@apl/Refund", 49900, 499900),
]


def _fmt(rng, tpl):
    return tpl.format(r12="".join(rng.choice("0123456789") for _ in range(12)),
                      r6="".join(rng.choice("0123456789") for _ in range(6)),
                      r4="".join(rng.choice("0123456789") for _ in range(4)),
                      ifsc=rng.choice(["HDFC0001234", "ICIC0004321", "UTIB0000789", "SBIN0001122"]))


def gen_month(seed: int, year: int, month: int, opening: int, n: int = 22,
              narr_override=None, big=False):
    """Return (rows, closing). rows: dicts date(date), desc, ref, debit, credit, balance."""
    rng = random.Random(seed)
    days = (dt.date(year + (month == 12), month % 12 + 1, 1) - dt.date(year, month, 1)).days
    dates = sorted(rng.randint(1, days) for _ in range(n))
    dates[0] = 1
    rows, bal = [], opening
    for i, d in enumerate(dates):
        date = dt.date(year, month, d)
        credit_turn = (i == 0) or (rng.random() < 0.22)
        if credit_turn:
            tpl, lo, hi = (CREDIT_NARR[0] if i == 0 else rng.choice(CREDIT_NARR[1:]))
            amt = rng.randint(lo, hi) if lo != hi else lo
            if i == 0 and big:
                amt = 123456789 if month == 1 else amt   # 12,34,567.89 once
            deb, cre = 0, amt
        else:
            tpl, lo, hi = rng.choice(DEBIT_NARR)
            amt = rng.randint(lo, hi) if lo != hi else lo
            if bal - amt < 10000:
                tpl, lo, hi = CREDIT_NARR[3]
                amt = rng.randint(lo, hi)
                deb, cre = 0, amt
            else:
                deb, cre = amt, 0
        bal += cre - deb
        desc = _fmt(rng, tpl)
        if narr_override:
            desc = narr_override(rng, desc, i)
        ref = "".join(rng.choice("0123456789") for _ in range(12)) if rng.random() < 0.5 else ""
        rows.append(dict(date=date, desc=desc, ref=ref, debit=deb, credit=cre, balance=bal))
    return rows, bal


def truth_rows(rows, with_balance=True):
    return [dict(date=r["date"].isoformat(), desc=" ".join(r["desc"].split()),
                 debit=r["debit"], credit=r["credit"],
                 balance=r["balance"] if with_balance else None) for r in rows]


def write_truth(path: Path, accounts):
    """accounts: list of {name, opening, closing, rows(truth)}"""
    path = Path(str(path) + ".truth.json")
    path.write_text(json.dumps({"accounts": accounts}, ensure_ascii=False, indent=0), encoding="utf-8")


def load_truth(doc_path):
    p = Path(str(doc_path) + ".truth.json")
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def norm(s):
    return " ".join(str(s).split())


def score(result_accounts, truth_accounts, desc_mode="exact"):
    """Row-level scoring. Returns (rows_right, rows_total, notes)."""
    import difflib
    right = total = 0
    notes = []
    if result_accounts is None:
        return 0, sum(len(a["rows"]) for a in truth_accounts), ["no result"]
    for ti, ta in enumerate(truth_accounts):
        total += len(ta["rows"])
        ra = result_accounts[ti] if ti < len(result_accounts) else {"rows": []}
        got = ra.get("rows", [])
        if len(got) != len(ta["rows"]):
            notes.append(f"acct{ti}: {len(got)} rows vs {len(ta['rows'])} expected")
        for g, t in zip(got, ta["rows"]):
            ok = (g.get("date") == t["date"] and int(g.get("debit") or 0) == t["debit"]
                  and int(g.get("credit") or 0) == t["credit"])
            if t.get("balance") is not None:
                ok = ok and g.get("balance") == t["balance"]
            if ok:
                gd, td = norm(g.get("desc", "")), norm(t["desc"])
                if desc_mode == "exact":
                    ok = gd == td
                elif desc_mode == "nospace":
                    ok = "".join(gd.split()) == "".join(td.split())
                elif desc_mode == "fuzzy":
                    ok = difflib.SequenceMatcher(None, gd, td).ratio() >= 0.8
            right += bool(ok)
    return right, total, notes
