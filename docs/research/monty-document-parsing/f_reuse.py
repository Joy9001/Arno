"""Part F: reuse. (1) rerun is covered by run_matrix.py (every Jan/Feb pair). (2) layout change: does the check fail or do
wrong rows pass silently? (3) day/month swap vs the balance check."""
import sys as _s, pathlib as _p
_s.path.insert(0, str(_p.Path(__file__).resolve().parent))   # lets `python -I` (isolated mode) still import sibling modules
import datetime as dt
import json
import random

import gen_pdf as gp
from common import FIX, PARSERS, gen_month, load_truth, norm, score, truth_rows, write_truth
from hostfns import Doc, check_statement
from runner import evaluate, run_text
from pathlib import Path

HARD = str(PARSERS / "pdf_sep_cols_hardcoded.py")
HEAD = str(PARSERS / "pdf_sep_cols.py")


def build_variant(name, rows, op, cl, **kw):
    rows = gp.fit_rows(rows, "sep", gp.A4 if hasattr(gp, "A4") else None) if False else rows
    p = FIX / f"f_{name}.pdf"
    acct = gp.acct_std("SAVINGS ACCOUNT STATEMENT", "50100123456789", op, cl, rows, "01-Jan-2026 to 31-Jan-2026")
    gp.build(p, [acct], style="sep", **kw)
    write_truth(p, [dict(name="main", opening=op, closing=cl, rows=truth_rows(rows))])
    return p


def describe(r):
    c = (r.get("checks") or [{}])[0]
    return dict(status=r.get("status"), rows_right=f"{r.get('rows_right')}/{r.get('rows_total')}", chain=c.get("chain_ok"), total_ok=c.get("total_ok"), err=r.get("error"))


def layout_changes():
    rows, cl = gen_month(11, 2026, 1, 24567890, 22, big=True)
    rows = gp.fit_rows(rows, "sep")
    print("--- F2. layout changes, same saved script reused")
    base = build_variant("base", rows, 24567890, cl)
    for label, kw in (("columns Credit/Debit swapped", dict(variant="swap")), ("new 'Value Date' column inserted", dict(variant="valuedate"))):
        rows_v = rows
        p = build_variant(label.split()[0].lower() + "_" + kw["variant"], rows_v, 24567890, cl, **kw)
        for nm, script in (("header-driven script (pdf_sep_cols.py)", HEAD), ("hard-coded x-bounds script", HARD)):
            r = evaluate(str(p), script, quiet=True, desc_mode="exact")
            print(f"  {label:36s} | {nm:40s} |", json.dumps(describe(r), default=str))
    # statement without running balance (card): swapped meaning goes unseen? (not testable here: card has no chain)


def day_month_swap():
    print("--- F3. day/month swap vs the balance check")
    rng = random.Random(5)
    rows, cl = gen_month(21, 2026, 1, 24567890, 18)
    # compress all dates into 1..12 January so every date is ambiguous (valid both ways)
    rows = [dict(r, date=dt.date(2026, 1, 1 + (r["date"].day - 1) * 11 // 30)) for r in rows]
    rows = gp.fit_rows(rows, "sep")
    p = build_variant("mmdd", rows, 24567890, cl, date_fmt="%m/%d/%Y")     # document prints MM/DD/YYYY
    r = evaluate(str(p), HEAD, quiet=True, desc_mode="none")
    out, err, _, _ = run_text(Doc(p), Path(HEAD).read_text(encoding="utf-8"))
    parsed = out["accounts"][0]["rows"]
    truth = load_truth(p)["accounts"][0]["rows"]
    wrong = sum(1 for g, t in zip(parsed, truth) if g["date"] != t["date"])
    print("  all days <= 12, document is MM/DD/YYYY, script reads DD/MM/YYYY:")
    print("   balance check status:", r["status"], "| rows with the wrong date:", f"{wrong}/{len(truth)}", "| dates_monotonic:", r["checks"][0]["dates_monotonic"])
    pc = check_statement(parsed, out["accounts"][0]["opening"], out["accounts"][0]["closing"], period=("2026-01-01", "2026-01-31"))
    print("   same rows with the statement-period containment check:", pc["status"], "| dates_in_period:", pc.get("dates_in_period"), "|", pc.get("reason"))
    # realistic month: days up to 31
    rows2, cl2 = gen_month(22, 2026, 1, 24567890, 22)
    rows2 = gp.fit_rows(rows2, "sep")
    p2 = build_variant("mmdd_full", rows2, 24567890, cl2, date_fmt="%m/%d/%Y")
    r2 = evaluate(str(p2), HEAD, quiet=True, desc_mode="none")
    print("  full month (days up to 31), same mismatch:", r2["status"], "rows parsed:", r2["checks"][0]["rows"] if r2.get("checks") else None, "of", len(rows2), "(rows with day>12 are silently dropped by this script, balance check catches it)")


if __name__ == "__main__":
    layout_changes()
    day_month_swap()
