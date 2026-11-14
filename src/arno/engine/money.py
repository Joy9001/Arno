"""Amortization, compounding, rate inversion, scaling, allocation, and card EMI, in whole paise.

These are the standard money formulas the agent must not retype in a script. Rates use Decimal;
money is rounded back to whole paise at every step.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Literal, TypedDict, overload

from arno.india.formatting import format_paise

HUNDRED = Decimal(100)
MONTHS_IN_YEAR = 12
_RATE_UNIT_SCALE = Decimal(10_000)
_MAX_RATE_UNIT = 1_000_000  # 100.0000% a year in 0.0001% (bps_x100) units


def _paise(value: Decimal | float | int) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _round_unit(
    value: Decimal,
    unit_paise: int,
    rounding: Literal["half_up", "up"] = "half_up",
) -> Decimal:
    unit = Decimal(unit_paise)
    mode = ROUND_HALF_UP if rounding == "half_up" else ROUND_CEILING
    return (value / unit).quantize(Decimal("1"), rounding=mode) * unit


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


class RateFit(TypedDict):
    """Bracketing interval for an inferred annual rate in 0.0001% (bps x 100) units."""

    low_bps_x100: int | None
    high_bps_x100: int | None
    exact: bool
    note: str


class CardEmiRow(TypedDict):
    """One monthly instalment of a credit card EMI plan."""

    n: int
    principal_paise: int
    interest_paise: int
    gst_paise: int
    fee_paise: int
    fee_gst_paise: int
    total_paise: int


class CardEmi(TypedDict):
    """Total cost and instalment schedule for a credit card EMI plan."""

    principal_paise: int
    months: int
    emi_paise: int
    schedule: list[CardEmiRow]
    total_interest_paise: int
    total_gst_paise: int
    fee_paise: int
    cost_paise: int
    total_paise: int


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


def add_months(day: date, months: int) -> date:
    """Add calendar months to a date, clamping to the target month's last day."""
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


_add_months = add_months


def _days_30_360(first: date, second: date) -> int:
    day1 = min(first.day, 30)
    day2 = min(second.day, 30)
    return (second.year - first.year) * 360 + (second.month - first.month) * 30 + (day2 - day1)


def _validate_amortize_conventions(
    *,
    rest: Literal["monthly", "daily"],
    day_count: Literal["30/360", "actual/365"],
    interest_unit_paise: int,
    emi_rounding: Literal["half_up", "up"],
    emi_unit_paise: int,
    start_date: date | None = None,
    require_start_date_for_daily: bool = True,
) -> None:
    if rest not in ("monthly", "daily"):
        raise ValueError("rest must be 'monthly' or 'daily'")
    if day_count not in ("30/360", "actual/365"):
        raise ValueError("day_count must be '30/360' or 'actual/365'")
    if require_start_date_for_daily and rest == "daily" and start_date is None:
        raise ValueError("daily rest requires start_date")
    if interest_unit_paise <= 0:
        raise ValueError("interest_unit_paise must be positive")
    if emi_rounding not in ("half_up", "up"):
        raise ValueError("emi_rounding must be 'half_up' or 'up'")
    if emi_unit_paise <= 0:
        raise ValueError("emi_unit_paise must be positive")


def _emi_for(principal: Decimal, monthly_rate: Decimal, months: int) -> Decimal:
    if months <= 0:
        raise ValueError("months must be positive")
    if monthly_rate == 0:
        return principal / Decimal(months)
    factor = (Decimal(1) + monthly_rate) ** months
    return principal * monthly_rate * factor / (factor - Decimal(1))


def _compute_emi(
    principal: Decimal,
    annual_rate_pct: Decimal,
    months: int,
    emi_rounding: Literal["half_up", "up"],
    emi_unit_paise: int,
) -> Decimal:
    monthly_rate = annual_rate_pct / (HUNDRED * MONTHS_IN_YEAR)
    raw = _emi_for(principal, monthly_rate, months)
    return _round_unit(raw, emi_unit_paise, emi_rounding)


