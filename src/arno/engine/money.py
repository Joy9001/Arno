"""Amortization, compounding, rate inversion, scaling, allocation, and card EMI, in whole paise.

These are the standard money formulas the agent must not retype in a script. Rates use Decimal;
money is rounded back to whole paise at every step.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Context, Decimal, localcontext
from functools import wraps
from typing import Literal, NotRequired, TypedDict, overload

from arno.india.formatting import format_paise

HUNDRED = Decimal(100)
MONTHS_IN_YEAR = 12
DEFAULT_GST_BPS = 1800
DEFAULT_MINIMUM_BPS = 500
DEFAULT_MINIMUM_FLOOR_PAISE = 20_000
_RATE_UNIT_SCALE = Decimal(10_000)
# shortcut: India nominal 12*monthly IRR and 30e/360 + actual/365f only, revisit if a non-India
# locale pack or US 30/360 / EU effective APR is requested
_APR_MONTHLY_SCALE = Decimal(12_000_000)  # 0.0001% a year / 12 months
_MAX_RATE_UNIT = 1_000_000  # 100.0000% a year in 0.0001% (bps_x100) units
# The kernel keeps balances in signed 64-bit integers; a larger amount would wrap silently.
MAX_PAISE = 9_223_372_036_854_775_807
_MAX_MONTHS = 600

# A caller's global Decimal context (precision, rounding, traps) must not move a paisa.
_DECIMAL_CONTEXT = Context(prec=28, rounding=ROUND_HALF_UP)

DayCount = Literal["30e/360", "actual/365f", "30/360", "actual/365"]
_CanonicalDayCount = Literal["30e/360", "actual/365f"]
# 30E/360 is the Eurobond basis; actual/365 fixed is ISDA's Actual/365 (Fixed).
_DAY_COUNT_ALIASES = {"30/360": "30e/360", "actual/365": "actual/365f"}


def _pinned[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Run the function in the module's Decimal context, whatever the caller set globally."""

    @wraps(function)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        with localcontext(_DECIMAL_CONTEXT):
            return function(*args, **kwargs)

    return run


def _canonical_day_count(day_count: str) -> _CanonicalDayCount:
    canonical = _DAY_COUNT_ALIASES.get(day_count, day_count)
    if canonical not in ("30e/360", "actual/365f"):
        raise ValueError("day_count must be '30e/360' or 'actual/365f'")
    return canonical


def check_paise(value: int) -> None:
    """Reject an amount the kernel's signed 64-bit balances cannot hold."""
    if abs(value) > MAX_PAISE:
        raise ValueError(f"amount is beyond the supported maximum of {MAX_PAISE} paise")


def _check_months(months: int, *, minimum: int = 1) -> None:
    if not minimum <= months <= _MAX_MONTHS:
        raise ValueError(f"months must be between {minimum} and {_MAX_MONTHS}")


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


class RateFit(TypedDict):
    """Bracketing interval for an inferred annual rate in 0.0001% (bps x 100) units."""

    low_bps_x100: int | None
    high_bps_x100: int | None
    exact: bool
    note: str
    matches_emi: NotRequired[bool]


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
    complete_cost: bool
    not_included: list[str]
    processing_fee: NotRequired[Money]
    fee_gst: NotRequired[Money]
    total_fee: NotRequired[Money]
    net_disbursed: NotRequired[Money]
    total_cost: NotRequired[Money]
    apr: NotRequired[RateFit]


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


class CardPayoffRow(TypedDict):
    """One month of a revolving credit card payoff schedule."""

    n: int
    date: str | None
    opening_paise: int
    finance_charge_paise: int
    gst_paise: int
    payment_paise: int
    closing_paise: int


class CardPayoff(TypedDict):
    """Revolving credit card payoff schedule, totals, and carrying cost."""

    months: int
    total_finance_charge: Money
    total_gst: Money
    total_paid: Money
    cost_of_carrying: Money
    payoff_date: str | None
    schedule: list[CardPayoffRow]
    assumptions: list[str]
    complete_cost: bool
    not_included: list[str]


class Offer(TypedDict):
    label: str
    cash_price_paise: int
    discount_paise: NotRequired[int]
    cashback_paise: NotRequired[int]
    financing: NotRequired[OfferFinancing | None]


class OfferFinancing(TypedDict):
    kind: Literal["card_emi", "loan"]
    # Missing is refused by compare_offers with a message the model can act on, not at the schema.
    annual_rate_bps: NotRequired[int]
    months: int
    processing_fee_paise: NotRequired[int]


