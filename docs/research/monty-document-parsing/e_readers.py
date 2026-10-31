"""Part E: pypdfium2 words vs pdfplumber words on the PDF fixtures (text equality, rows recovered, time)."""
import sys as _s, pathlib as _p
_s.path.insert(0, str(_p.Path(__file__).resolve().parent))   # lets `python -I` (isolated mode) still import sibling modules
import time
from collections import Counter

import pdfplumber

from common import FIX
from hostfns import Doc

CASES = ["a1_sep_cols_jan.pdf", "a2_wrapped_jan.pdf", "a4_multipage_jan.pdf", "a6a_landscape_jan.pdf", "a6b_rotate90_jan.pdf",
         "a6c_landscape_rotated_upright_jan.pdf", "a7_card_jan.pdf", "a9_devanagari_jan.pdf", "a35_12month.pdf", "a38_mf_cas.pdf"]


def pl_words(path, page_no):
    with pdfplumber.open(path) as pdf:
        pg = pdf.pages[page_no]
        return [w["text"] for w in pg.extract_words(x_tolerance=1.5, y_tolerance=2)], pg


def main(big=True):
    print(f"{'file':40s} {'pages':>5s} {'pdfium_words':>12s} {'plumber_words':>13s} {'same_tokens':>11s} {'pdfium_s':>8s} {'plumber_s':>9s}")
    for f in CASES:
        p = FIX / f
        d = Doc(p)
        n = len(d._pdf)
        t0 = time.perf_counter()
        a = [w["text"] for i in range(n) for w in d._pdf_words(i)]
        ta = time.perf_counter() - t0
        t0 = time.perf_counter()
        with pdfplumber.open(p) as pdf:
            b = [w["text"] for pg in pdf.pages for w in pg.extract_words(x_tolerance=1.5, y_tolerance=2)]
        tb = time.perf_counter() - t0
        ca, cb = Counter(a), Counter(b)
        diff_a = sum((ca - cb).values()); diff_b = sum((cb - ca).values())
        print(f"{f:40s} {n:5d} {len(a):12d} {len(b):13d} {('yes' if not diff_a and not diff_b else f'-{diff_a}/+{diff_b}'):>11s} {ta:8.2f} {tb:9.2f}")
        if diff_a or diff_b:
            print("    only pdfium:", list((ca - cb).items())[:6], "  only plumber:", list((cb - ca).items())[:6])
    if big:
        p = FIX / "a34_big_200p.pdf"
        d = Doc(p)
        t0 = time.perf_counter()
        n = sum(len(d._pdf_words(i)) for i in range(len(d._pdf)))
        ta = time.perf_counter() - t0
        t0 = time.perf_counter()
        with pdfplumber.open(p) as pdf:
            m = sum(len(pg.extract_words(x_tolerance=1.5, y_tolerance=2)) for pg in pdf.pages)
        tb = time.perf_counter() - t0
        print(f"{'a34_big_200p.pdf':40s} {len(d._pdf):5d} {n:12d} {m:13d} {'':>11s} {ta:8.2f} {tb:9.2f}")


if __name__ == "__main__":
    main()
