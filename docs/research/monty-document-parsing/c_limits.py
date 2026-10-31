"""Part C: limits and cost. Prints measurements; no pass/fail."""
import sys as _s, pathlib as _p
_s.path.insert(0, str(_p.Path(__file__).resolve().parent))   # lets `python -I` (isolated mode) still import sibling modules
import ast
import datetime as dt
import decimal
import fractions
import json
import pathlib
import time
import uuid
from collections import namedtuple
from dataclasses import dataclass

from pydantic_monty import Monty, MontyRuntimeError, ResourceLimits

from common import FIX, PARSERS
from hostfns import Doc, check_statement
from runner import PRELUDE, run_text


def pr(label, **kw):
    print(f"{label:46s}", json.dumps(kw, default=str, ensure_ascii=False)[:400])


def cpython_run(doc, script):
    ns = {"doc_info": doc.info, "doc_words": doc.words, "doc_cells": doc.cells, "check_statement": check_statement,
          "ledger_opening": lambda: None}
    tree = ast.parse(PRELUDE + "\n" + script)
    last = tree.body.pop()
    exec(compile(tree, "<s>", "exec"), ns)
    return eval(compile(ast.Expression(last.value), "<s>", "eval"), ns)


def a34_limits():
    doc = Doc(FIX / "a34_big_200p.pdf")
    t0 = time.perf_counter()
    for i in range(len(doc._pdf)):
        doc.words(i)
    pr("host: 182 pages -> words (pypdfium2)", seconds=round(time.perf_counter() - t0, 2))
    script = (PARSERS / "pdf_sep_cols.py").read_text(encoding="utf-8")
    out, err, secs, stats = run_text(doc, script)
    pr("Monty parse, words cached host-side", ok=err is None, seconds=round(secs, 2), calls=stats["calls"], mb_across=round(stats["bytes"] / 1e6, 2))
    t0 = time.perf_counter()
    res = cpython_run(doc, script)
    tc = time.perf_counter() - t0
    pr("CPython same script, words cached", seconds=round(tc, 2), rows=sum(len(a["rows"]) for a in res["accounts"]), slowdown_x=round(secs / tc, 1))
    for mb in (8, 16, 32, 64, 128, 256):
        out, err, secs2, _ = run_text(doc, script, limits=ResourceLimits(max_memory=mb * 1024 * 1024, max_feed_duration_secs=30))
        pr(f"Monty with max_memory={mb} MiB", result="ok" if err is None else err[-110:], seconds=round(secs2, 2))
    out, err, secs3, _ = run_text(doc, script, limits=ResourceLimits(max_memory=256 * 1024 * 1024, max_feed_duration_secs=2))
    pr("Monty with max_feed_duration_secs=2", result="ok" if err is None else err[-120:], seconds=round(secs3, 2))
    # CSV with 10k rows
    d2 = Doc(FIX / "a34_big_10k.csv")
    s2 = (PARSERS / "sheet_header.py").read_text(encoding="utf-8")
    out, err, secs, st = run_text(d2, s2)
    t0 = time.perf_counter(); cpython_run(d2, s2); tc = time.perf_counter() - t0
    pr("10k-row CSV: Monty vs CPython", monty_s=round(secs, 3), cpython_s=round(tc, 3), slowdown_x=round(secs / tc, 1), kb_across=round(st["bytes"] / 1e3))


def micro():
    snippets = {
        "int loop 1e6 (sum of squares)": "t = 0\nfor i in range(1000000):\n    t += i * i\nt",
        "str ops 2e5 (split/strip/join)": "n = 0\nfor i in range(200000):\n    n += len(' a,b ,c '.strip().split(','))\nn",
        "regex 5e4 searches": "import re\np = re.compile(r'(\\d+)/(\\d+)/(\\d{4})')\nn = 0\nfor i in range(50000):\n    if p.search('on 05/01/2026 ok'):\n        n += 1\nn",
        "sort 2e5 tuples with key": "xs = [(i * 7919) % 100003 for i in range(200000)]\nsorted(xs, key=lambda v: -v)[0]",
        "dict build 2e5": "d = {}\nfor i in range(200000):\n    d[str(i)] = i\nlen(d)",
    }
    with Monty() as pool:
        for name, code in snippets.items():
            t0 = time.perf_counter()
            with pool.checkout(limits=ResourceLimits(max_memory=256 * 1024 * 1024, max_feed_duration_secs=60)) as s:
                v = s.feed_run(code)
            tm = time.perf_counter() - t0
            g = {}
            tree = ast.parse(code); last = tree.body.pop()
            t0 = time.perf_counter()
            exec(compile(tree, "<m>", "exec"), g); v2 = eval(compile(ast.Expression(last.value), "<m>", "eval"), g)
            tc = time.perf_counter() - t0
            pr("micro: " + name, monty_s=round(tm, 3), cpython_s=round(tc, 3), slowdown_x=round(tm / max(tc, 1e-6), 1), same=(v == v2))


