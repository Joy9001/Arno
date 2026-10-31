"""Run the Monty-parser pipeline on YOUR OWN statements and print only PASS / FAIL plus counts.

    python check_real.py FILE_OR_FOLDER [FILE_OR_FOLDER ...] [--password PW | --ask-password] [--ocr]
                         [--ledger-opening PAISE] [--period YYYY-MM-DD YYYY-MM-DD] [--parser SCRIPT.py] [--show-names]

What it prints per file: an index, the detected kind, PASS or FAIL, the check status word, and counts (accounts, rows,
debit rows, credit rows, rows carrying a running balance, balance-chain breaks, pages without a text layer).
What it never prints: file names (unless --show-names), narrations, dates, amounts, balances, account numbers, passwords,
or the text of any error (only the exception class name). Nothing is written to disk, nothing leaves the machine.

PASS means ONLY that the amounts balance: opening + credits - debits == closing, and every printed running balance
follows from the row before it. It does NOT mean the dates or narrations are right: a day/month swap or an extra
column can still PASS. Pass --period with the statement's own period (read it yourself from the header, not from the
parser) and dates outside it turn PASS into FAIL. If the statement prints no opening balance, pass --ledger-opening
(integer paise, e.g. 24567890 for 2,45,678.90) or the file cannot reach VERIFIED.

The bundled parsers key on the exact header words of my synthetic layouts (e.g. "Narration", "Chq/Ref", "Withdrawal").
On a real statement a FAIL with NO_ACCOUNTS or SCRIPT_ERROR usually means no bundled parser fits that layout, not that
the file is bad: write a Monty script for it (see parsers/ and inspect_doc.py) and run it with --parser.
"""
import sys as _s, pathlib as _p
_s.path.insert(0, str(_p.Path(__file__).resolve().parent))   # lets `python -I` (isolated mode) still import sibling modules
import argparse
import getpass
import os
import sys
import time
from pathlib import Path

from hostfns import Doc, DocError, check_statement
from runner import run_text

PARSERS = Path(__file__).resolve().parent / "parsers"
CANDIDATES = {
    "pdf": ["pdf_sep_cols.py", "pdf_wrapped.py", "pdf_drcr.py", "pdf_card.py"],
    "image": ["pdf_sep_cols_ocr.py"],
    "sheet": ["sheet_header.py"],
    "text": ["sheet_header.py", "text_fixed.py"],
    "ofx": ["ofx.py"], "mt940": ["mt940.py"], "camt": ["camt053.py"], "json": ["json_aa.py"], "rtf": ["rtf_table.py"],
}
KIND_GROUP = {"xlsx": "sheet", "xls": "sheet", "xlsb": "sheet", "ods": "sheet", "docx": "sheet", "html": "sheet"}
RANK = {"VERIFIED": 3, "DISCREPANCY": 2, "FAILED": 1}


def counts(accts, checks):
    rows = sum(len(a.get("rows", [])) for a in accts)
    deb = sum(1 for a in accts for r in a.get("rows", []) if r.get("debit"))
    cre = sum(1 for a in accts for r in a.get("rows", []) if r.get("credit"))
    bal = sum(1 for a in accts for r in a.get("rows", []) if r.get("balance") is not None)
    brk = sum(len(c.get("chain_breaks") or []) for c in checks)
    return dict(accounts=len(accts), rows=rows, debit_rows=deb, credit_rows=cre, rows_with_balance=bal, chain_breaks=brk)


