"""Amortization and compounding, in whole paise.

These are the two standard money formulas the agent must not retype in a script. Interest and
growth are floating point because a rate is; money is rounded back to whole paise at every step.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import TypedDict, overload

from arno.india.formatting import format_paise

HUNDRED = Decimal(100)
MONTHS_IN_YEAR = 12


def _paise(value: Decimal | float | int) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class Money(TypedDict):
    """The shared Money shape: whole paise and its display text."""

    paise: int
    text: str


class ScheduleRow(TypedDict):
    """One month of an amortization schedule."""

    n: int
    date: str | None
    interest_paise: int
    principal_paise: int
    balance_paise: int


class Prepay(TypedDict, total=False):
    """A prepayment: the lump sum, the EMI it lands before, and what it keeps."""

    amount_paise: int
    at_emi: int
    reduce: str


class PrepayResult(TypedDict):
    """What the prepayment changed, beside the loan it was applied to."""

    new_emi: Money
    new_months: int
    new_total_interest: Money
    interest_saved: Money
    months_saved: int


class Amortization(TypedDict):
    """An EMI plan: the schedule, its totals, and the prepayment if one was asked for."""

    emi: Money
    months: int
    total_interest: Money
    total_paid: Money
    payoff_date: str | None
    schedule: list[ScheduleRow]
    prepay: PrepayResult | None
    assumptions: list[str]


class YearlyRow(TypedDict):
    """The balance at the end of one year of compounding."""

    year: int
    balance_paise: int


class CompoundResult(TypedDict):
    """Growth at an assumed rate: the final value, what went in, and the growth."""

    final: Money
    contributed: Money
    growth: Money
    yearly: list[YearlyRow]
    assumptions: list[str]


@overload
def money(paise: None) -> None: ...


@overload
def money(paise: int) -> Money: ...


def money(paise: int | None) -> Money | None:
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
    if balance > 0 and len(principal_paid) > 0:
        # A rounded EMI can leave a few paise after the last month; the final installment pays
        # them, so the schedule's principal always sums to the loan.
        principal_paid[-1] += balance
    return _Walk(emi=emi, interest=interest, principal=principal_paid, balances=balances)


def amortize(
    principal_paise: int,
    annual_rate: float,
    months: int,
    *,
    emi_paise: int | None = None,
    start_date: date | None = None,
    prepay: Prepay | None = None,
    assumptions: list[str] | None = None,
) -> Amortization:
    """EMI, total interest, payoff date, and a monthly schedule, optionally with a prepayment.

    ``annual_rate`` is a percentage (8.5 means 8.5% a year). A prepayment is applied before EMI
    number ``at_emi`` (counting from the next one) and either keeps the EMI and shortens the tenure
    or keeps the EMIs the loan has left and lowers the EMI.
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

    def schedule(w: _Walk) -> list[ScheduleRow]:
        rows: list[ScheduleRow] = []
        for index, (balance, interest, principal_paid) in enumerate(
            zip(w.balances, w.interest, w.principal, strict=True), start=1
        ):
            rows.append(
                {
                    "n": index,
                    "date": _add_months(start_date, index).isoformat()
                    if start_date is not None
                    else None,
                    "interest_paise": int(interest),
                    "principal_paise": int(principal_paid),
                    "balance_paise": int(balance - principal_paid),
                }
            )
        return rows

    total_interest = sum(walk.interest, Decimal(0))
    result: Amortization = {
        "emi": money(int(emi)),
        "months": len(walk.principal),
        "total_interest": money(int(total_interest)),
        "total_paid": money(int(principal + total_interest)),
        "payoff_date": _add_months(start_date, len(walk.principal)).isoformat()
        if start_date is not None
        else None,
        "schedule": schedule(walk),
        "prepay": None,
        "assumptions": assumptions
        if assumptions is not None and len(assumptions) > 0
        else [f"{annual_rate:g}% a year, as entered; a floating rate can change"],
    }

    if prepay is not None and len(prepay) > 0:
        unknown = sorted(set(prepay) - {"amount_paise", "at_emi", "reduce"})
        if len(unknown) > 0:
            # An ignored timing key once priced "after 12 EMIs" as "now" without a word.
            raise ValueError(
                f"prepay takes amount_paise, at_emi, and reduce, not {', '.join(unknown)}"
            )
        raw_amount = prepay.get("amount_paise")
        amount = int(raw_amount) if raw_amount is not None and raw_amount != 0 else 0
        if amount <= 0:
            raise ValueError("a prepayment must be positive")
        raw_at_emi = prepay.get("at_emi")
        at_emi = int(raw_at_emi) if raw_at_emi is not None else 1
        if not 1 <= at_emi <= len(walk.principal):
            raise ValueError("at_emi must be between the next EMI and the last one")
        raw_reduce = prepay.get("reduce")
        reduce = str(raw_reduce) if raw_reduce is not None and raw_reduce != "" else "tenure"
        if reduce not in {"tenure", "emi"}:
            raise ValueError("reduce is tenure or emi")
        before = at_emi - 1
        paid_interest = sum(walk.interest[:before], Decimal(0))
        balance = principal - sum(walk.principal[:before], Decimal(0))
        balance = max(balance - Decimal(amount), Decimal(0))
        # The EMIs the loan actually has left, not the caller's tenure cap: a loan on track to
        # finish early would otherwise be re-stretched to its original tenure.
        remaining = max(len(walk.principal) - before, 1)

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
) -> CompoundResult:
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
    yearly: list[YearlyRow] = []
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