class OfferComparison(TypedDict):
    label: str
    cash_price_paise: int
    discount_paise: int
    cashback_paise: int
    interest_paise: int
    gst_on_interest_paise: int
    processing_fee_paise: int
    gst_on_fee_paise: int
    total_paid_paise: int
    net_cost_paise: int
    difference_vs_cash_paise: int
    # Each other offer's net cost subtracted from this one, in input order: positive costs more.
    difference_vs_offers: list[OfferDifference]
    # What each month's instalment actually charges, for a financed offer; empty for cash.
    instalments_paise: list[int]
    apr: RateFit | None
    complete_cost: bool
    not_included: list[str]


class OfferDifference(TypedDict):
    label: str
    difference_paise: int


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
    """30E/360 day count: both endpoints clamp to the 30th of their month."""
    day1 = min(first.day, 30)
    day2 = min(second.day, 30)
    return (second.year - first.year) * 360 + (second.month - first.month) * 30 + (day2 - day1)


def _validate_amortize_conventions(
    *,
    rest: Literal["monthly", "daily"],
    day_count: DayCount,
    interest_unit_paise: int,
    emi_rounding: Literal["half_up", "up"],
    emi_unit_paise: int,
    start_date: date | None = None,
    require_start_date_for_daily: bool = True,
) -> None:
    if rest not in ("monthly", "daily"):
        raise ValueError("rest must be 'monthly' or 'daily'")
    _canonical_day_count(day_count)
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
    day_count: _CanonicalDayCount,
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
    if day_count == "actual/365f":
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
    day_count: _CanonicalDayCount = "30e/360",
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
    max_steps = _MAX_MONTHS if unbounded else target_months

    for offset in range(max_steps):
        if balance <= 0:
            break
        index = start_index + offset
        if rate_changes_map is not None and index in rate_changes_map:
            current_rate = rate_changes_map[index]
            # A prepayment path runs its own first month from the EMI the caller kept, so a reset
            # coinciding with it must not recompute that EMI.
            if reset == "emi" and index > start_index:
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
            raise ValueError(f"this EMI never repays the loan within {_MAX_MONTHS} months")

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


def _schedule_pv(payments: Sequence[int], unit: int) -> Decimal:
    if unit == 0:
        return Decimal(sum(payments))
    monthly_rate = Decimal(unit) / _APR_MONTHLY_SCALE
    one_plus_r = Decimal(1) + monthly_rate
    pv = Decimal(0)
    for payment in reversed(payments):
        pv = (pv + Decimal(payment)) / one_plus_r
    return pv


@_pinned
def _compute_apr(net_disbursed_paise: int, payments: Sequence[int]) -> RateFit:
    if net_disbursed_paise <= 0:
        raise ValueError("net disbursed amount must be positive")
    if len(payments) == 0 or any(payment <= 0 for payment in payments):
        raise ValueError("schedule payments must be positive")

    target = Decimal(net_disbursed_paise)
    undiscounted = _schedule_pv(payments, 0)
    if undiscounted < target:
        # Every rate above zero discounts the payments further, so no non-negative APR exists.
        raise ValueError(
            "payments repay less than the net amount disbursed, so the APR is negative"
        )
    if undiscounted == target:
        return {
            "low_bps_x100": 0,
            "high_bps_x100": 0,
            "exact": True,
            "note": "exact APR 0.0000% a year",
        }

    hi = _MAX_RATE_UNIT
    while _schedule_pv(payments, hi) > target:
        hi *= 2

    # Largest integer rate unit whose present value is at least the net disbursed amount.
    lo = 0
    right = hi
    while lo < right:
        mid = (lo + right + 1) // 2
        if _schedule_pv(payments, mid) >= target:
            lo = mid
        else:
            right = mid - 1

    if _schedule_pv(payments, lo) == target:
        return {
            "low_bps_x100": lo,
            "high_bps_x100": lo,
            "exact": True,
            "note": f"exact APR {lo / 10_000:.4f}% a year",
        }

    high_unit = lo + 1
    return {
        "low_bps_x100": lo,
        "high_bps_x100": high_unit,
        "exact": False,
        "note": f"bracketed between {lo / 10_000:.4f}% and {high_unit / 10_000:.4f}% a year",
    }


