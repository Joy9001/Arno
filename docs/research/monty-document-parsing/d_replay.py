"""Part D3: can the text of a Code-Mode-written script (await + keyword-only args) run later in raw pydantic-monty
with the same host functions, outside Code Mode?"""
import sys as _s, pathlib as _p
_s.path.insert(0, str(_p.Path(__file__).resolve().parent))   # lets `python -I` (isolated mode) still import sibling modules
import asyncio
import json

from pydantic_monty import AsyncMonty, Monty, ResourceLimits

from common import FIX, PARSERS, load_truth, score
from hostfns import Doc, check_statement

PRELUDE = (PARSERS / "_prelude.py").read_text(encoding="utf-8")
CM_SCRIPT = PRELUDE + "\n" + (PARSERS / "cm_pdf_sep_cols.py").read_text(encoding="utf-8")
LIM = ResourceLimits(max_memory=256 * 1024 * 1024, max_feed_duration_secs=30, max_suspensions=5000)


async def raw_async(path):
    doc = Doc(path)

    async def doc_info(): return doc.info()
    async def doc_words(page): return doc.words(page)
    async def doc_cells(sheet=0): return doc.cells(sheet)
    async def check(rows, opening, closing): return check_statement(rows, opening, closing)
    funcs = {"doc_info": doc_info, "doc_words": doc_words, "doc_cells": doc_cells, "check_statement": check}
    async with AsyncMonty() as pool:
        async with pool.checkout(limits=LIM) as s:
            return await s.feed_run(CM_SCRIPT, external_lookup=funcs)


def main():
    for f in ("a1_sep_cols_jan.pdf", "a8_two_accounts_jan.pdf", "a1_sep_cols_feb.pdf"):
        p = FIX / f
        try:
            out = asyncio.run(raw_async(p))
            right, total, _ = score(out["accounts"], load_truth(p)["accounts"], "exact")
            print(f"raw AsyncMonty + async host fns, Code-Mode script text on {f}: rows {right}/{total}; checks",
                  [a["check"]["status"] for a in out["accounts"]])
        except Exception as e:
            print(f"raw AsyncMonty on {f}: {type(e).__name__}: {str(e)[-200:]}")
    # sync Monty with sync host functions: top-level await?
    doc = Doc(FIX / "a1_sep_cols_jan.pdf")
    funcs = {"doc_info": doc.info, "doc_words": doc.words, "doc_cells": doc.cells, "check_statement": check_statement}
    try:
        with Monty() as pool:
            with pool.checkout(limits=LIM) as s:
                out = s.feed_run(CM_SCRIPT, external_lookup=funcs)
        print("sync Monty + sync host fns, same script text (with await):", type(out).__name__, "ok" if out else out)
    except Exception as e:
        print("sync Monty + sync host fns, same script text (with await):", f"{type(e).__name__}: {str(e)[-200:]}")
    # the sync-style saved parser (no await, positional args) in AsyncMonty
    plain = PRELUDE + "\n" + (PARSERS / "pdf_sep_cols.py").read_text(encoding="utf-8")

    async def run_plain():
        d = Doc(FIX / "a1_sep_cols_jan.pdf")
        async def doc_info(): return d.info()
        async def doc_words(page): return d.words(page)
        async def check(rows, opening, closing): return check_statement(rows, opening, closing)
        async with AsyncMonty() as pool:
            async with pool.checkout(limits=LIM) as s:
                return await s.feed_run(plain, external_lookup={"doc_info": doc_info, "doc_words": doc_words, "check_statement": check})
    try:
        out = asyncio.run(run_plain())
        print("AsyncMonty + async fns, sync-style script (no await, positional):", "ran" if out else out)
    except Exception as e:
        print("AsyncMonty + async fns, sync-style script (no await, positional):", f"{type(e).__name__}: {str(e)[-240:]}")


if __name__ == "__main__":
    main()
