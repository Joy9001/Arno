"""A32 (zip), A33 (corrupt/truncated/empty/wrong extension), A18 (xlsb) host-side checks. Prints one line per case."""
import sys as _s, pathlib as _p
_s.path.insert(0, str(_p.Path(__file__).resolve().parent))   # lets `python -I` (isolated mode) still import sibling modules
import io
import json
import time
import zipfile
from pathlib import Path

from common import DOWNLOADS, FIX, PARSERS, SPIKE, load_truth, score
from hostfns import Doc, DocError
from runner import evaluate

PDF_PARSER = str(PARSERS / "pdf_sep_cols.py")


def line(case, **kw):
    print(f"{case:34s}", json.dumps(kw, ensure_ascii=False, default=str)[:420])


def zip_case():
    z = FIX / "a32_zip_of_pdfs.zip"
    t0 = time.perf_counter()
    d = Doc(z)
    names = [n for n, _ in d.members]
    right = total = 0
    stat = []
    work = SPIKE / "zip_extract"
    work.mkdir(exist_ok=True)
    truth = load_truth(z)["accounts"]
    for i, (n, data) in enumerate(d.members):
        p = work / n
        p.write_bytes(data)
        r = evaluate(str(p), PDF_PARSER, quiet=True, desc_mode="none")
        stat.append(r.get("status"))
        # score against the matching truth account
        from runner import run_text
        out, err, _, _ = run_text(Doc(p), Path(PDF_PARSER).read_text(encoding="utf-8"))
        rr, tt, _ = score(out["accounts"], [truth[i]], "exact")
        right += rr; total += tt
    line("A32 zip of 2 PDFs", members=names, statuses=stat, rows=f"{right}/{total}", secs=round(time.perf_counter() - t0, 2))
    # guards
    bomb = SPIKE / "zip_bomb_test.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zz:
        zz.writestr("zeros.pdf", b"\0" * (300 * 1024 * 1024))
    try:
        t0 = time.perf_counter()
        Doc(bomb)
        line("A32 zip bomb 300MB", result="OPENED (guard missing)")
    except DocError as e:
        line("A32 zip bomb 300MB", result=f"refused: {e.code}", compressed_kb=bomb.stat().st_size // 1024, secs=round(time.perf_counter() - t0, 3))
    bomb.unlink()
    trav = SPIKE / "zip_traversal_test.zip"
    with zipfile.ZipFile(trav, "w") as zz:
        zz.writestr("../evil.pdf", b"x")
    try:
        Doc(trav); line("A32 path traversal member", result="OPENED")
    except DocError as e:
        line("A32 path traversal member", result=f"refused: {e.code}")
    trav.unlink()


def broken_cases():
    cases = [("truncated PDF (60%)", "a33_truncated.pdf"), ("corrupt PDF (300 NUL bytes mid-file)", "a33_corrupt.pdf"),
             ("empty file (0 bytes) .pdf", "a33_empty.pdf"), ("xlsx named .pdf", "a33_xlsx_named_pdf.pdf"),
             ("pdf named .xlsx", "a33_pdf_named_xlsx.xlsx"), ("random bytes named .xls", "a33_junk.xls")]
    for label, f in cases:
        try:
            d = Doc(FIX / f)
        except DocError as e:
            line("A33 " + label, outcome="refused by host", code=e.code, detail=str(e)[:80])
            continue
        except Exception as e:
            line("A33 " + label, outcome="UNHANDLED HOST EXCEPTION", exc=type(e).__name__, detail=str(e)[:80])
            continue
        parser = PDF_PARSER if d.kind == "pdf" else str(PARSERS / "sheet_header.py")
        r = evaluate(str(FIX / f), parser, quiet=True, desc_mode="none")
        line("A33 " + label, outcome="opened", kind=d.kind, pipeline_status=r.get("status"), error=r.get("error"), rows=(r.get("checks") or [{}])[0].get("rows"))


def xlsb_case():
    d = DOWNLOADS / "calamine_xlsb" / "any_sheets.xlsb"
    t0 = time.perf_counter()
    doc = Doc(d)
    line("A18 xlsb (public calamine sample)", kind=doc.kind, sheets=[(n, len(r)) for n, r in doc._sheets][:6], first_row=doc._sheets[0][1][:2], secs=round(time.perf_counter() - t0, 3))
    d2 = DOWNLOADS / "calamine_xlsb2" / "date.xlsb"
    doc = Doc(d2)
    line("A18 xlsb dates (calamine sample)", kind=doc.kind, rows=doc._sheets[0][1][:4])


if __name__ == "__main__":
    zip_case()
    broken_cases()
    xlsb_case()