def try_parsers(doc, names, ledger_opening, period=None):
    best = None
    for n in names:
        out, err, secs, _ = run_text(doc, (PARSERS / n).read_text(encoding="utf-8") if (PARSERS / n).exists() else Path(n).read_text(encoding="utf-8"),
                                     ledger_opening=ledger_opening)
        if err:
            res = dict(parser=n, status="SCRIPT_ERROR", err=err.split(":")[0], secs=secs)
        elif not isinstance(out, dict) or not out.get("accounts"):
            res = dict(parser=n, status="NO_ACCOUNTS", secs=secs)
        else:
            accts = out["accounts"]
            for a in accts:
                if a.get("opening") is None and ledger_opening is not None:
                    a["opening"] = ledger_opening
                if a.get("closing") is None and a.get("rows") and a["rows"][-1].get("balance") is not None:
                    a["closing"] = a["rows"][-1]["balance"]
            checks = [check_statement(a.get("rows", []), a.get("opening"), a.get("closing"), period=period) for a in accts]
            st = "VERIFIED" if all(c["status"] == "VERIFIED" for c in checks) else min((c["status"] for c in checks), key=lambda s: RANK.get(s, 0))
            res = dict(parser=n, status=st, secs=secs, **counts(accts, checks))
            if st != "VERIFIED" and any(c.get("reason") == "opening/closing not int" for c in checks):
                res["hint"] = "opening or closing balance not found in the file (try --ledger-opening)"
            if st != "VERIFIED" and any(c.get("reason") == "dates outside the statement period" for c in checks):
                res["hint"] = "dates fall outside --period (day/month order or wrong period?)"
        if best is None or (RANK.get(res["status"], 0), res.get("rows", 0)) > (RANK.get(best["status"], 0), best.get("rows", 0)):
            best = res
        if res["status"] == "VERIFIED":
            break
    return best


def expand(paths):
    out = []
    for p in paths:
        p = Path(p)
        out += sorted(x for x in p.iterdir() if x.is_file()) if p.is_dir() else [p]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--password"); ap.add_argument("--ask-password", action="store_true")
    ap.add_argument("--ocr", action="store_true", help="OCR image-only pages with RapidOCR (needs the rapidocr package)")
    ap.add_argument("--ledger-opening", type=int, help="opening balance in paise when the statement does not print one")
    ap.add_argument("--period", nargs=2, metavar=("START", "END"), help="statement period, ISO dates; rows outside it fail the check")
    ap.add_argument("--parser", help="your own Monty script instead of the bundled ones")
    ap.add_argument("--show-names", action="store_true", help="print file names (off by default)")
    a = ap.parse_args()
    pw = a.password or os.environ.get("STATEMENT_PASSWORD") or (getpass.getpass("PDF/Office password: ") if a.ask_password else None)
    ocr_fn = None
    if a.ocr:
        from ocr_host import ocr_words
        ocr_fn = ocr_words
    files = expand(a.paths)
    n_pass = 0
    for i, f in enumerate(files, 1):
        label = f"file {i}" + (f" ({f.name})" if a.show_names else "")
        try:
            doc = Doc(f, password=pw, ocr=ocr_fn)
        except DocError as e:
            print(f"{label} [?] FAIL host:{e.code}")
            continue
        except Exception as e:
            print(f"{label} [?] FAIL host:{type(e).__name__}")
            continue
        info = doc.info()
        notext = ""
        if doc.kind == "pdf" and not ocr_fn:
            notext = f" pages_without_text_layer={info.get('pages', 0) - info.get('text_layer_pages', 0)}/{info.get('pages', 0)}"
        names = [a.parser] if a.parser else CANDIDATES.get(KIND_GROUP.get(doc.kind, doc.kind), [])
        if doc.kind == "zip":
            print(f"{label} [zip] FAIL unpack the archive first (members={len(doc.members)})")
            continue
        if doc.kind == "pdf" and not names:
            names = CANDIDATES["pdf"]
        if not names:
            print(f"{label} [{doc.kind}] FAIL no bundled parser for this kind")
            continue
        r = try_parsers(doc, names, a.ledger_opening, tuple(a.period) if a.period else None)
        ok = r["status"] == "VERIFIED"
        n_pass += ok
        extra = " ".join(f"{k}={r[k]}" for k in ("accounts", "rows", "debit_rows", "credit_rows", "rows_with_balance", "chain_breaks") if k in r)
        if not ok and r["status"] in ("NO_ACCOUNTS", "SCRIPT_ERROR") and not r.get("hint"):
            r["hint"] = "no bundled parser fits this layout; write one and pass --parser"
        print(f"{label} [{doc.kind}] {'PASS(amounts balance)' if ok else 'FAIL'} status={r['status']} parser={r['parser']} {extra}{notext}"
              + (f" err={r['err']}" if r.get("err") else "") + (f" hint={r['hint']}" if r.get("hint") else "") + f" secs={r['secs']:.2f}")
    print(f"summary: {n_pass} of {len(files)} PASS")
    sys.exit(0 if n_pass == len(files) and files else 1)


if __name__ == "__main__":
    main()
