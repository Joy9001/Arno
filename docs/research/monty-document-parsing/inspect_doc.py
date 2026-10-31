"""What the model sees: doc_info + a compact view of words/cells. Usage: inspect_doc.py FILE [password] [page/sheet] [maxlines]"""
import sys as _s, pathlib as _p
_s.path.insert(0, str(_p.Path(__file__).resolve().parent))   # lets `python -I` (isolated mode) still import sibling modules
import json
import sys

from hostfns import Doc, DocError


def lines_from_words(words, tol=3.0):
    words = sorted(words, key=lambda w: (round(w["top"] / tol), w["x0"]))
    out, cur, cy = [], [], None
    for w in words:
        yc = (w["top"] + w["bottom"]) / 2
        if cy is None or abs(yc - cy) > tol:
            if cur:
                out.append(cur)
            cur, cy = [w], yc
        else:
            cur.append(w)
    if cur:
        out.append(cur)
    return [sorted(l, key=lambda w: w["x0"]) for l in out]


def main():
    path = sys.argv[1]
    pw = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "-" else None
    which = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    maxl = int(sys.argv[4]) if len(sys.argv) > 4 else 25
    try:
        d = Doc(path, password=pw)
    except DocError as e:
        print("DocError:", e)
        return
    print("doc_info =", json.dumps(d.info(), ensure_ascii=False)[:1500])
    if d._pdf is not None:
        for ln in lines_from_words(d.words(which))[:maxl]:
            print(" | ".join(f"{w['text']}@{w['x0']:.0f}-{w['x1']:.0f}" for w in ln)[:260], f"   (y={ln[0]['top']:.0f})")
    elif d._sheets is not None:
        for i, r in enumerate(d.cells(which)[:maxl]):
            print(i, json.dumps(r, ensure_ascii=False)[:230])


if __name__ == "__main__":
    main()
