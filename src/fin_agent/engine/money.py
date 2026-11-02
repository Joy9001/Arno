"""Amortization and compounding, in whole paise.

These are the two standard money formulas the agent must not retype in a script. Interest and
growth are floating point because a rate is; money is rounded back to whole paise at every step.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fin_agent.india.formatting import format_paise

HUNDRED = Decimal(100)
MONTHS_IN_YEAR = 12


def _paise(value: Decimal | float | int) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def money(paise: int | None) -> dict[str, Any] | None:
    """The shared Money shape: unknown is null, never zero."""
    if paise is None:
        return None
    value = int(paise)
    text = format_paise(value)
    if text.endswith(".00"):
        text = text[:-3]
    return {"paise": value, "text": text}


def _add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    # Keep the same day, capped at the month's last day.
    for candidate in range(day.day, 0, -1):
        try:
            return date(year, month, candidate)
        except ValueError:
            continue
    return date(year, month, 28)


def _emi_for(principal: Decimal, monthly_rate: Decimal, months: int) -> Decimal:
    if months <= 0:
        raise ValueError("months must be positive")
    if monthly_rate == 0:
        return principal / Decimal(months)
    factor = (Decimal(1) + monthly_rate) ** months
    return principal * monthly_rate * factor / (factor - Decimal(1))


@dataclass(frozen=True)
class _Walk:
    emi: Decimal
    interest: list[Decimal]
    principal: list[Decimal]
    balances: list[Decimal]


def _walk(principal: Decimal, monthly_rate: Decimal, emi: Decimal, months: int) -> _Walk:
    balance = principal
    interest: list[Decimal] = []
    principal_paid: list[Decimal] = []
    balances: list[Decimal] = []
    for _ in range(months):
        if balance <= 0:
            break
        month_interest = (balance * monthly_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        month_principal = min(emi - month_interest, balance)
        if month_principal <= 0:
            raise ValueError("this EMI never repays the loan")
        balances.append(balance)
        interest.append(month_interest)
        principal_paid.append(month_principal)
        balance -= month_principal
    return _Walk(emi=emi, interest=interest, principal=principal_paid, balances=balances)


def amortize(
    principal_paise: int,
    annual_rate: float,
    months: int,
    *,
    emi_paise: int | None = None,
    start_date: date | None = None,
    prepay: dict[str, Any] | None = None,
    assumptions: list[str] | None = None,
) -> dict[str, Any]:
    """EMI, total interest, payoff date, and a monthly schedule, optionally with a prepayment.

    ``annual_rate`` is a percentage (8.5 means 8.5% a year). A prepayment is applied before EMI
    number ``at_emi`` (counting from the next one) and either keeps the EMI and shortens the tenure
    or keeps the tenure and lowers the EMI.
    """
    if principal_paise <= 0:
        raise ValueError("principal must be positive")
    if months <= 0:
        raise ValueError("months must be positive")
    if annual_rate < 0:
        raise ValueError("rate cannot be negative")

    principal = Decimal(principal_paise)
    monthly_rate = Decimal(str(annual_rate)) / (HUNDRED * MONTHS_IN_YEAR)
    emi = Decimal(emi_paise) if emi_paise is not None else _emi_for(principal, monthly_rate, months)
    emi = emi.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    walk = _walk(principal, monthly_rate, emi, months)

    def schedule(w: _Walk) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for index, (balance, interest, principal_paid) in enumerate(
            zip(w.balances, w.interest, w.principal, strict=True), start=1
        ):
            rows.append(
                {
                    "n": index,
                    "date": _add_months(start_date, index).isoformat() if start_date else None,
                    "interest_paise": int(interest),
                    "principal_paise": int(principal_paid),
                    "balance_paise": int(balance - principal_paid),
                }
            )
        return rows

    total_interest = sum(walk.interest, Decimal(0))
    result: dict[str, Any] = {
        "emi": money(int(emi)),
        "months": len(walk.principal),
        "total_interest": money(int(total_interest)),
        "total_paid": money(int(emi * len(walk.principal))),
        "payoff_date": _add_months(start_date, len(walk.principal)).isoformat()
        if start_date
        else None,
        "schedule": schedule(walk),
        "prepay": None,
        "assumptions": assumptions
        or [f"{annual_rate:g}% a year, as entered; a floating rate can change"],
    }

    if prepay:
        unknown = sorted(set(prepay) - {"amount_paise", "at_emi", "reduce"})
        if unknown:
            # An ignored timing key once priced "after 12 EMIs" as "now" without a word.
            raise ValueError(
                f"prepay takes amount_paise, at_emi, and reduce, not {', '.join(unknown)}"
            )
        amount = int(prepay.get("amount_paise") or 0)
        if amount <= 0:
            raise ValueError("a prepayment must be positive")
        at_emi = int(prepay.get("at_emi") or 1)
        reduce = str(prepay.get("reduce") or "tenure")
        if reduce not in {"tenure", "emi"}:
            raise ValueError("reduce is tenure or emi")
        before = max(at_emi - 1, 0)
        paid_interest = sum(walk.interest[:before], Decimal(0))
        balance = principal - sum(walk.principal[:before], Decimal(0))
        balance = max(balance - Decimal(amount), Decimal(0))
        remaining = max(months - before, 1)

        if reduce == "tenure":
            new_emi = emi
            after = _walk(balance, monthly_rate, new_emi, remaining)
        else:
            new_emi = _emi_for(balance, monthly_rate, remaining).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
            after = _walk(balance, monthly_rate, new_emi, remaining)

        new_months = before + len(after.principal)
        new_total_interest = paid_interest + sum(after.interest, Decimal(0))
        result["prepay"] = {
            "new_emi": money(int(after.emi)),
            "new_months": new_months,
            "new_total_interest": money(int(new_total_interest)),
            "interest_saved": money(int(total_interest - new_total_interest)),
            "months_saved": len(walk.principal) - new_months,
        }
    return result


def compound(
    monthly_paise: int,
    annual_rate: float,
    months: int,
    *,
    start_paise: int = 0,
) -> dict[str, Any]:
    """Growth of a monthly investment at an assumed annual rate, paid at the start of each month.

    The rate is an assumption, not a prediction: the caller must state it in the answer.
    """
    if monthly_paise < 0 or start_paise < 0:
        raise ValueError("amounts cannot be negative")
    if months < 0:
        raise ValueError("months cannot be negative")
    if annual_rate < 0:
        raise ValueError("rate cannot be negative")

    monthly_rate = Decimal(str(annual_rate)) / (HUNDRED * MONTHS_IN_YEAR)
    balance = Decimal(start_paise)
    yearly: list[dict[str, Any]] = []
    for month in range(1, months + 1):
        balance = ((balance + Decimal(monthly_paise)) * (Decimal(1) + monthly_rate)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        if month % MONTHS_IN_YEAR == 0 or month == months:
            yearly.append({"year": (month + 11) // 12, "balance_paise": int(balance)})

    contributed = start_paise + monthly_paise * months
    return {
        "final": money(int(balance)),
        "contributed": money(contributed),
        "growth": money(int(balance) - contributed),
        "yearly": yearly,
        "assumptions": [
            f"{annual_rate:g}% a year, assumed",
            "paid at the start of each month",
        ],
    }


__all__ = ["amortize", "compound", "money"]
