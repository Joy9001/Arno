"""Show the first mismatching rows (synthetic fixtures only): python diff_rows.py DOC PARSER [--pw P] [--ocr] [--n 6]"""
import sys as _s, pathlib as _p
_s.path.insert(0, str(_p.Path(__file__).resolve().parent))   # lets `python -I` (isolated mode) still import sibling modules
import argparse
import sys
from pathlib import Path

from common import FIX, PARSERS, load_truth, norm
from hostfns import Doc
from runner import run_text

ap = argparse.ArgumentParser()
ap.add_argument("doc"); ap.add_argument("parser"); ap.add_argument("--pw"); ap.add_argument("--ocr", action="store_true"); ap.add_argument("--n", type=int, default=6)
a = ap.parse_args()
d = a.doc if Path(a.doc).exists() else str(FIX / a.doc)
p = a.parser if Path(a.parser).exists() else str(PARSERS / a.parser)
ocr = None
if a.ocr:
    from ocr_host import ocr_words as ocr
doc = Doc(d, password=a.pw, ocr=ocr)
out, err, secs, stats = run_text(doc, Path(p).read_text(encoding="utf-8"))
if err:
    print("ERR", err); sys.exit()
truth = load_truth(d)["accounts"]
shown = 0
for ai, ta in enumerate(truth):
    got = out["accounts"][ai]["rows"] if ai < len(out["accounts"]) else []
    print(f"acct{ai}: got {len(got)} rows, truth {len(ta['rows'])}; opening {out['accounts'][ai].get('opening')} vs {ta['opening']}; closing {out['accounts'][ai].get('closing')} vs {ta['closing']}")
    for i, (g, t) in enumerate(zip(got, ta["rows"])):
        keys = ("date", "debit", "credit", "balance")
        bad = [k for k in keys if t.get(k) is not None and g.get(k) != t.get(k)]
        if bad or norm(g.get("desc", "")) != norm(t["desc"]) and not a.ocr:
            if shown < a.n:
                print(f" row {i}: bad={bad}\n   got  {g}\n   want {t}")
            shown += 1
print("mismatching rows:", shown)