def suspensions():
    n_ok = None
    with Monty() as pool:
        for n in (500, 999, 1000, 1500, 5000):
            try:
                with pool.checkout() as s:
                    s.feed_run(f"for i in range({n}):\n    ping()\n'done'", external_lookup={"ping": lambda: 1})
                pr(f"{n} host calls, default max_suspensions", result="ok")
            except Exception as e:
                pr(f"{n} host calls, default max_suspensions", result=f"{type(e).__name__}: {str(e)[-120:]}")
        for n in (5000,):
            try:
                with pool.checkout(limits=ResourceLimits(max_suspensions=100000)) as s:
                    t0 = time.perf_counter()
                    s.feed_run(f"for i in range({n}):\n    ping()\n'done'", external_lookup={"ping": lambda: 1})
                pr(f"{n} host calls, max_suspensions=100000", result="ok", seconds=round(time.perf_counter() - t0, 2), per_call_ms=round((time.perf_counter() - t0) / n * 1000, 3))
            except Exception as e:
                pr(f"{n} host calls, max_suspensions=100000", result=f"{type(e).__name__}: {str(e)[-120:]}")
        # does a function-name lookup count? one call fn bound to local vs repeated lookups
        try:
            with pool.checkout() as s:
                s.feed_run("for i in range(900):\n    ping()\nfor i in range(900):\n    ping()\n'done'", external_lookup={"ping": lambda: 1})
            pr("1800 calls inside ONE snippet", result="ok (budget not per-snippet?)")
        except Exception as e:
            pr("1800 calls inside ONE snippet", result=f"{type(e).__name__}: {str(e)[-100:]}")
        with pool.checkout() as s:
            try:
                s.feed_run("for i in range(900):\n    ping()", external_lookup={"ping": lambda: 1})
                s.feed_run("for i in range(900):\n    ping()", external_lookup={"ping": lambda: 1})
                pr("2 snippets x 900 calls, same session", result="ok")
            except Exception as e:
                pr("2 snippets x 900 calls, same session", result=f"{type(e).__name__}: {str(e)[-100:]}")


def boundary():
    with Monty() as pool:
        for label, make in (("str of N bytes", lambda n: "x" * n), ("list of N small ints", lambda n: list(range(n // 8))),
                            ("list of N/100 word-dicts", lambda n: [{"text": "1,00,000.00", "x0": 1.5, "x1": 2.5, "top": 3.5, "bottom": 4.5}] * (n // 100)),
                            ("bytes of N", lambda n: b"x" * n)):
            for n in (10**6, 10**7, 5 * 10**7, 10**8):
                try:
                    t0 = time.perf_counter()
                    with pool.checkout(limits=ResourceLimits(max_memory=256 * 1024 * 1024, max_feed_duration_secs=60)) as s:
                        r = s.feed_run("len(big())", external_lookup={"big": lambda: make(n)})
                    pr(f"host return {label}, N={n:.0e}", result="ok", len=r, seconds=round(time.perf_counter() - t0, 2))
                except Exception as e:
                    pr(f"host return {label}, N={n:.0e}", result=f"{type(e).__name__}: {str(e)[-110:]}")
                    break


@dataclass
class DC:
    a: int


class Plain:
    def __init__(self):
        self.a = 1


def types():
    NT = namedtuple("NT", "a b")
    samples = {
        "int": 5, "big int": 10**30, "float": 1.5, "nan": float("nan"), "bool": True, "None": None, "str": "x₹", "bytes": b"ab",
        "bytearray": bytearray(b"ab"), "list": [1], "tuple": (1, 2), "set": {1}, "frozenset": frozenset({1}), "dict str keys": {"a": 1},
        "dict int keys": {1: "a"}, "date": dt.date(2026, 1, 5), "datetime": dt.datetime(2026, 1, 5, 1, 2), "timedelta": dt.timedelta(days=1),
        "Decimal": decimal.Decimal("1.50"), "Fraction": fractions.Fraction(1, 3), "UUID": uuid.UUID(int=1), "Path": pathlib.PurePosixPath("/a"),
        "dataclass": DC(1), "namedtuple": NT(1, 2), "plain object": Plain(), "complex": 1 + 2j, "range": range(3), "Ellipsis": ...,
    }
    with Monty() as pool:
        for name, v in samples.items():
            try:
                with pool.checkout() as s:
                    r = s.feed_run("x = get()\n[type(x).__name__, repr(x)[:60]]", external_lookup={"get": lambda v=v: v})
                pr(f"host -> Monty: {name}", arrives_as=r)
            except Exception as e:
                pr(f"host -> Monty: {name}", result=f"{type(e).__name__}: {str(e)[-100:]}")
    outs = {"tuple": "(1, 2)", "set": "{1, 2}", "date": "__import__('datetime').date(2026, 1, 5)", "timedelta": "__import__('datetime').timedelta(days=1)",
            "bytes": "b'ab'", "dict int keys": "{1: 'a'}", "nested": "{'a': [(1, {'b': None})]}", "float inf": "float('inf')", "big int": "10**30"}
    with Monty() as pool:
        for name, code in outs.items():
            try:
                with pool.checkout() as s:
                    r = s.feed_run(code)
                pr(f"Monty -> host: {name}", host_sees=f"{type(r).__name__}: {r!r}"[:100])
            except Exception as e:
                pr(f"Monty -> host: {name}", result=f"{type(e).__name__}: {str(e)[-100:]}")


if __name__ == "__main__":
    which = sys.argv[1:] if (sys := __import__("sys")).argv[1:] else ["a34", "micro", "susp", "boundary", "types"]
    if "a34" in which: a34_limits()
    if "micro" in which: micro()
    if "susp" in which: suspensions()
    if "boundary" in which: boundary()
    if "types" in which: types()