@_pinned
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
    day_count: DayCount = "30e/360",
    interest_unit_paise: int = 1,
    emi_rounding: Literal["half_up", "up"] = "half_up",
    emi_unit_paise: int = 1,
    rate_changes: Sequence[tuple[int, float]] = (),
    reset: Literal["tenure", "emi"] = "tenure",
    processing_fee_paise: int = 0,
    gst_bps: int = DEFAULT_GST_BPS,
) -> Amortization:
    """EMI, total interest, payoff date, and a monthly schedule, optionally with a prepayment.

    ``annual_rate`` is a percentage (8.5 means 8.5% a year). A prepayment is applied before EMI
    number ``at_emi`` (counting from the next one) and either keeps the EMI and shortens the tenure
    or keeps the EMIs the loan has left and lowers the EMI. When ``processing_fee_paise`` is
    positive, the fee plus GST is treated as deducted at disbursal and the result includes the
    effective annual percentage rate (APR) on the baseline schedule only, even if ``prepay`` is
    given.
    """
    if principal_paise <= 0:
        raise ValueError("principal must be positive")
    _check_months(months)
    check_paise(principal_paise)
    if emi_paise is not None:
        check_paise(emi_paise)
    if annual_rate < 0:
        raise ValueError("rate cannot be negative")
    if processing_fee_paise < 0:
        raise ValueError("processing_fee_paise cannot be negative")
    check_paise(processing_fee_paise)
    if gst_bps < 0:
        raise ValueError("gst_bps cannot be negative")
    fee_gst_paise = 0
    total_fee_paise = 0
    if processing_fee_paise > 0:
        fee_gst_paise = _round_half_up_div(processing_fee_paise * gst_bps, 10_000)
        total_fee_paise = processing_fee_paise + fee_gst_paise
        if total_fee_paise >= principal_paise:
            raise ValueError("processing fee and GST must be less than principal")
    day_count = _canonical_day_count(day_count)
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
        if change_emi > months:
            # A reset beyond the stated tenure would silently stretch the loan.
            raise ValueError(
                f"a rate reset at EMI {change_emi} is beyond the {months}-month tenure"
            )
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
    elif day_count != "30e/360":
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
    if emi_paise is None and emi_unit_paise > 1 and len(walk.principal) > 0:
        last_total = walk.principal[-1] + walk.interest[-1]
        if abs(last_total - emi) > emi_unit_paise:
            # Rounding the EMI to a coarse unit leaves a balloon the EMI figure hides.
            notes.append(
                f"the last instalment is {money(int(last_total))['text']}, not "
                f"{money(int(emi))['text']}: the EMI was rounded to {emi_unit_paise} paise, so "
                "the last month pays the remainder"
            )

    total_interest = sum(walk.interest, Decimal(0))
    schedule_rows = schedule(walk)
    not_included = [
        "foreclosure and prepayment charges",
        "late and contingent charges",
    ]
    if len(rate_changes) == 0:
        not_included.append("future floating-rate resets not supplied")
    result: Amortization = {
        "emi": money(int(emi)),
        "months": len(walk.principal),
        "total_interest": money(int(total_interest)),
        "total_paid": money(int(principal + total_interest)),
        "payoff_date": add_months(start_date, len(walk.principal)).isoformat()
        if start_date is not None
        else None,
        "schedule": schedule_rows,
        "prepay": None,
        "assumptions": notes,
        "complete_cost": False,
        "not_included": not_included,
    }

    if processing_fee_paise > 0:
        net_disbursed_paise = principal_paise - total_fee_paise
        total_cost_paise = int(total_interest) + total_fee_paise
        payments = [row["interest_paise"] + row["principal_paise"] for row in schedule_rows]
        fee_text = money(processing_fee_paise)["text"]
        notes.append(
            f"processing fee of {fee_text} plus {gst_bps / 100:g}% GST treated as deducted at "
            "disbursal"
        )
        result["processing_fee"] = money(processing_fee_paise)
        result["fee_gst"] = money(fee_gst_paise)
        result["total_fee"] = money(total_fee_paise)
        result["net_disbursed"] = money(net_disbursed_paise)
        result["total_cost"] = money(total_cost_paise)
        result["apr"] = _compute_apr(net_disbursed_paise, payments)

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
        check_paise(amount)
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
            # The EMI actually charged at at_emi, including a reset that lands on the same month.
            new_emi = walk.emis[before]
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