def _period_interest(
    balance: Decimal,
    annual_rate_pct: Decimal,
    index: int,
    *,
    rest: Literal["monthly", "daily"],
    day_count: Literal["30/360", "actual/365"],
    start_date: date | None,
    interest_unit_paise: int,
) -> Decimal:
    if rest == "monthly":
        raw = balance * annual_rate_pct / (HUNDRED * MONTHS_IN_YEAR)
        return _round_unit(raw, interest_unit_paise, "half_up")
    if start_date is None:
        raise ValueError("daily rest requires start_date")
    prev_date = start_date if index == 1 else add_months(start_date, index - 1)
    curr_date = add_months(start_date, index)
    if day_count == "actual/365":
        days = (curr_date - prev_date).days
        basis = 365
    else:
        days = _days_30_360(prev_date, curr_date)
        basis = 360
    raw = balance * annual_rate_pct * Decimal(days) / (HUNDRED * Decimal(basis))
    return _round_unit(raw, interest_unit_paise, "half_up")


@dataclass(frozen=True)
class _Walk:
    emi: Decimal
    interest: list[Decimal]
    principal: list[Decimal]
    balances: list[Decimal]
    emis: list[Decimal]
    annual_rates: list[Decimal]


def _walk(
    principal: Decimal,
    initial_annual_rate: Decimal,
    initial_emi: Decimal,
    target_months: int,
    *,
    unbounded: bool,
    start_index: int = 1,
    start_date: date | None = None,
    rest: Literal["monthly", "daily"] = "monthly",
    day_count: Literal["30/360", "actual/365"] = "30/360",
    interest_unit_paise: int = 1,
    emi_rounding: Literal["half_up", "up"] = "half_up",
    emi_unit_paise: int = 1,
    rate_changes_map: dict[int, Decimal] | None = None,
    reset: Literal["tenure", "emi"] = "tenure",
    reset_end_index: int | None = None,
) -> _Walk:
    balance = principal
    interest: list[Decimal] = []
    principal_paid: list[Decimal] = []
    balances: list[Decimal] = []
    emis: list[Decimal] = []
    annual_rates: list[Decimal] = []

    current_rate = initial_annual_rate
    current_emi = initial_emi
    end_index = reset_end_index if reset_end_index is not None else start_index + target_months - 1
    max_steps = max(target_months * 20, 12_000) if unbounded else target_months

    for offset in range(max_steps):
        if balance <= 0:
            break
        index = start_index + offset
        if rate_changes_map is not None and index in rate_changes_map:
            current_rate = rate_changes_map[index]
            if reset == "emi" and index > 1:
                remaining_for_reset = max(end_index - index + 1, 1)
                current_emi = _compute_emi(
                    balance, current_rate, remaining_for_reset, emi_rounding, emi_unit_paise
                )
        month_interest = _period_interest(
            balance,
            current_rate,
            index,
            rest=rest,
            day_count=day_count,
            start_date=start_date,
            interest_unit_paise=interest_unit_paise,
        )
        month_principal = min(current_emi - month_interest, balance)
        if month_principal <= 0:
            raise ValueError("this EMI never repays the loan")
        balances.append(balance)
        interest.append(month_interest)
        principal_paid.append(month_principal)
        emis.append(current_emi)
        annual_rates.append(current_rate)
        balance -= month_principal
    else:
        if unbounded and balance > 0:
            raise ValueError("this EMI never repays the loan")

    if not unbounded and balance > 0 and len(principal_paid) > 0:
        # A rounded EMI can leave a few paise after the last month; the final installment pays
        # them, so the schedule's principal always sums to the loan.
        principal_paid[-1] += balance
    return _Walk(
        emi=initial_emi,
        interest=interest,
        principal=principal_paid,
        balances=balances,
        emis=emis,
        annual_rates=annual_rates,
    )


