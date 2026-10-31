"""Run a Monty parser script against a document and score it.

    python runner.py DOC PARSER [--pw PASSWORD] [--ocr] [--desc exact|nospace|fuzzy|none] [--log NAME]

The script text is run in a fresh Monty session with these host functions:
doc_info(), doc_words(page), doc_cells(sheet), check_statement(rows, opening, closing).
The script's last expression must be {'accounts': [{'name','opening','closing','rows':[...]}]}
(or {'not_a_statement': ..., 'fields': {...}}). The server-side check is then re-run on the result.
"""
import sys as _s, pathlib as _p
_s.path.insert(0, str(_p.Path(__file__).resolve().parent))   # lets `python -I` (isolated mode) still import sibling modules
import argparse
import json
import sys
import time
from pathlib import Path

from pydantic_monty import Monty, MontyCrashedError, MontyRuntimeError, MontySyntaxError, ResourceLimits

from common import FIX, HERE, PARSERS, load_truth, score
from hostfns import Doc, DocError, check_statement

PRELUDE = (PARSERS / "_prelude.py").read_text(encoding="utf-8") if (PARSERS / "_prelude.py").exists() else ""
LOG = Path(__file__).with_name("attempts.jsonl")
DEFAULT_LIMITS = ResourceLimits(max_memory=256 * 1024 * 1024, max_feed_duration_secs=30)


def make_funcs(doc: Doc, stats: dict):
    def counted(name, fn):
        def w(*a, **k):
            stats["calls"][name] = stats["calls"].get(name, 0) + 1
            try:
                r = fn(*a, **k)
            except DocError as e:
                raise ValueError(str(e))          # surfaces inside the script as a ValueError
            try:
                n = len(json.dumps(r, default=str))
            except Exception:
                n = 0
            stats["bytes"] += n
            stats["max_return"] = max(stats["max_return"], n)
            return r
        return w
    return {
        "doc_info": counted("doc_info", doc.info),
        "doc_words": counted("doc_words", doc.words),
        "doc_cells": counted("doc_cells", doc.cells),
        "check_statement": counted("check_statement", check_statement),
        "ledger_opening": counted("ledger_opening", lambda: stats.get("ledger_opening")),
    }


def run_text(doc: Doc, script: str, limits=DEFAULT_LIMITS, with_prelude=True, ledger_opening=None):
    stats = {"calls": {}, "bytes": 0, "max_return": 0, "ledger_opening": ledger_opening}
    funcs = make_funcs(doc, stats)
    t0 = time.perf_counter()
    err = None
    out = None
    try:
        with Monty(request_timeout=60) as pool:
            with pool.checkout(limits=limits) as s:
                out = s.feed_run((PRELUDE + "\n" if with_prelude else "") + script, external_lookup=funcs)
    except MontySyntaxError as e:
        err = f"SyntaxError: {str(e)[:400]}"
    except MontyRuntimeError as e:
        err = f"RuntimeError: {str(e)[-500:]}"
    except MontyCrashedError as e:
        err = f"Crashed: {str(e)[:300]}"
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:300]}"
    return out, err, time.perf_counter() - t0, stats


def evaluate(doc_path, parser_path, password=None, ocr=None, desc_mode="exact", log=None, quiet=False, limits=DEFAULT_LIMITS, ledger_opening=None):
    ocr_fn = None
    if ocr:
        from ocr_host import ocr_words
        ocr_fn = ocr_words
    t_open = time.perf_counter()
    try:
        doc = Doc(doc_path, password=password, ocr=ocr_fn)
    except DocError as e:
        res = dict(doc=Path(doc_path).name, parser=Path(parser_path).name, status="HOST_ERROR", error=str(e))
        _log(res, log, quiet)
        return res
    open_s = time.perf_counter() - t_open
    script = Path(parser_path).read_text(encoding="utf-8")
    _t = load_truth(doc_path)
    if ledger_opening is None and _t and _t.get("accounts"):
        ledger_opening = _t["accounts"][0].get("opening")
    out, err, secs, stats = run_text(doc, script, limits=limits, ledger_opening=ledger_opening)
    res = dict(doc=Path(doc_path).name, parser=Path(parser_path).name, kind=doc.kind, host_open_s=round(open_s, 3),
               monty_s=round(secs, 3), calls=stats["calls"], bytes=stats["bytes"], max_return=stats["max_return"])
    if err:
        res.update(status="SCRIPT_ERROR", error=err)
        _log(res, log, quiet)
        return res
    if not isinstance(out, dict):
        res.update(status="BAD_RESULT", error=f"script returned {type(out).__name__}")
        _log(res, log, quiet)
        return res
    if out.get("not_a_statement") or out.get("kind"):
        res.update(status="NOT_A_STATEMENT", note=str(out.get("not_a_statement") or out.get("kind"))[:200], fields=out.get("fields"))
        truth = load_truth(doc_path)
        if truth and truth.get("fields") and out.get("fields") is not None:
            tf = truth["fields"]
            res["fields_right"] = sum(1 for k, v in tf.items() if str(out["fields"].get(k)) == str(v))
            res["fields_total"] = len(tf)
            res["fields_wrong"] = {k: [out["fields"].get(k), v] for k, v in tf.items() if str(out["fields"].get(k)) != str(v)}
        _log(res, log, quiet)
        return res
    if not out.get("accounts"):
        res.update(status="NO_ACCOUNTS", note="script found no statement structure")
        _log(res, log, quiet)
        return res
    accts = out.get("accounts") or []
    truth = load_truth(doc_path)
    anchors = []
    for ai, a in enumerate(accts):          # the ledger supplies a missing opening; a missing closing is the last row balance
        if a.get("opening") is None and truth and ai < len(truth["accounts"]):
            a["opening"] = truth["accounts"][ai]["opening"]; anchors.append("opening-from-ledger")
        if a.get("closing") is None and a.get("rows") and a["rows"][-1].get("balance") is not None:
            a["closing"] = a["rows"][-1]["balance"]; anchors.append("closing-derived")
    if anchors:
        res["anchors"] = sorted(set(anchors))
    checks = [check_statement(a.get("rows", []), a.get("opening"), a.get("closing")) for a in accts]
    statuses = [c["status"] for c in checks]
    res["checks"] = [{k: c.get(k) for k in ("status", "rows", "total_ok", "chain_ok", "chain_breaks", "dates_monotonic", "reason")} for c in checks]
    res["status"] = "VERIFIED" if statuses and all(s == "VERIFIED" for s in statuses) else (statuses[0] if len(set(statuses)) == 1 else "MIXED")
    if truth and desc_mode != "none":
        right, total, notes = score(accts, truth["accounts"], desc_mode)
        res.update(rows_right=right, rows_total=total, notes=notes)
    _log(res, log, quiet)
    return res


def _log(res, log, quiet):
    if log:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(case=log, ts=time.strftime("%H:%M:%S"), **res), ensure_ascii=False, default=str) + "\n")
    if not quiet:
        print(json.dumps(res, ensure_ascii=False, default=str, indent=None))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("doc"); ap.add_argument("parser")
    ap.add_argument("--pw"); ap.add_argument("--ocr", action="store_true")
    ap.add_argument("--desc", default="exact"); ap.add_argument("--log"); ap.add_argument("--ledger-opening", type=int)
    a = ap.parse_args()
    d = a.doc if Path(a.doc).exists() else str(FIX / a.doc)
    p = a.parser if Path(a.parser).exists() else str(PARSERS / a.parser)
    evaluate(d, p, a.pw, a.ocr, a.desc, a.log, ledger_opening=a.ledger_opening)
