"""Rupee formatting with Indian digit grouping (lakh and crore).

The web app formats money with `Intl.NumberFormat('en-IN')`. This is the server-side equivalent
for CLI output, logs and tests. Money crossing the API is always integer paise.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Context, Decimal, localcontext

RUPEE = "\u20b9"
_FORMAT_CONTEXT = Context(prec=28, rounding=ROUND_HALF_UP)


def _group_indian(digits: str) -> str:
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if len(head) > 0:
        groups.insert(0, head)
    return ",".join([*groups, tail])


def format_paise(paise: int | Decimal) -> str:
    """Format money as rupees: 123456789 -> '₹12,34,567.89'.

    A fractional paise rounds half up, so 123.5 paise is ₹1.24, not ₹1.23.
    """
    with localcontext(_FORMAT_CONTEXT):
        value = (
            paise
            if isinstance(paise, int)
            else int(Decimal(paise).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        )
    sign = "-" if value < 0 else ""
    rupees, fraction = divmod(abs(value), 100)
    return f"{sign}{RUPEE}{_group_indian(str(rupees))}.{fraction:02d}"