@_pinned
def infer_rate(
    principal_paise: int,
    months: int,
    emi_paise: int,
    *,
    rest: Literal["monthly", "daily"] = "monthly",
    day_count: DayCount = "30e/360",
    interest_unit_paise: int = 1,
    emi_rounding: Literal["half_up", "up"] = "half_up",
    emi_unit_paise: int = 1,
) -> RateFit:
    """Infer the annual rate interval in 0.0001% (bps x 100) units for an observed EMI.

    The EMI is assumed monthly-rest, so a daily rest is refused rather than silently ignored.
    ``exact`` is true only when one rate unit reproduces the EMI; a wider interval means several
    rates round to the same instalment, and the note carries the range.
    """
    if principal_paise <= 0:
        raise ValueError("principal must be positive")
    _check_months(months)
    check_paise(principal_paise)
    if emi_paise < 0:
        raise ValueError("emi cannot be negative")
    check_paise(emi_paise)
    if rest != "monthly":
        raise ValueError("infer_rate supports monthly rest only")
    day_count = _canonical_day_count(day_count)
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
                "matches_emi": False,
                "note": "EMI cannot repay the principal in the given tenure (below 0% EMI)",
            }
        return {
            "low_bps_x100": None,
            "high_bps_x100": None,
            "exact": False,
            "matches_emi": False,
            "note": "EMI is below the rounded 0% EMI under these conventions",
        }

    max_emi = emi_at(_MAX_RATE_UNIT)
    if emi_paise > max_emi:
        return {
            "low_bps_x100": None,
            "high_bps_x100": None,
            "exact": False,
            "matches_emi": False,
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
        exact = first_ge == last_le
        if first_ge == 0:
            note = (
                f"zero-rate loan (0% to {last_le / 10_000:.4f}% a year)"
                if last_le > 0
                else "zero-rate loan (0% a year)"
            )
        elif exact:
            note = f"exact rate {first_ge / 10_000:.4f}% a year"
        else:
            note = f"{first_ge / 10_000:.4f}% to {last_le / 10_000:.4f}% a year"
        return {
            "low_bps_x100": first_ge,
            "high_bps_x100": last_le,
            "exact": exact,
            "matches_emi": True,
            "note": note,
        }

    return {
        "low_bps_x100": last_le,
        "high_bps_x100": first_ge,
        "exact": False,
        "matches_emi": False,
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


@_pinned
def card_emi(
    principal_paise: int,
    *,
    annual_rate_bps: int,
    months: int,
    processing_fee_paise: int = 0,
    gst_bps: int = DEFAULT_GST_BPS,
) -> CardEmi:
    """Monthly reducing-balance credit card EMI schedule with GST on interest and fee.

    Source: CSB Bank One credit card EMI terms (April 2024): GST (18% / 1800 bps) applies on
    the processing fee, interest, and foreclosure charges; interest uses a monthly reducing
    balance and each instalment is added to the monthly bill.
    """
    if principal_paise <= 0:
        raise ValueError("principal must be positive")
    check_paise(principal_paise)
    if annual_rate_bps < 0:
        raise ValueError("rate cannot be negative")
    if not 2 <= months <= 48:
        raise ValueError("months must be between 2 and 48")
    if processing_fee_paise < 0:
        raise ValueError("processing_fee_paise cannot be negative")
    check_paise(processing_fee_paise)
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
        if balance <= 0:
            # A small principal can clear before the last named instalment; the rest are zero.
            break
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


def card_payoff(
    balance_paise: int,
    *,
    annual_rate_bps: int,
    payment: Literal["minimum"] | int,
    minimum_bps: int | None = None,
    minimum_floor_paise: int | None = None,
    gst_bps: int = DEFAULT_GST_BPS,
    start_date: date | None = None,
) -> CardPayoff:
    """Monthly revolving credit card payoff schedule, totals, and carrying cost.

    Each month runs in five steps: (1) finance charge = opening balance * annual rate / 12,
    rounded half-up to whole paise; (2) GST = finance charge * GST rate, rounded half-up to whole
    paise; (3) statement balance = opening + finance charge + GST; (4) payment = for
    ``"minimum"``, the larger of ``minimum_bps`` of the statement balance (rounded up to a whole
    paisa) and ``minimum_floor_paise``, capped at the statement balance, or for a fixed integer
    payment, that amount capped at the statement balance; (5) closing balance = statement balance -
    payment. Stops when closing reaches zero. A payment that does not reduce the balance is
    refused at once, not walked to the horizon. Assumes no new spending and no interest-free
    period while a balance is carried.
    """
    if balance_paise <= 0:
        raise ValueError("balance must be positive")
    check_paise(balance_paise)
    if annual_rate_bps < 0:
        raise ValueError("rate cannot be negative")
    if gst_bps < 0:
        raise ValueError("gst_bps cannot be negative")

    effective_minimum_bps = DEFAULT_MINIMUM_BPS if minimum_bps is None else minimum_bps
    effective_minimum_floor = (
        DEFAULT_MINIMUM_FLOOR_PAISE if minimum_floor_paise is None else minimum_floor_paise
    )
    if effective_minimum_bps <= 0:
        raise ValueError("minimum_bps must be positive")
    if effective_minimum_floor <= 0:
        raise ValueError("minimum_floor_paise must be positive")
    check_paise(effective_minimum_floor)

    is_minimum = payment == "minimum"
    fixed_payment = 0
    if not is_minimum:
        if not isinstance(payment, int) or isinstance(payment, bool):
            raise ValueError("payment must be 'minimum' or a positive integer amount in paise")
        fixed_payment = payment
        check_paise(fixed_payment)
        first_charge = _round_half_up_div(balance_paise * annual_rate_bps, 120_000)
        first_gst = _round_half_up_div(first_charge * gst_bps, 10_000)
        if fixed_payment <= first_charge + first_gst:
            raise ValueError("this payment never clears the balance")

    balance = balance_paise
    schedule: list[CardPayoffRow] = []
    total_finance_charge = 0
    total_gst = 0
    total_paid = 0

    for month in range(1, _MAX_MONTHS + 1):
        if balance <= 0:
            break
        finance_charge = _round_half_up_div(balance * annual_rate_bps, 120_000)
        gst = _round_half_up_div(finance_charge * gst_bps, 10_000)
        statement = balance + finance_charge + gst
        if is_minimum:
            pct_due = (statement * effective_minimum_bps + 9_999) // 10_000
            due = max(pct_due, effective_minimum_floor)
        else:
            due = fixed_payment
        paid = min(due, statement)
        closing = statement - paid
        if closing >= balance:
            # A minimum due at or below the month's charges grows the balance forever.
            raise ValueError(
                "the payment does not cover the finance charge, so the balance never falls"
            )
        schedule.append(
            {
                "n": month,
                "date": add_months(start_date, month).isoformat()
                if start_date is not None
                else None,
                "opening_paise": balance,
                "finance_charge_paise": finance_charge,
                "gst_paise": gst,
                "payment_paise": paid,
                "closing_paise": closing,
            }
        )
        total_finance_charge += finance_charge
        total_gst += gst
        total_paid += paid
        balance = closing
    else:
        if balance > 0:
            raise ValueError(f"card payoff exceeds maximum horizon of {_MAX_MONTHS} months")

    notes: list[str] = [
        f"{annual_rate_bps / 100:g}% a year ({annual_rate_bps} bps) finance charge, with "
        f"{gst_bps / 100:g}% GST on the finance charge",
        "monthly approximation: finance charge is applied once per month on the opening balance "
        "(issuers typically accrue daily)",
    ]
    if is_minimum and (minimum_bps is None or minimum_floor_paise is None):
        if minimum_bps is None and minimum_floor_paise is None:
            floor_text = money(effective_minimum_floor)["text"]
            notes.append(
                f"minimum due assumed at {effective_minimum_bps / 100:g}% of statement balance "
                f"or {floor_text} floor (issuers differ)"
            )
        elif minimum_bps is None:
            notes.append(
                f"minimum due percentage assumed at {effective_minimum_bps / 100:g}% of "
                "statement balance (issuers differ)"
            )
        else:
            floor_text = money(effective_minimum_floor)["text"]
            notes.append(f"minimum due floor assumed at {floor_text} (issuers differ)")
    notes.append(
        "assumes no new spending on the card; while a balance is carried, new purchases get "
        "no interest-free period"
    )

    months = len(schedule)
    return {
        "months": months,
        "total_finance_charge": money(total_finance_charge),
        "total_gst": money(total_gst),
        "total_paid": money(total_paid),
        "cost_of_carrying": money(total_finance_charge + total_gst),
        "payoff_date": add_months(start_date, months).isoformat()
        if start_date is not None
        else None,
        "schedule": schedule,
        "assumptions": notes,
        "complete_cost": False,
        "not_included": ["late and contingent charges", "future rate changes"],
    }


def compare_offers(offers: Sequence[Offer]) -> list[OfferComparison]:
    """Compare stated purchase costs in input order, without choosing an offer."""
    if len(offers) < 2:
        raise ValueError("at least two offers are required")
    comparisons: list[OfferComparison] = []
    for offer in offers:
        price = offer["cash_price_paise"]
        discount = offer.get("discount_paise", 0)
        cashback = offer.get("cashback_paise", 0)
        if offer["label"].strip() == "" or price <= 0 or min(discount, cashback) < 0:
            raise ValueError("offer needs a label, positive price and non-negative discounts")
        check_paise(price)
        check_paise(discount)
        check_paise(cashback)
        principal = price - discount
        if principal <= 0 or cashback > principal:
            raise ValueError("discounts cannot exceed the cash price")
        financing = offer.get("financing")
        interest = interest_gst = fee = fee_gst = 0
        instalments: list[int] = []
        apr: RateFit | None = None
        omitted: list[str] = []
        if cashback > 0:
            omitted.append("cashback eligibility and payment conditions")
        if financing is not None:
            rate = financing.get("annual_rate_bps")
            if rate is None:
                raise ValueError("financing requires an annual rate")
            months = financing["months"]
            fee = financing.get("processing_fee_paise", 0)
            check_paise(fee)
            if financing["kind"] == "card_emi":
                emi = card_emi(
                    principal, annual_rate_bps=rate, months=months, processing_fee_paise=fee
                )
                interest = emi["total_interest_paise"]
                interest_gst = sum(row["gst_paise"] for row in emi["schedule"])
                fee_gst = sum(row["fee_gst_paise"] for row in emi["schedule"])
                instalments = [row["total_paise"] for row in emi["schedule"]]
                apr = _compute_apr(principal, instalments)
            elif financing["kind"] == "loan":
                loan = amortize(principal, rate / 100, months, processing_fee_paise=fee)
                interest = loan["total_interest"]["paise"]
                fee_gst = loan.get("fee_gst", money(0))["paise"]
                instalments = [
                    row["principal_paise"] + row["interest_paise"] for row in loan["schedule"]
                ]
                apr = loan.get("apr")
                if apr is None:
                    apr = _compute_apr(principal, instalments)
            else:
                raise ValueError("financing kind must be card_emi or loan")
            omitted.extend(
                [
                    "foreclosure and prepayment charges",
                    "late and contingent charges",
                    "future floating-rate resets not supplied",
                ]
            )
        total = principal + interest + interest_gst + fee + fee_gst
        net_cost = total - cashback
        comparisons.append(
            {
                "label": offer["label"],
                "cash_price_paise": price,
                "discount_paise": discount,
                "cashback_paise": cashback,
                "interest_paise": interest,
                "gst_on_interest_paise": interest_gst,
                "processing_fee_paise": fee,
                "gst_on_fee_paise": fee_gst,
                "total_paid_paise": total,
                "net_cost_paise": net_cost,
                "difference_vs_cash_paise": net_cost - price,
                "difference_vs_offers": [],
                "instalments_paise": instalments,
                "apr": apr,
                "complete_cost": len(omitted) == 0,
                "not_included": omitted,
            }
        )
    for index, row in enumerate(comparisons):
        row["difference_vs_offers"] = [
            {
                "label": other["label"],
                "difference_paise": row["net_cost_paise"] - other["net_cost_paise"],
            }
            for other_index, other in enumerate(comparisons)
            if other_index != index
        ]
    return comparisons


@_pinned
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
    _check_months(months, minimum=0)
    check_paise(monthly_paise)
    check_paise(start_paise)
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


def apr_percent(fit: RateFit | None) -> float | None:
    """Quotable annual APR percent rounded to two decimals from a RateFit interval."""
    if fit is None:
        return None
    low = fit["low_bps_x100"]
    high = fit["high_bps_x100"]
    if low is None or high is None:
        return None
    if low == 0 and high == 0:
        return 0.0
    midpoint = (Decimal(low) + Decimal(high)) / Decimal(2) / Decimal(10_000)
    return float(midpoint.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


__all__ = [
    "apr_percent",
    "MAX_PAISE",
    "Amortization",
    "CardEmi",
    "CardEmiRow",
    "CardPayoff",
    "CardPayoffRow",
    "CompoundResult",
    "DayCount",
    "Money",
    "Offer",
    "OfferComparison",
    "OfferFinancing",
    "Prepay",
    "PrepayResult",
    "RateFit",
    "ScheduleRow",
    "YearlyRow",
    "add_months",
    "allocate",
    "amortize",
    "card_emi",
    "card_payoff",
    "check_paise",
    "compound",
    "compare_offers",
    "infer_rate",
    "money",
    "scale_cumulative",
]
