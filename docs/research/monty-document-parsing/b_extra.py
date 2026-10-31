"""Part B extras: float rounding agreement with CPython, and the decimal module in Monty."""
import sys as _s, pathlib as _p
_s.path.insert(0, str(_p.Path(__file__).resolve().parent))   # lets `python -I` (isolated mode) still import sibling modules
import random

from pydantic_monty import Monty


def main():
    rng = random.Random(1)
    xs = [round(rng.uniform(0, 100000), 3) for _ in range(3000)] + [i + 0.005 for i in range(0, 2000, 7)] + [2.675, 1.005, 0.285, 1.115, 8.345]
    code = """
out = []
for x in xs:
    out.append([round(x, 2), f'{x:.2f}', int(round(x * 100)), round(x * 100)])
out
"""
    with Monty() as pool:
        with pool.checkout() as s:
            got = s.feed_run(code, inputs={"xs": xs})
    ref = [[round(x, 2), f"{x:.2f}", int(round(x * 100)), round(x * 100)] for x in xs]
    n = len(xs)
    diff = [(x, r, g) for x, r, g in zip(xs, ref, got) if r != g]
    for col, name in enumerate(("round(x, 2)", "f'{x:.2f}'", "int(round(x*100))", "round(x*100)")):
        c = sum(1 for _, r, g in diff if r[col] != g[col])
        print(f"{name:20s} differs from CPython on {c} of {n} inputs")
    print("examples (x, cpython, monty):", diff[:5])

    dec = """
from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN, getcontext
a = Decimal('1234567.89')
r = [str(a * 100), str(Decimal('2.675').quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)), str(Decimal('2.675').quantize(Decimal('0.01'))),
     int(Decimal('1234.56') * 100), str(Decimal('0.1') + Decimal('0.2')), str(sum([Decimal('0.1')] * 10)),
     str(Decimal('1,00,000.00'.replace(',', '')) * 100), str(Decimal(1) / Decimal(3))[:12]]
r
"""
    ref_dec = None
    from decimal import Decimal, ROUND_HALF_UP
    ref_dec = [str(Decimal('1234567.89') * 100), str(Decimal('2.675').quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)), str(Decimal('2.675').quantize(Decimal('0.01'))),
               int(Decimal('1234.56') * 100), str(Decimal('0.1') + Decimal('0.2')), str(sum([Decimal('0.1')] * 10)),
               str(Decimal('100000.00') * 100), str(Decimal(1) / Decimal(3))[:12]]
    with Monty() as pool:
        with pool.checkout() as s:
            try:
                g = s.feed_run(dec)
                print("decimal in Monty:", g)
                print("decimal in CPython:", ref_dec, "| same:", g == ref_dec)
            except Exception as e:
                print("decimal in Monty failed:", type(e).__name__, str(e)[-200:])


if __name__ == "__main__":
    main()