def amortize(
    principal_paise: int,
    annual_rate: float,
    months: int,
    *,
    emi_paise: int | None = None,
    start_date: date | None = None,
    prepay: Prepay | None = None,
    assumptions: list[str] | None = None,
    rest: Literal["monthly", "daily"] = "monthly",
    day_count: Literal["30/360", "actual/365"] = "30/360",
    interest_unit_paise: int = 1,
    emi_rounding: Literal["half_up", "up"] = "half_up",
    emi_unit_paise: int = 1,
    rate_changes: Sequence[tuple[int, float]] = (),
    reset: Literal["tenure", "emi"] = "tenure",
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
    _validate_amortize_conventions(
        rest=rest,
        day_count=day_count,
        interest_unit_paise=interest_unit_paise,
        emi_rounding=emi_rounding,
        emi_unit_paise=emi_unit_paise,
        start_date=start_date,
        require_start_date_for_daily=True,
    )
    if reset not in ("tenure", "emi"):
        raise ValueError("reset must be 'tenure' or 'emi'")

    rate_changes_map: dict[int, Decimal] = {}
    prev_change_emi = 0
    for change_emi, new_rate in rate_changes:
        if change_emi <= 0:
            raise ValueError("rate_changes EMI numbers must be positive")
        if change_emi <= prev_change_emi:
            raise ValueError("rate_changes must be in strictly increasing EMI order")
        if new_rate < 0:
            raise ValueError("rate cannot be negative")
        rate_changes_map[change_emi] = Decimal(str(new_rate))
        prev_change_emi = change_emi

    principal = Decimal(principal_paise)
    initial_rate = rate_changes_map.get(1, Decimal(str(annual_rate)))
    if emi_paise is not None:
        if emi_paise <= 0:
            raise ValueError("this EMI never repays the loan")
        emi = Decimal(emi_paise)
    else:
        emi = _compute_emi(principal, initial_rate, months, emi_rounding, emi_unit_paise)

    unbounded = emi_paise is not None or (len(rate_changes_map) > 0 and reset == "tenure")
    walk = _walk(
        principal,
        initial_rate,
        emi,
        months,
        unbounded=unbounded,
        start_index=1,
        start_date=start_date,
        rest=rest,
        day_count=day_count,
        interest_unit_paise=interest_unit_paise,
        emi_rounding=emi_rounding,
        emi_unit_paise=emi_unit_paise,
        rate_changes_map=rate_changes_map if len(rate_changes_map) > 0 else None,
        reset=reset,
        reset_end_index=months,
    )

    def schedule(w: _Walk) -> list[ScheduleRow]:
        rows: list[ScheduleRow] = []
        for index, (balance, interest, principal_paid) in enumerate(
            zip(w.balances, w.interest, w.principal, strict=True), start=1
        ):
            rows.append(
                {
                    "n": index,
                    "date": add_months(start_date, index).isoformat()
                    if start_date is not None
                    else None,
                    "interest_paise": int(interest),
                    "principal_paise": int(principal_paid),
                    "balance_paise": int(balance - principal_paid),
                }
            )
        return rows

    notes = (
        list(assumptions)
        if assumptions is not None and len(assumptions) > 0
        else [f"{annual_rate:g}% a year, as entered; a floating rate can change"]
    )
    if rest != "monthly":
        notes.append(f"daily rest ({day_count})")
    elif day_count != "30/360":
        notes.append(f"day count: {day_count}")
    if interest_unit_paise != 1:
        notes.append(f"interest rounded half-up to {interest_unit_paise} paise")
    if emi_rounding != "half_up" or emi_unit_paise != 1:
        notes.append(f"EMI rounded {emi_rounding.replace('_', '-')} to {emi_unit_paise} paise")
    if len(rate_changes) > 0:
        summary = ", ".join(f"{rate:g}% from EMI {idx}" for idx, rate in rate_changes)
        notes.append(f"rate resets ({reset}): {summary}")
    if emi_paise is not None and len(walk.principal) > months:
        notes.append(f"this EMI takes {len(walk.principal)} months, not {months}")

    total_interest = sum(walk.interest, Decimal(0))
    result: Amortization = {
        "emi": money(int(emi)),
        "months": len(walk.principal),
        "total_interest": money(int(total_interest)),
        "total_paid": money(int(principal + total_interest)),
        "payoff_date": add_months(start_date, len(walk.principal)).isoformat()
        if start_date is not None
        else None,
        "schedule": schedule(walk),
        "prepay": None,
        "assumptions": notes,
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
        if Decimal(amount) > balance:
            raise ValueError(
                f"prepayment of {amount} paise exceeds outstanding balance of {int(balance)} paise"
            )
        balance -= Decimal(amount)
        # The EMIs the loan actually has left, not the caller's tenure cap: a loan on track to
        # finish early would otherwise be re-stretched to its original tenure.
        remaining = max(len(walk.principal) - before, 1)

        rate_at_emi = walk.annual_rates[before]
        if reduce == "tenure":
            new_emi = walk.emis[before - 1] if before > 0 else walk.emi
        else:
            new_emi = _compute_emi(balance, rate_at_emi, remaining, emi_rounding, emi_unit_paise)

        after = _walk(
            balance,
            rate_at_emi,
            new_emi,
            remaining,
            unbounded=unbounded,
            start_index=at_emi,
            start_date=start_date,
            rest=rest,
            day_count=day_count,
            interest_unit_paise=interest_unit_paise,
            emi_rounding=emi_rounding,
            emi_unit_paise=emi_unit_paise,
            rate_changes_map=rate_changes_map if len(rate_changes_map) > 0 else None,
            reset=reset,
            reset_end_index=at_emi + remaining - 1,
        )

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


def infer_rate(
    principal_paise: int,
    months: int,
    emi_paise: int,
    *,
    rest: Literal["monthly", "daily"] = "monthly",
    day_count: Literal["30/360", "actual/365"] = "30/360",
    interest_unit_paise: int = 1,
    emi_rounding: Literal["half_up", "up"] = "half_up",
    emi_unit_paise: int = 1,
) -> RateFit:
    """Infer the annual rate interval in 0.0001% (bps x 100) units for an observed EMI."""
    if principal_paise <= 0:
        raise ValueError("principal must be positive")
    if months <= 0:
        raise ValueError("months must be positive")
    if emi_paise < 0:
        raise ValueError("emi cannot be negative")
    _validate_amortize_conventions(
        rest=rest,
        day_count=day_count,
        interest_unit_paise=interest_unit_paise,
        emi_rounding=emi_rounding,
        emi_unit_paise=emi_unit_paise,
        require_start_date_for_daily=False,
    )

    principal = Decimal(principal_paise)

    def emi_at(unit: int) -> int:
        rate_pct = Decimal(unit) / _RATE_UNIT_SCALE
        return int(_compute_emi(principal, rate_pct, months, emi_rounding, emi_unit_paise))

    zero_emi = emi_at(0)
    if emi_paise < zero_emi:
        if emi_paise * months < principal_paise:
            return {
                "low_bps_x100": None,
                "high_bps_x100": None,
                "exact": False,
                "note": "EMI cannot repay the principal in the given tenure (below 0% EMI)",
            }
        return {
            "low_bps_x100": None,
            "high_bps_x100": None,
            "exact": False,
            "note": "EMI is below the rounded 0% EMI under these conventions",
        }

    max_emi = emi_at(_MAX_RATE_UNIT)
    if emi_paise > max_emi:
        return {
            "low_bps_x100": None,
            "high_bps_x100": None,
            "exact": False,
            "note": "EMI exceeds the 100% annual rate search range",
        }

    # Smallest unit whose EMI is at least the observed EMI.
    lo = 0
    hi = _MAX_RATE_UNIT
    while lo < hi:
        mid = (lo + hi) // 2
        if emi_at(mid) >= emi_paise:
            hi = mid
        else:
            lo = mid + 1
    first_ge = lo

    # Largest unit whose EMI is at most the observed EMI.
    lo = 0
    hi = _MAX_RATE_UNIT
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if emi_at(mid) <= emi_paise:
            lo = mid
        else:
            hi = mid - 1
    last_le = lo

    if first_ge <= last_le:
        if first_ge == 0:
            note = (
                f"zero-rate loan (0% to {last_le / 10_000:.4f}% a year)"
                if last_le > 0
                else "zero-rate loan (0% a year)"
            )
        elif first_ge == last_le:
            note = f"exact rate {first_ge / 10_000:.4f}% a year"
        else:
            note = f"{first_ge / 10_000:.4f}% to {last_le / 10_000:.4f}% a year"
        return {
            "low_bps_x100": first_ge,
            "high_bps_x100": last_le,
            "exact": True,
            "note": note,
        }

    return {
        "low_bps_x100": last_le,
        "high_bps_x100": first_ge,
        "exact": False,
        "note": (
            f"no exact unit match; bracketed between {last_le / 10_000:.4f}% and "
            f"{first_ge / 10_000:.4f}% a year"
        ),
    }


def _round_half_up_div(num: int, den: int) -> int:
    if num >= 0:
        return (2 * num + den) // (2 * den)
    return -((2 * (-num) + den) // (2 * den))


def scale_cumulative(amounts: Sequence[int], numerator: int, denominator: int) -> list[int]:
    """Scale each amount using cumulative half-up rounding in pure integer arithmetic."""
    if denominator == 0:
        raise ValueError("denominator cannot be zero")
    num = int(numerator)
    den = int(denominator)
    if den < 0:
        num = -num
        den = -den

    result: list[int] = []
    running = 0
    prev_rounded = 0
    for amount in amounts:
        running += int(amount)
        curr_rounded = _round_half_up_div(running * num, den)
        result.append(curr_rounded - prev_rounded)
        prev_rounded = curr_rounded
    return result


def allocate(total_paise: int, weights: Sequence[int]) -> list[int]:
    """Split ``total_paise`` across non-negative integer ``weights`` by largest remainder."""
    if len(weights) == 0:
        raise ValueError("weights must be non-empty")
    int_weights = [int(weight) for weight in weights]
    if any(weight < 0 for weight in int_weights):
        raise ValueError("weights must be non-negative")
    total_weight = sum(int_weights)
    if total_weight <= 0:
        raise ValueError("at least one weight must be positive")

    total = int(total_paise)
    sign = -1 if total < 0 else 1
    abs_total = abs(total)

    parts: list[int] = []
    remainders: list[tuple[int, int]] = []
    for index, weight in enumerate(int_weights):
        if weight == 0:
            parts.append(0)
        else:
            quotient, remainder = divmod(abs_total * weight, total_weight)
            parts.append(quotient)
            remainders.append((remainder, index))

    leftover = abs_total - sum(parts)
    if leftover > 0:
        remainders.sort(key=lambda item: (-item[0], item[1]))
        for _remainder, index in remainders[:leftover]:
            parts[index] += 1

    if sign < 0:
        return [-part for part in parts]
    return parts


def card_emi(
    principal_paise: int,
    *,
    annual_rate_bps: int,
    months: int,
    processing_fee_paise: int = 0,
    gst_bps: int = 1800,
) -> CardEmi:
    """Monthly reducing-balance credit card EMI schedule with GST on interest and fee.

    Source: CSB Bank One credit card EMI terms (April 2024): GST (18% / 1800 bps) applies on
    the processing fee, interest, and foreclosure charges; interest uses a monthly reducing
    balance and each instalment is added to the monthly bill.
    """
    if principal_paise <= 0:
        raise ValueError("principal must be positive")
    if annual_rate_bps < 0:
        raise ValueError("rate cannot be negative")
    if not 2 <= months <= 48:
        raise ValueError("months must be between 2 and 48")
    if processing_fee_paise < 0:
        raise ValueError("processing_fee_paise cannot be negative")
    if gst_bps < 0:
        raise ValueError("gst_bps cannot be negative")

    principal = Decimal(principal_paise)
    monthly_rate = Decimal(annual_rate_bps) / Decimal(120_000)
    gst_rate = Decimal(gst_bps) / Decimal(10_000)
    emi = _emi_for(principal, monthly_rate, months).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    balance = principal
    interests: list[int] = []
    principals: list[int] = []
    for _ in range(months):
        month_interest = int(
            (balance * monthly_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        month_principal = min(int(emi) - month_interest, int(balance))
        if month_principal <= 0:
            raise ValueError("this EMI never repays the loan")
        interests.append(month_interest)
        principals.append(month_principal)
        balance -= Decimal(month_principal)
    if balance > 0 and len(principals) > 0:
        principals[-1] += int(balance)

    fee_gst_paise = int(
        (Decimal(processing_fee_paise) * gst_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )

    schedule: list[CardEmiRow] = []
    total_interest_paise = 0
    total_gst_paise = 0
    for index, (month_principal, month_interest) in enumerate(
        zip(principals, interests, strict=True), start=1
    ):
        interest_gst = int(
            (Decimal(month_interest) * gst_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        row_fee = processing_fee_paise if index == 1 else 0
        row_fee_gst = fee_gst_paise if index == 1 else 0
        row_total = month_principal + month_interest + interest_gst + row_fee + row_fee_gst
        schedule.append(
            {
                "n": index,
                "principal_paise": month_principal,
                "interest_paise": month_interest,
                "gst_paise": interest_gst,
                "fee_paise": row_fee,
                "fee_gst_paise": row_fee_gst,
                "total_paise": row_total,
            }
        )
        total_interest_paise += month_interest
        total_gst_paise += interest_gst + row_fee_gst

    cost_paise = total_interest_paise + total_gst_paise + processing_fee_paise
    total_paise = principal_paise + cost_paise
    return {
        "principal_paise": principal_paise,
        "months": months,
        "emi_paise": int(emi),
        "schedule": schedule,
        "total_interest_paise": total_interest_paise,
        "total_gst_paise": total_gst_paise,
        "fee_paise": processing_fee_paise,
        "cost_paise": cost_paise,
        "total_paise": total_paise,
    }


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


__all__ = [
    "Amortization",
    "CardEmi",
    "CardEmiRow",
    "CompoundResult",
    "Money",
    "Prepay",
    "PrepayResult",
    "RateFit",
    "ScheduleRow",
    "YearlyRow",
    "add_months",
    "allocate",
    "amortize",
    "card_emi",
    "compound",
    "infer_rate",
    "money",
    "scale_cumulative",
]
