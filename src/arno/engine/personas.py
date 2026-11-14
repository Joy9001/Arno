"""Deterministic generator of adversarial synthetic Indian household ledgers."""

from __future__ import annotations

import calendar as _calendar
import math
import random
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from typing import Literal

from arno.india.recurrence import expand_rule
from arno.india.workdays import HolidayAdjust, IndiaCalendar, calendar_for

HISTORY_START = date(2024, 6, 1)
END = date(2026, 10, 31)
PERSONA_KEYS: tuple[str, ...] = (
    "salaried_steady",
    "heavy_tails",
    "family_runs",
    "lifestyle_drift",
    "rare_large",
    "freelancer",
    "tight_card",
    "new_user",
    "messy_recurring",
    "seasonal_bills",
)
TUNING_SEEDS = (101, 102)
# The windows the floor is calibrated on: weekly origins over the last year, 90 days each.
SCORED_ORIGINS = 52
SCORED_DAYS = 90
# Weekend days raise the count and size of discretionary purchases.
WEEKEND_LIFT = {
    "food": (1.35, 1.10),
    "entertainment": (1.6, 1.15),
    "shopping": (1.3, 1.05),
    "transport": (1.2, 1.0),
}
HOLDOUT_SEEDS = (201, 202)

_MONTH_ABBR = (
    "",
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)


@dataclass(frozen=True, slots=True)
class Card:
    id: str
    name: str
    statement_day: int
    due_day: int
    credit_limit_paise: int


@dataclass(frozen=True, slots=True)
class Category:
    id: str
    name: str
    reducible: bool
    stated_per_day_paise: int | None


@dataclass(frozen=True, slots=True)
class Schedule:
    id: str
    name: str
    kind: str
    rule: str
    starts: date
    ends: date | None
    amount_paise: int
    holiday_adjust: str
    card_id: str | None
    category_id: str | None
    goal_id: str | None
    confirmed: bool
    created: date


@dataclass(frozen=True, slots=True)
class Goal:
    id: str
    name: str
    target_paise: int
    deadline: date | None
    priority: int
    created: date


@dataclass(frozen=True, slots=True)
class Movement:
    id: str
    date: date
    amount_paise: int
    kind: str
    account: str
    category_id: str | None
    payee: str
    payee_key: str
    series_id: str | None
    goal_id: str | None = None


@dataclass(frozen=True, slots=True)
class Series:
    id: str
    payee_key: str
    kind: str
    cadence: str
    schedule_id: str | None
    start: date
    end: date | None


@dataclass(frozen=True, slots=True)
class Persona:
    key: str
    seed: int
    description: str
    history_start: date
    end: date
    opening_bank_paise: int
    floor_paise: int
    cards: tuple[Card, ...]
    categories: tuple[Category, ...]
    schedules: tuple[Schedule, ...]
    goals: tuple[Goal, ...]
    movements: tuple[Movement, ...]
    series: tuple[Series, ...]


@dataclass(frozen=True, slots=True)
class _CatParam:
    id: str
    name: str
    reducible: bool
    lam: float
    median_rupees: float
    sigma: float
    card_prob: float
    max_rupees: float = 80_000.0


@dataclass(frozen=True, slots=True)
class _ScheduledEvent:
    day: date
    amount_paise: int
    kind: Literal["spend", "income", "goal_contribution", "refund"]
    account: str
    category_id: str | None
    payee_key: str
    payee_mode: Literal["upi", "card", "nach", "neft", "imps"]
    series_id: str | None
    goal_id: str | None = None


_BASE_CATEGORIES: tuple[_CatParam, ...] = (
    _CatParam("groceries", "Groceries", False, 0.62, 520.0, 0.52, 0.35),
    _CatParam("food", "Food & Dining", True, 0.52, 310.0, 0.50, 0.45),
    _CatParam("transport", "Transport", False, 0.42, 190.0, 0.52, 0.25),
    _CatParam("shopping", "Shopping", True, 0.16, 1050.0, 0.68, 0.72),
    _CatParam("fuel", "Fuel", False, 0.08, 1750.0, 0.28, 0.65),
    _CatParam("utilities", "Utilities", False, 0.03, 420.0, 0.45, 0.20),
    _CatParam("entertainment", "Entertainment", True, 0.12, 480.0, 0.58, 0.70),
    _CatParam("health", "Health & Pharmacy", False, 0.07, 620.0, 0.62, 0.35),
    _CatParam("family", "Family & Home", False, 0.06, 950.0, 0.50, 0.20),
)

_MERCHANTS: dict[str, tuple[tuple[str, str], ...]] = {
    "groceries": (
        ("bigbasket", "BIGBASKET"),
        ("blinkit", "BLINKIT"),
        ("dmart", "DMART READY"),
        ("zepto", "ZEPTO"),
        ("mother_dairy", "MOTHER DAIRY"),
    ),
    "food": (
        ("swiggy", "SWIGGY"),
        ("zomato", "ZOMATO"),
        ("third_wave", "THIRD WAVE COFFEE"),
        ("haldirams", "HALDIRAMS"),
        ("chai_point", "CHAI POINT"),
    ),
    "transport": (
        ("uber", "UBER INDIA"),
        ("ola", "OLA CABS"),
        ("metro_rail", "METRO RAIL"),
        ("rapido", "RAPIDO"),
        ("irctc", "IRCTC"),
    ),
    "shopping": (
        ("amazon_in", "AMAZON PAY IN"),
        ("flipkart", "FLIPKART"),
        ("myntra", "MYNTRA"),
        ("nykaa", "NYKAA"),
        ("decathlon", "DECATHLON"),
    ),
    "fuel": (
        ("indianoil", "INDIAN OIL"),
        ("hpcl", "HPCL FUEL"),
        ("bpcl", "BPCL PETROL"),
    ),
    "utilities": (
        ("airtel", "AIRTEL PREPAID"),
        ("jio", "JIO RECHARGE"),
        ("bescom", "BESCOM"),
    ),
    "entertainment": (
        ("bookmyshow", "BOOKMYSHOW"),
        ("pvr_inox", "PVR INOX"),
        ("steam", "STEAM GAMES"),
    ),
    "health": (
        ("apollo_pharmacy", "APOLLO PHARMACY"),
        ("pharmeasy", "PHARMEASY"),
        ("1mg", "TATA 1MG"),
    ),
    "family": (
        ("urban_company", "URBAN COMPANY"),
        ("local_kirana", "SHARMA TRADERS"),
        ("family_transfer", "FAMILY SUPPORT"),
    ),
}

_BANK_HANDLES = ("ybl", "okicici", "oksbi", "okhdfcbank", "ibl")
_BANK_CODES = ("YESB", "HDFC", "ICIC", "SBIN", "UTIB")

_PAYEE_LABELS: dict[str, str] = {
    "acme_salary": "ACME TECH PVT LTD",
    "retainer_client": "KESTREL DIGITAL LLP",
    "variable_client": "ORION ANALYTICS PVT LTD",
    "freelance_proj": "PIXELCRAFT STUDIO",
    "landlord_rent": "RAGHAVAN PROPERTIES",
    "car_emi": "HDFC BANK AUTO LOAN",
    "bike_emi": "BAJAJ AUTO FINANCE",
    "phone_emi": "BAJAJ FINSERV EMI",
    "goal_sip": "PPFAS MUTUAL FUND SIP",
    "airtel_fiber": "BHARTI AIRTEL",
    "jio_fiber": "RELIANCE JIOFIBER",
    "netflix": "NETFLIX ENTERTAINMENT",
    "spotify": "SPOTIFY INDIA",
    "cult_fit": "CUREFIT HEALTHCARE",
    "prime_video": "AMAZON PRIME",
    "youtube_prem": "GOOGLE YOUTUBE",
    "bescom_power": "BESCOM ELECTRICITY",
    "bwssb_water": "BWSSB WATER BOARD",
    "indane_gas": "INDANE GAS",
    "hdfc_life": "HDFC LIFE INSURANCE",
    "dps_school": "DELHI PUBLIC SCHOOL",
    "milk_tiffin": "COUNTRY DELIGHT",
    "society_maint": "PRESTIGE RWA MAINT",
}


def _month_last_day(year: int, month: int) -> int:
    return _calendar.monthrange(year, month)[1]


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    return total // 12, (total % 12) + 1


def _statement_due_date(close_date: date, card: Card) -> date:
    if card.due_day > card.statement_day:
        ty, tm = close_date.year, close_date.month
    else:
        ty, tm = _add_months(close_date.year, close_date.month, 1)
    return date(ty, tm, min(card.due_day, _month_last_day(ty, tm)))


def _close_dates(start: date, end: date, card: Card) -> list[date]:
    closes: list[date] = []
    y, m = start.year, start.month
    while True:
        cday = date(y, m, min(card.statement_day, _month_last_day(y, m)))
        if cday > end:
            break
        if cday >= start:
            closes.append(cday)
        y, m = _add_months(y, m, 1)
    return closes


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0.0:
        return 0
    limit = math.exp(-lam)
    k = 0
    p = 1.0
    while p > limit:
        k += 1
        p *= rng.random()
    return k - 1


def _lognormal_paise(
    rng: random.Random,
    median_rupees: float,
    sigma: float,
    max_rupees: float = 80_000.0,
) -> int:
    mu = math.log(max(median_rupees, 10.0))
    rupees = math.exp(rng.gauss(mu, sigma))
    rupees = min(max(rupees, 20.0), max_rupees)
    return max(100, int(round(rupees)) * 100)


def _format_payee(
    rng: random.Random,
    payee_key: str,
    label: str,
    mode: Literal["upi", "card", "nach", "neft", "imps"],
    kind: Literal["spend", "income", "goal_contribution", "refund", "card_payment"],
    day: date,
    card_last4: str = "4591",
) -> str:
    clean_label = label.upper()
    if kind == "card_payment":
        utr = rng.randint(100_000_000_000, 999_999_999_999)
        return f"UPI/DR/{utr}/{clean_label} BILL/HDFC/ccbill@hdfcbank"
    if kind == "refund":
        ref = rng.randint(100_000_000_000, 999_999_999_999)
        return f"UPI/CR/{ref}/{clean_label} REFUND/ICIC/refund@icici"
    if mode == "card":
        auth = rng.randint(10_000, 99_999)
        return f"POS {card_last4}XXXXXX{auth % 10000:04d} {clean_label}"
    if mode == "nach":
        mandate = rng.randint(10_000, 99_999)
        return f"NACH DR {clean_label} {mandate}"
    if mode == "neft":
        ref = rng.randint(100_000, 999_999)
        mon = _MONTH_ABBR[day.month]
        return f"NEFT CR {clean_label} {mon} {ref}"
    if mode == "imps":
        ref = rng.randint(1_000_000_000, 9_999_999_999)
        return f"IMPS CR {ref} {clean_label}"
    utr = rng.randint(100_000_000_000, 999_999_999_999)
    bank = rng.choice(_BANK_CODES)
    handle = rng.choice(_BANK_HANDLES)
    short = payee_key.replace("_", "")[:12]
    direction = "CR" if kind == "income" else "DR"
    return f"UPI/{direction}/{utr}/{clean_label}/{bank}/{short}@{handle}"


def _card_last4(card_id: str) -> str:
    digits = "".join(ch for ch in card_id if ch.isdigit())
    if len(digits) >= 4:
        return digits[-4:]
    mapping = {
        "hdfc_millennia": "4591",
        "hdfc_regalia": "8824",
        "icici_amazon": "6103",
        "hdfc_freedom": "3318",
        "icici_coral": "7742",
        "sbi_cashback": "9056",
    }
    return mapping.get(card_id, "4242")


def _build_categories(
    params: tuple[_CatParam, ...],
    stated_map: dict[str, int] | None = None,
) -> tuple[Category, ...]:
    return tuple(
        Category(
            id=p.id,
            name=p.name,
            reducible=p.reducible,
            stated_per_day_paise=stated_map[p.id] if stated_map is not None else None,
        )
        for p in params
    )


@lru_cache(maxsize=8)
def _cached_calendar(start: date, end: date) -> IndiaCalendar:
    return calendar_for(start, end)


def _expand(
    rule: str,
    starts: date,
    start: date,
    end: date,
    ends: date | None,
    holiday_adjust: HolidayAdjust,
    cal: IndiaCalendar,
) -> list[date]:
    return expand_rule(
        rule,
        starts,
        start=start,
        end=end,
        ends=ends,
        holiday_adjust=holiday_adjust,
        calendar=cal,
    )


def _window_breach_share(
    history_start: date,
    end: date,
    opening: int,
    floor: int,
    movements: tuple[Movement, ...],
) -> float:
    days_count = (end - history_start).days + 1
    daily_delta = [0] * days_count
    for m in movements:
        idx = (m.date - history_start).days
        if 0 <= idx < days_count:
            if m.kind in ("income", "refund") and m.account == "bank":
                daily_delta[idx] += m.amount_paise
            elif m.kind in ("spend", "goal_contribution") and m.account == "bank":
                daily_delta[idx] -= m.amount_paise
            elif m.kind == "card_payment":
                daily_delta[idx] -= m.amount_paise

    balances = [0] * days_count
    running = opening
    for i, delta in enumerate(daily_delta):
        running += delta
        balances[i] = running

    window = 90
    if days_count < window:
        return 0.0
    breaches = 0
    total_windows = days_count - window + 1
    for i in range(total_windows):
        if min(balances[i : i + window]) < floor:
            breaches += 1
    return breaches / total_windows


def _calibrate_opening_and_floor(
    history_start: date,
    end: date,
    movements: tuple[Movement, ...],
    base_opening: int,
    target_share: float,
    min_cushion: int = 2_200_000,
) -> tuple[int, int]:
    days_count = (end - history_start).days + 1
    daily_delta = [0] * days_count
    for m in movements:
        idx = (m.date - history_start).days
        if 0 <= idx < days_count:
            if m.kind in ("income", "refund") and m.account == "bank":
                daily_delta[idx] += m.amount_paise
            elif m.kind in ("spend", "goal_contribution") and m.account == "bank":
                daily_delta[idx] -= m.amount_paise
            elif m.kind == "card_payment":
                daily_delta[idx] -= m.amount_paise

    cum = [0] * days_count
    running = 0
    for i, delta in enumerate(daily_delta):
        running += delta
        cum[i] = running

    min_cum = min(0, min(cum))
    needed_opening = ((-min_cum + min_cushion + 499_999) // 500_000) * 500_000
    opening = max(base_opening, needed_opening)

    balances = [opening + c for c in cum]
    # The floor is set on the windows a backtest scores: weekly, over the last year, each
    # reaching 90 days ahead. Calibrating on the whole history would put every breach early.
    last = days_count - SCORED_DAYS - 1
    first = 14
    starts = [last - 7 * step for step in range(SCORED_ORIGINS) if last - 7 * step >= first]
    window_mins = sorted(min(balances[i : i + SCORED_DAYS + 1]) for i in starts)
    total_windows = len(window_mins)

    target_idx = min(max(int(round(target_share * (total_windows - 1))), 1), total_windows - 1)
    raw_floor = window_mins[target_idx] + 50_000
    candidate_base = max(1_000_000, ((raw_floor + 50_000) // 100_000) * 100_000)
    best_floor = candidate_base
    best_err = 999.0
    for offset in range(-15, 16):
        cand = max(1_000_000, candidate_base + offset * 100_000)
        breaches = sum(1 for wm in window_mins if wm < cand)
        share = breaches / total_windows
        if breaches == 0:
            continue
        err = abs(share - target_share)
        if err < best_err:
            best_err = err
            best_floor = cand
    if best_err > 0.12:
        fine_base = max(1_000_000, ((window_mins[target_idx] + 5_000) // 10_000) * 10_000)
        for offset in range(-30, 31):
            cand = max(1_000_000, fine_base + offset * 10_000)
            breaches = sum(1 for wm in window_mins if wm < cand)
            if breaches == 0:
                continue
            err = abs((breaches / total_windows) - target_share)
            if err < best_err:
                best_err = err
                best_floor = cand
    if sum(1 for wm in window_mins if wm < best_floor) == 0:
        best_floor = ((window_mins[0] // 100_000) + 2) * 100_000
    return opening, best_floor


def _simulate_ledger(
    *,
    rng: random.Random,
    history_start: date,
    end: date,
    cards: tuple[Card, ...],
    scheduled_events: list[_ScheduledEvent],
    cat_params: tuple[_CatParam, ...],
    persona_key: str,
    cal: IndiaCalendar,
    salary_dates: set[date],
    diwali_windows: tuple[tuple[date, date], ...],
) -> tuple[Movement, ...]:
    events_by_day: dict[date, list[_ScheduledEvent]] = {}
    for ev in scheduled_events:
        if history_start <= ev.day <= end:
            events_by_day.setdefault(ev.day, []).append(ev)

    card_by_id = {c.id: c for c in cards}
    close_dates_by_card: dict[str, set[date]] = {
        c.id: set(_close_dates(history_start, end, c)) for c in cards
    }
    cycle_net: dict[str, int] = {c.id: 0 for c in cards}
    used_credit: dict[str, int] = {c.id: 0 for c in cards}
    pending_payments: dict[date, list[tuple[Card, int]]] = {}

    movements: list[Movement] = []
    seq_counter = 0

    def emit(
        day: date,
        amount_paise: int,
        kind: Literal["spend", "refund", "income", "card_payment", "goal_contribution"],
        account: str,
        category_id: str | None,
        payee: str,
        payee_key: str,
        series_id: str | None,
        goal_id: str | None = None,
    ) -> None:
        nonlocal seq_counter
        if amount_paise <= 0:
            return
        seq_counter += 1
        movements.append(
            Movement(
                id=f"m_{day.strftime('%Y%m%d')}_{seq_counter:05d}",
                date=day,
                amount_paise=amount_paise,
                kind=kind,
                account=account,
                category_id=category_id,
                payee=payee,
                payee_key=payee_key,
                series_id=series_id,
                goal_id=goal_id,
            )
        )

    # Hidden Markov state for family_runs: 0=normal, 1=outing, 2=trip_festival
    markov_state = 0
    last_salary_date: date | None = None
    card_ids = tuple(c.id for c in cards)

    # Pre-plan rare_large shock days (every 95-145 days).
    rare_shocks: dict[date, tuple[str, int, bool]] = {}
    if persona_key == "rare_large":
        shock_day = history_start + timedelta(days=rng.randint(55, 95))
        shock_kinds = (
            ("health", "apollo_hospital", "APOLLO HOSPITALS", True),
            ("shopping", "croma_electronics", "CROMA ELECTRONICS", True),
            ("entertainment", "makemytrip", "MAKEMYTRIP HOLIDAYS", False),
            ("family", "home_repair", "LIVSPACE HOME REPAIR", False),
        )
        idx_s = 0
        while shock_day <= end - timedelta(days=5):
            cat_id, pkey, label, prefer_card = shock_kinds[idx_s % len(shock_kinds)]
            rupees = 20_000.0 * rng.paretovariate(1.55)
            rupees = min(max(rupees, 20_000.0), 200_000.0)
            amt_paise = int(round(rupees / 100.0)) * 10_000
            rare_shocks[shock_day] = (f"{cat_id}|{pkey}|{label}", amt_paise, prefer_card)
            idx_s += 1
            shock_day = shock_day + timedelta(days=rng.randint(95, 145))

    # Pre-plan heavy_tails occasional spike days (₹15k-₹60k) in addition to sigma=1.35.
    heavy_spike_days: dict[date, tuple[str, int]] = {}
    if persona_key == "heavy_tails":
        spike_day = history_start + timedelta(days=rng.randint(30, 65))
        while spike_day <= end - timedelta(days=3):
            if rng.random() < 0.7:
                cat = "shopping"
                pkey, label = rng.choice(_MERCHANTS["shopping"])
            else:
                cat = "food"
                pkey, label = ("taj_dining", "TAJ HOTELS DINING")
            amt_paise = rng.randint(150, 580) * 10_000
            heavy_spike_days[spike_day] = (f"{cat}|{pkey}|{label}", amt_paise)
            spike_day = spike_day + timedelta(days=rng.randint(45, 90))

    total_days = (end - history_start).days + 1
    for offset in range(total_days):
        day = history_start + timedelta(days=offset)
        if day in salary_dates:
            last_salary_date = day

        # 1. Process card bill payments scheduled for today first so limit frees up.
        for card, bill_amt in pending_payments.pop(day, []):
            pay_text = _format_payee(rng, f"cc_{card.id}", card.name, "upi", "card_payment", day)
            emit(
                day=day,
                amount_paise=bill_amt,
                kind="card_payment",
                account=card.id,
                category_id=None,
                payee=pay_text,
                payee_key=f"cc_bill_{card.id}",
                series_id=None,
            )
            used_credit[card.id] -= bill_amt

        # 2. Process scheduled income / obligations / EMIs / series / goal contributions.
        for ev in events_by_day.get(day, []):
            if ev.kind == "income":
                text = _format_payee(
                    rng,
                    ev.payee_key,
                    _PAYEE_LABELS.get(ev.payee_key, ev.payee_key),
                    ev.payee_mode,
                    "income",
                    day,
                )
                emit(day, ev.amount_paise, "income", "bank", None, text, ev.payee_key, ev.series_id)
            elif ev.kind == "goal_contribution":
                text = _format_payee(
                    rng,
                    ev.payee_key,
                    _PAYEE_LABELS.get(ev.payee_key, ev.payee_key),
                    ev.payee_mode,
                    "goal_contribution",
                    day,
                )
                emit(
                    day,
                    ev.amount_paise,
                    "goal_contribution",
                    "bank",
                    None,
                    text,
                    ev.payee_key,
                    None,
                    goal_id=ev.goal_id,
                )
            elif ev.kind == "spend":
                acc = ev.account
                mode = ev.payee_mode
                if acc != "bank":
                    card = card_by_id[acc]
                    if used_credit[acc] + ev.amount_paise <= card.credit_limit_paise:
                        used_credit[acc] += ev.amount_paise
                        cycle_net[acc] += ev.amount_paise
                        mode = "card"
                    else:
                        acc = "bank"
                        mode = "upi"
                text = _format_payee(
                    rng,
                    ev.payee_key,
                    _PAYEE_LABELS.get(ev.payee_key, ev.payee_key),
                    mode,
                    "spend",
                    day,
                    card_last4=_card_last4(acc) if acc != "bank" else "0000",
                )
                emit(
                    day,
                    ev.amount_paise,
                    "spend",
                    acc,
                    ev.category_id,
                    text,
                    ev.payee_key,
                    ev.series_id,
                )

        # 3. Rare large shocks (rare_large) or heavy-tail bursts (heavy_tails).
        if day in rare_shocks:
            meta, amt_paise, prefer_card = rare_shocks[day]
            cat_id, pkey, label = meta.split("|")
            acc = "bank"
            mode: Literal["upi", "card", "nach", "neft", "imps"] = "upi"
            if prefer_card and len(card_ids) > 0:
                chosen = card_ids[0]
                if used_credit[chosen] + amt_paise <= card_by_id[chosen].credit_limit_paise:
                    acc = chosen
                    mode = "card"
                    used_credit[chosen] += amt_paise
                    cycle_net[chosen] += amt_paise
            text = _format_payee(
                rng,
                pkey,
                label,
                mode,
                "spend",
                day,
                card_last4=_card_last4(acc) if acc != "bank" else "0000",
            )
            emit(day, amt_paise, "spend", acc, cat_id, text, pkey, None)

        if day in heavy_spike_days:
            meta, amt_paise = heavy_spike_days[day]
            cat_id, pkey, label = meta.split("|")
            acc = "bank"
            mode = "upi"
            if len(card_ids) > 0:
                chosen = card_ids[0]
                if used_credit[chosen] + amt_paise <= card_by_id[chosen].credit_limit_paise:
                    acc = chosen
                    mode = "card"
                    used_credit[chosen] += amt_paise
                    cycle_net[chosen] += amt_paise
            text = _format_payee(
                rng,
                pkey,
                label,
                mode,
                "spend",
                day,
                card_last4=_card_last4(acc) if acc != "bank" else "0000",
            )
            emit(day, amt_paise, "spend", acc, cat_id, text, pkey, None)

        # 4. Update hidden regime for family_runs.
        if persona_key == "family_runs":
            u = rng.random()
            if markov_state == 0:
                if u < 0.055:
                    markov_state = 1
                elif u < 0.073:
                    markov_state = 2
            elif markov_state == 1:
                if u >= 0.55:
                    markov_state = 0
            else:
                if u >= 0.72:
                    markov_state = 0

        # Compute daily multipliers for drift / regime / payday / Diwali.
        m_idx = (day.year - history_start.year) * 12 + (day.month - history_start.month)
        drift_mult = (1.015**m_idx) if persona_key == "lifestyle_drift" else 1.0
        payday_mult = 1.0
        if (
            persona_key == "family_runs"
            and last_salary_date is not None
            and 0 <= (day - last_salary_date).days <= 5
        ):
            payday_mult = 1.65

        diwali_active = any(w_start <= day <= w_end for w_start, w_end in diwali_windows)

        # 5. Daily discretionary spending per category.
        for cp in cat_params:
            lam = cp.lam
            med = cp.median_rupees * drift_mult
            if persona_key == "family_runs":
                if markov_state == 1 and cp.id in (
                    "food",
                    "transport",
                    "entertainment",
                    "shopping",
                ):
                    lam *= 2.4
                    med *= 1.85
                elif markov_state == 2 and cp.id in (
                    "food",
                    "transport",
                    "entertainment",
                    "shopping",
                    "groceries",
                    "family",
                ):
                    lam *= 3.0
                    med *= 2.25
                if payday_mult > 1.0 and cp.id in ("groceries", "shopping", "food", "family"):
                    lam *= 1.35
                    med *= 1.35
            if (
                persona_key == "seasonal_bills"
                and diwali_active
                and cp.id in ("shopping", "food", "family", "entertainment")
            ):
                lam *= 2.3
                med *= 1.65

            lift = WEEKEND_LIFT.get(cp.id) if day.weekday() >= 5 else None
            if lift is not None:
                lam *= lift[0]
                med *= lift[1]
            count = _poisson(rng, lam)
            for _ in range(count):
                amt_paise = _lognormal_paise(rng, med, cp.sigma, cp.max_rupees)
                pkey, label = rng.choice(_MERCHANTS[cp.id])
                use_card = len(card_ids) > 0 and rng.random() < cp.card_prob
                acc = "bank"
                mode = "upi"
                if use_card:
                    chosen = card_ids[0] if len(card_ids) == 1 else rng.choice(card_ids)
                    if used_credit[chosen] + amt_paise <= card_by_id[chosen].credit_limit_paise:
                        acc = chosen
                        mode = "card"
                        used_credit[chosen] += amt_paise
                        cycle_net[chosen] += amt_paise
                text = _format_payee(
                    rng,
                    pkey,
                    label,
                    mode,
                    "spend",
                    day,
                    card_last4=_card_last4(acc) if acc != "bank" else "0000",
                )
                emit(day, amt_paise, "spend", acc, cp.id, text, pkey, None)

        # Occasional small UPI refund (~1.2% of days).
        if rng.random() < 0.012:
            rcat = rng.choice(("shopping", "food", "groceries"))
            pkey, label = rng.choice(_MERCHANTS[rcat])
            ramt = rng.randint(150, 950) * 100
            rtext = _format_payee(rng, pkey, label, "upi", "refund", day)
            emit(day, ramt, "refund", "bank", rcat, rtext, pkey, None)

        # 6. End-of-day statement closes.
        for card in cards:
            if day in close_dates_by_card[card.id]:
                bill = cycle_net[card.id]
                cycle_net[card.id] = 0
                if bill > 0:
                    due = _statement_due_date(day, card)
                    pay_day = due
                    if persona_key == "tight_card" and due <= end and rng.random() < 0.45:
                        early = rng.randint(1, 3)
                        cand = due - timedelta(days=early)
                        if cand > day:
                            pay_day = cand
                    if pay_day <= end:
                        pending_payments.setdefault(pay_day, []).append((card, bill))

        _ = cal  # calendar already used by callers; kept explicit for clarity

    movements.sort(key=lambda m: (m.date, m.id))
    return tuple(movements)


def _add_confirmed_series(
    *,
    sched: Schedule,
    series_id: str,
    payee_key: str,
    cadence: str,
    payee_mode: Literal["upi", "card", "nach", "neft", "imps"],
    cal: IndiaCalendar,
    history_start: date,
    end: date,
    scheduled_events: list[_ScheduledEvent],
    series_list: list[Series],
    salary_dates: set[date] | None = None,
) -> None:
    adj: HolidayAdjust = (
        sched.holiday_adjust
        if sched.holiday_adjust in ("none", "next", "previous")
        else "none"  # pragma: no cover
    )
    dates = _expand(sched.rule, sched.starts, history_start, end, sched.ends, adj, cal)
    if sched.kind == "income_fixed":
        skind: Literal["spend", "income"] = "income"
        ekind: Literal["spend", "income", "goal_contribution", "refund"] = "income"
        acc = "bank"
        if salary_dates is not None:
            salary_dates.update(dates)
    elif sched.kind == "goal_contribution":
        for d in dates:
            scheduled_events.append(
                _ScheduledEvent(
                    day=d,
                    amount_paise=sched.amount_paise,
                    kind="goal_contribution",
                    account="bank",
                    category_id=None,
                    payee_key=payee_key,
                    payee_mode=payee_mode,
                    series_id=None,
                    goal_id=sched.goal_id,
                )
            )
        return
    else:
        skind = "spend"
        ekind = "spend"
        acc = sched.card_id if sched.card_id is not None else "bank"

    for d in dates:
        scheduled_events.append(
            _ScheduledEvent(
                day=d,
                amount_paise=sched.amount_paise,
                kind=ekind,
                account=acc,
                category_id=sched.category_id,
                payee_key=payee_key,
                payee_mode=payee_mode,
                series_id=series_id,
            )
        )
    series_list.append(
        Series(
            id=series_id,
            payee_key=payee_key,
            kind=skind,
            cadence=cadence,
            schedule_id=sched.id if sched.confirmed else None,
            start=sched.starts,
            end=sched.ends,
        )
    )


def _add_unscheduled_monthly(
    *,
    rng: random.Random,
    series_id: str,
    payee_key: str,
    cadence: str,
    rule: str,
    starts: date,
    ends: date | None,
    base_paise: int,
    jitter_frac: float,
    account: str,
    category_id: str,
    payee_mode: Literal["upi", "card", "nach", "neft", "imps"],
    holiday_adjust: HolidayAdjust,
    cal: IndiaCalendar,
    history_start: date,
    end: date,
    scheduled_events: list[_ScheduledEvent],
    series_list: list[Series],
) -> None:
    dates = _expand(rule, starts, history_start, end, ends, holiday_adjust, cal)
    for d in dates:
        amt = base_paise
        if jitter_frac > 0.0:
            amt = max(100, int(round(base_paise * (1.0 + rng.uniform(-jitter_frac, jitter_frac)))))
        scheduled_events.append(
            _ScheduledEvent(
                day=d,
                amount_paise=amt,
                kind="spend",
                account=account,
                category_id=category_id,
                payee_key=payee_key,
                payee_mode=payee_mode,
                series_id=series_id,
            )
        )
    series_list.append(
        Series(
            id=series_id,
            payee_key=payee_key,
            kind="spend",
            cadence=cadence,
            schedule_id=None,
            start=starts,
            end=ends,
        )
    )


def _persona_cat_params(key: str) -> tuple[_CatParam, ...]:
    if key == "heavy_tails":
        out: list[_CatParam] = []
        for cp in _BASE_CATEGORIES:
            if cp.id == "shopping":
                out.append(
                    _CatParam(cp.id, cp.name, cp.reducible, 0.20, 1100.0, 1.35, 0.75, 70_000.0)
                )
            elif cp.id == "food":
                out.append(
                    _CatParam(cp.id, cp.name, cp.reducible, 0.55, 340.0, 1.25, 0.50, 45_000.0)
                )
            else:
                out.append(cp)
        return tuple(out)
    if key == "tight_card":
        return tuple(
            _CatParam(
                cp.id,
                cp.name,
                cp.reducible,
                cp.lam * 1.15,
                cp.median_rupees * 1.05,
                cp.sigma,
                0.88,
                cp.max_rupees,
            )
            for cp in _BASE_CATEGORIES
        )
    if key == "freelancer":
        return tuple(
            _CatParam(
                cp.id,
                cp.name,
                cp.reducible,
                cp.lam * 0.85,
                cp.median_rupees * 0.88,
                cp.sigma,
                cp.card_prob,
                cp.max_rupees,
            )
            for cp in _BASE_CATEGORIES
        )
    return _BASE_CATEGORIES


def _expected_daily_paise(cp: _CatParam) -> int:
    mean_rupees = cp.median_rupees * math.exp(0.5 * cp.sigma * cp.sigma)
    return max(100, int(round(cp.lam * mean_rupees)) * 100)


def generate(key: str, seed: int) -> Persona:
    """Generate a deterministic synthetic household Persona for `(key, seed)`."""
    if key not in PERSONA_KEYS:
        raise ValueError(f"Unknown persona key {key!r}")

    rng = random.Random(f"{key}:{seed}")
    history_start = date(2026, 3, 1) if key == "new_user" else HISTORY_START
    end = END
    cal = _cached_calendar(history_start - timedelta(days=14), end + timedelta(days=14))

    cat_params = _persona_cat_params(key)
    stated_map: dict[str, int] | None = None
    if key == "new_user":
        stated_map = {}
        for idx_c, cp in enumerate(cat_params):
            expected = _expected_daily_paise(cp)
            mult = 1.30 if idx_c % 2 == 0 else 0.70
            stated_map[cp.id] = max(100, int(round(expected * mult)))
    categories = _build_categories(cat_params, stated_map)

    # Cards
    if key == "salaried_steady":
        cards = (Card("hdfc_millennia", "HDFC Millennia", 15, 5, 20_000_000),)
    elif key == "heavy_tails":
        cards = (
            Card("hdfc_regalia", "HDFC Regalia", 20, 10, 35_000_000),
            Card("icici_amazon", "ICICI Amazon Pay", 12, 2, 20_000_000),
        )
    elif key == "family_runs":
        cards = (
            Card("hdfc_regalia", "HDFC Regalia", 18, 8, 25_000_000),
            Card("sbi_cashback", "SBI Cashback", 5, 25, 15_000_000),
        )
    elif key == "rare_large":
        cards = (Card("hdfc_regalia", "HDFC Regalia", 15, 5, 30_000_000),)
    elif key == "tight_card":
        cards = (
            Card("hdfc_freedom", "HDFC Freedom", 15, 5, 2_500_000),
            Card("icici_coral", "ICICI Coral", 22, 12, 1_800_000),
        )
    elif key == "new_user":
        cards = (Card("icici_amazon", "ICICI Amazon Pay", 15, 5, 15_000_000),)
    else:
        cards = (
            Card("hdfc_millennia", "HDFC Millennia", 15, 5, 18_000_000),
            Card("icici_amazon", "ICICI Amazon Pay", 5, 25, 12_000_000),
        )

    primary_card_id = cards[0].id
    secondary_card_id = cards[-1].id

    # Goals
    goal_id = "goal_emergency"
    goal_map = {
        "salaried_steady": ("Emergency Fund", 60_000_000, date(2027, 6, 1), 1, 3_600_000),
        "heavy_tails": ("Goa & Europe Travel", 50_000_000, date(2027, 4, 1), 2, 4_500_000),
        "family_runs": ("Child Education Corpus", 80_000_000, date(2028, 3, 1), 1, 4_600_000),
        "lifestyle_drift": ("Home Renovation", 45_000_000, date(2027, 8, 1), 1, 3_800_000),
        "rare_large": ("Medical Reserve", 50_000_000, date(2027, 12, 1), 1, 4_600_000),
        "freelancer": ("Tax & Runway Buffer", 35_000_000, date(2027, 3, 31), 1, 5_200_000),
        "tight_card": ("Debt-Free Buffer", 20_000_000, date(2027, 6, 1), 1, 800_000),
        "new_user": ("Emergency Fund", 30_000_000, date(2027, 9, 1), 1, 4_400_000),
        "messy_recurring": ("Emergency Fund", 50_000_000, date(2027, 6, 1), 1, 4_200_000),
        "seasonal_bills": ("Solar Inverter & Home", 40_000_000, date(2027, 5, 1), 1, 3_900_000),
    }
    g_name, g_target, g_deadline, g_prio, sip_paise = goal_map[key]
    goals = (Goal(goal_id, g_name, g_target, g_deadline, g_prio, history_start),)

    # Income / Rent / EMI parameters per persona
    salary_map = {
        "salaried_steady": 12_500_000,
        "heavy_tails": 16_500_000,
        "family_runs": 17_500_000,
        "lifestyle_drift": 13_500_000,
        "rare_large": 15_500_000,
        "freelancer": 3_500_000,
        "tight_card": 9_500_000,
        "new_user": 13_000_000,
        "messy_recurring": 13_500_000,
        "seasonal_bills": 13_500_000,
    }
    rent_map = {
        "salaried_steady": 2_800_000,
        "heavy_tails": 3_400_000,
        "family_runs": 3_200_000,
        "lifestyle_drift": 2_600_000,
        "rare_large": 3_000_000,
        "freelancer": 2_200_000,
        "tight_card": 2_200_000,
        "new_user": 2_800_000,
        "messy_recurring": 2_700_000,
        "seasonal_bills": 2_800_000,
    }
    emi_map = {
        "salaried_steady": 1_450_000,
        "heavy_tails": 1_800_000,
        "family_runs": 1_600_000,
        "lifestyle_drift": 1_500_000,
        "rare_large": 1_500_000,
        "freelancer": 900_000,
        "tight_card": 1_200_000,
        "new_user": 1_200_000,
        "messy_recurring": 1_400_000,
        "seasonal_bills": 1_400_000,
    }

    schedules_list: list[Schedule] = []
    series_list: list[Series] = []
    scheduled_events: list[_ScheduledEvent] = []
    salary_dates: set[date] = set()

    # 1. Salary / Retainer
    if key == "freelancer":
        retainer_sched = Schedule(
            id="sched_retainer",
            name="Monthly Retainer",
            kind="income_fixed",
            rule="FREQ=MONTHLY;BYMONTHDAY=5",
            starts=history_start,
            ends=None,
            amount_paise=salary_map[key],
            holiday_adjust="next",
            card_id=None,
            category_id=None,
            goal_id=None,
            confirmed=True,
            created=history_start,
        )
        schedules_list.append(retainer_sched)
        _add_confirmed_series(
            sched=retainer_sched,
            series_id="ser_retainer",
            payee_key="retainer_client",
            cadence="monthly on the 5th, next working day",
            payee_mode="neft",
            cal=cal,
            history_start=history_start,
            end=end,
            scheduled_events=scheduled_events,
            series_list=series_list,
            salary_dates=salary_dates,
        )
        # Unconfirmed variable client schedule
        var_sched = Schedule(
            id="sched_var_client",
            name="Orion Analytics Retainer Guess",
            kind="income_variable",
            rule="FREQ=MONTHLY;BYMONTHDAY=20",
            starts=history_start,
            ends=None,
            amount_paise=4_500_000,
            holiday_adjust="none",
            card_id=None,
            category_id=None,
            goal_id=None,
            confirmed=False,
            created=history_start + timedelta(days=30),
        )
        schedules_list.append(var_sched)
        # Actual irregular payouts from Orion Analytics (sometimes delayed, skipped, or lumped)
        var_dates = _expand(
            "FREQ=MONTHLY;BYMONTHDAY=20",
            history_start,
            history_start,
            end,
            None,
            "none",
            cal,
        )
        carryover = 0
        for vd in var_dates:
            if rng.random() < 0.14:
                carryover += rng.randint(320, 480) * 10_000
                continue
            shift = rng.randint(-3, 16)
            actual_d = vd + timedelta(days=shift)
            if history_start <= actual_d <= end:
                amt = rng.randint(250, 720) * 10_000 + carryover
                carryover = 0
                scheduled_events.append(
                    _ScheduledEvent(
                        day=actual_d,
                        amount_paise=amt,
                        kind="income",
                        account="bank",
                        category_id=None,
                        payee_key="variable_client",
                        payee_mode="neft",
                        series_id="ser_variable_client",
                    )
                )
        series_list.append(
            Series(
                id="ser_variable_client",
                payee_key="variable_client",
                kind="income",
                cadence="irregular monthly milestones",
                schedule_id=None,
                start=history_start,
                end=None,
            )
        )
        # Completely unscheduled one-off freelance projects
        proj_day = history_start + timedelta(days=rng.randint(15, 40))
        while proj_day <= end:
            amt = rng.randint(200, 760) * 10_000
            scheduled_events.append(
                _ScheduledEvent(
                    day=proj_day,
                    amount_paise=amt,
                    kind="income",
                    account="bank",
                    category_id=None,
                    payee_key="freelance_proj",
                    payee_mode="imps",
                    series_id=None,
                )
            )
            proj_day = proj_day + timedelta(days=rng.randint(24, 54))
    else:
        sal_sched = Schedule(
            id="sched_salary",
            name="Monthly Salary",
            kind="income_fixed",
            rule="FREQ=MONTHLY;BYMONTHDAY=-1",
            starts=history_start,
            ends=None,
            amount_paise=salary_map[key],
            holiday_adjust="previous",
            card_id=None,
            category_id=None,
            goal_id=None,
            confirmed=True,
            created=history_start,
        )
        schedules_list.append(sal_sched)
        if key == "messy_recurring":
            rule_dates = _expand(
                sal_sched.rule,
                sal_sched.starts,
                history_start,
                end,
                None,
                "previous",
                cal,
            )
            for idx_s, rd in enumerate(rule_dates):
                actual_d = rd
                amt = sal_sched.amount_paise
                if rd.month == 10:
                    # Paid early before Diwali
                    actual_d = rd - timedelta(days=rng.randint(2, 4))
                elif idx_s % 4 == 1 or rng.random() < 0.25:
                    # 1-3 days late
                    actual_d = rd + timedelta(days=rng.randint(1, 3))
                if rd.year == 2025 and rd.month == 4:
                    # Annual appraisal bonus month
                    amt = int(round(sal_sched.amount_paise * 1.80))
                if history_start <= actual_d <= end:
                    salary_dates.add(actual_d)
                    scheduled_events.append(
                        _ScheduledEvent(
                            day=actual_d,
                            amount_paise=amt,
                            kind="income",
                            account="bank",
                            category_id=None,
                            payee_key="acme_salary",
                            payee_mode="neft",
                            series_id="ser_salary",
                        )
                    )
            series_list.append(
                Series(
                    id="ser_salary",
                    payee_key="acme_salary",
                    kind="income",
                    cadence="last working day (irregular timing + bonus)",
                    schedule_id=sal_sched.id,
                    start=history_start,
                    end=None,
                )
            )
        else:
            _add_confirmed_series(
                sched=sal_sched,
                series_id="ser_salary",
                payee_key="acme_salary",
                cadence="monthly on the last working day",
                payee_mode="neft",
                cal=cal,
                history_start=history_start,
                end=end,
                scheduled_events=scheduled_events,
                series_list=series_list,
                salary_dates=salary_dates,
            )

    # 2. Rent (stale schedule in lifestyle_drift!)
    rent_sched = Schedule(
        id="sched_rent",
        name="House Rent",
        kind="obligation",
        rule="FREQ=MONTHLY;BYMONTHDAY=3",
        starts=history_start,
        ends=None,
        amount_paise=rent_map[key],
        holiday_adjust="next",
        card_id=None,
        category_id="family",
        goal_id=None,
        confirmed=True,
        created=history_start,
    )
    schedules_list.append(rent_sched)
    if key == "lifestyle_drift":
        rent_dates = _expand(
            rent_sched.rule, rent_sched.starts, history_start, end, None, "next", cal
        )
        for rd in rent_dates:
            years_elapsed = (rd.year - history_start.year) * 12 + (rd.month - history_start.month)
            hikes = years_elapsed // 12
            rent_amt = int(round(rent_sched.amount_paise * (1.08**hikes)))
            scheduled_events.append(
                _ScheduledEvent(
                    day=rd,
                    amount_paise=rent_amt,
                    kind="spend",
                    account="bank",
                    category_id="family",
                    payee_key="landlord_rent",
                    payee_mode="upi",
                    series_id="ser_rent",
                )
            )
        series_list.append(
            Series(
                id="ser_rent",
                payee_key="landlord_rent",
                kind="spend",
                cadence="monthly on the 3rd, +8% annual escalation",
                schedule_id=rent_sched.id,
                start=history_start,
                end=None,
            )
        )
    else:
        _add_confirmed_series(
            sched=rent_sched,
            series_id="ser_rent",
            payee_key="landlord_rent",
            cadence="monthly on the 3rd, next working day",
            payee_mode="upi",
            cal=cal,
            history_start=history_start,
            end=end,
            scheduled_events=scheduled_events,
            series_list=series_list,
        )

    # 3. EMI (instalment)
    emi_sched = Schedule(
        id="sched_emi",
        name="Vehicle Loan EMI",
        kind="instalment",
        rule="FREQ=MONTHLY;BYMONTHDAY=7",
        starts=history_start,
        ends=None,
        amount_paise=emi_map[key],
        holiday_adjust="next",
        card_id=None,
        category_id="transport",
        goal_id=None,
        confirmed=True,
        created=history_start,
    )
    schedules_list.append(emi_sched)
    _add_confirmed_series(
        sched=emi_sched,
        series_id="ser_emi",
        payee_key="car_emi",
        cadence="monthly on the 7th, next working day",
        payee_mode="nach",
        cal=cal,
        history_start=history_start,
        end=end,
        scheduled_events=scheduled_events,
        series_list=series_list,
    )

    if key in ("salaried_steady", "tight_card"):
        phone_emi = Schedule(
            id="sched_phone_emi",
            name="Phone No-Cost EMI",
            kind="instalment",
            rule="FREQ=MONTHLY;BYMONTHDAY=14",
            starts=history_start,
            ends=date(2025, 5, 31),
            amount_paise=420_000,
            holiday_adjust="none",
            card_id=primary_card_id,
            category_id="shopping",
            goal_id=None,
            confirmed=True,
            created=history_start,
        )
        schedules_list.append(phone_emi)
        _add_confirmed_series(
            sched=phone_emi,
            series_id="ser_phone_emi",
            payee_key="phone_emi",
            cadence="monthly on the 14th until May 2025",
            payee_mode="card",
            cal=cal,
            history_start=history_start,
            end=end,
            scheduled_events=scheduled_events,
            series_list=series_list,
        )

    # 4. Goal contribution schedule
    goal_sched = Schedule(
        id="sched_goal",
        name=f"{g_name} SIP",
        kind="goal_contribution",
        rule="FREQ=MONTHLY;BYMONTHDAY=10",
        starts=history_start,
        ends=None,
        amount_paise=sip_paise,
        holiday_adjust="next",
        card_id=None,
        category_id=None,
        goal_id=goal_id,
        confirmed=True,
        created=history_start,
    )
    schedules_list.append(goal_sched)
    _add_confirmed_series(
        sched=goal_sched,
        series_id="ser_goal",
        payee_key="goal_sip",
        cadence="monthly on the 10th",
        payee_mode="nach",
        cal=cal,
        history_start=history_start,
        end=end,
        scheduled_events=scheduled_events,
        series_list=series_list,
    )

    # 5. Persona-specific confirmed obligations & unentered recurring series
    if key == "family_runs":
        school_sched = Schedule(
            id="sched_school",
            name="School Fees",
            kind="obligation",
            rule="FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY=10",
            starts=date(2024, 7, 1),
            ends=None,
            amount_paise=3_800_000,
            holiday_adjust="next",
            card_id=None,
            category_id="family",
            goal_id=None,
            confirmed=True,
            created=history_start,
        )
        schedules_list.append(school_sched)
        _add_confirmed_series(
            sched=school_sched,
            series_id="ser_school",
            payee_key="dps_school",
            cadence="quarterly on the 10th (Jan/Apr/Jul/Oct)",
            payee_mode="upi",
            cal=cal,
            history_start=history_start,
            end=end,
            scheduled_events=scheduled_events,
            series_list=series_list,
        )

    # Common unentered recurring charges (schedule_id=None)
    _add_unscheduled_monthly(
        rng=rng,
        series_id="ser_broadband",
        payee_key="airtel_fiber",
        cadence="monthly on the 12th",
        rule="FREQ=MONTHLY;BYMONTHDAY=12",
        starts=history_start,
        ends=None,
        base_paise=99_900,
        jitter_frac=0.0,
        account=secondary_card_id if key == "tight_card" else "bank",
        category_id="utilities",
        payee_mode="card" if key == "tight_card" else "upi",
        holiday_adjust="none",
        cal=cal,
        history_start=history_start,
        end=end,
        scheduled_events=scheduled_events,
        series_list=series_list,
    )
    _add_unscheduled_monthly(
        rng=rng,
        series_id="ser_ott_music",
        payee_key="spotify",
        cadence="monthly on the 19th",
        rule="FREQ=MONTHLY;BYMONTHDAY=19",
        starts=history_start,
        ends=None,
        base_paise=17_900,
        jitter_frac=0.0,
        account=primary_card_id,
        category_id="entertainment",
        payee_mode="card",
        holiday_adjust="none",
        cal=cal,
        history_start=history_start,
        end=end,
        scheduled_events=scheduled_events,
        series_list=series_list,
    )

    if key == "messy_recurring":
        # (a) Price change: Netflix ₹649 -> ₹799 starting Jul 2025
        nf_dates = _expand(
            "FREQ=MONTHLY;BYMONTHDAY=18", history_start, history_start, end, None, "none", cal
        )
        for nd in nf_dates:
            nf_amt = 64_900 if nd < date(2025, 7, 1) else 79_900
            scheduled_events.append(
                _ScheduledEvent(
                    nd,
                    nf_amt,
                    "spend",
                    primary_card_id,
                    "entertainment",
                    "netflix",
                    "card",
                    "ser_nf",
                )
            )
        series_list.append(
            Series(
                "ser_nf",
                "netflix",
                "spend",
                "monthly on the 18th (price hike)",
                None,
                history_start,
                None,
            )
        )

        # (b) Skipped month: Cult.fit skips April 2025
        gym_dates = _expand(
            "FREQ=MONTHLY;BYMONTHDAY=2", history_start, history_start, end, None, "none", cal
        )
        for gd in gym_dates:
            if gd.year == 2025 and gd.month == 4:
                continue
            scheduled_events.append(
                _ScheduledEvent(
                    gd, 159_900, "spend", "bank", "health", "cult_fit", "upi", "ser_gym"
                )
            )
        series_list.append(
            Series(
                "ser_gym",
                "cult_fit",
                "spend",
                "monthly on the 2nd (1 skipped month)",
                None,
                history_start,
                None,
            )
        )

        # (c) Double charge: JioFiber charged twice in Sep 2025
        jio_dates = _expand(
            "FREQ=MONTHLY;BYMONTHDAY=8", history_start, history_start, end, None, "none", cal
        )
        for jd in jio_dates:
            scheduled_events.append(
                _ScheduledEvent(
                    jd, 117_900, "spend", "bank", "utilities", "jio_fiber", "upi", "ser_jio"
                )
            )
            if jd.year == 2025 and jd.month == 9:
                scheduled_events.append(
                    _ScheduledEvent(
                        jd + timedelta(days=2),
                        117_900,
                        "spend",
                        "bank",
                        "utilities",
                        "jio_fiber",
                        "upi",
                        "ser_jio",
                    )
                )
        series_list.append(
            Series(
                "ser_jio",
                "jio_fiber",
                "spend",
                "monthly on the 8th (double charge)",
                None,
                history_start,
                None,
            )
        )

        # (d) Cancellation mid-history: Prime Video ends Oct 2025
        prime_end = date(2025, 10, 15)
        prime_dates = _expand(
            "FREQ=MONTHLY;BYMONTHDAY=11", history_start, history_start, end, prime_end, "none", cal
        )
        for pd in prime_dates:
            scheduled_events.append(
                _ScheduledEvent(
                    pd,
                    29_900,
                    "spend",
                    secondary_card_id,
                    "entertainment",
                    "prime_video",
                    "card",
                    "ser_prime",
                )
            )
        series_list.append(
            Series(
                "ser_prime",
                "prime_video",
                "spend",
                "monthly on the 11th (cancelled)",
                None,
                history_start,
                prime_end,
            )
        )

        # (e) Starts mid-history: YouTube Premium starts Jun 2025
        yt_start = date(2025, 6, 1)
        yt_dates = _expand(
            "FREQ=MONTHLY;BYMONTHDAY=24", yt_start, history_start, end, None, "none", cal
        )
        for yd in yt_dates:
            scheduled_events.append(
                _ScheduledEvent(
                    yd, 21_900, "spend", "bank", "entertainment", "youtube_prem", "upi", "ser_yt"
                )
            )
        series_list.append(
            Series(
                "ser_yt",
                "youtube_prem",
                "spend",
                "monthly on the 24th (started mid-history)",
                None,
                yt_start,
                None,
            )
        )

        # (f) Fortnightly series: Country Delight milk/tiffin
        fn_dates = _expand(
            "FREQ=WEEKLY;INTERVAL=2;BYDAY=WE", history_start, history_start, end, None, "none", cal
        )
        for fd in fn_dates:
            amt = rng.randint(18, 25) * 10_000
            scheduled_events.append(
                _ScheduledEvent(
                    fd, amt, "spend", "bank", "groceries", "milk_tiffin", "upi", "ser_milk"
                )
            )
        series_list.append(
            Series(
                "ser_milk",
                "milk_tiffin",
                "spend",
                "fortnightly on Wednesdays",
                None,
                history_start,
                None,
            )
        )

        # (g) Quarterly series: RWA maintenance
        qt_dates = _expand(
            "FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY=10",
            date(2024, 7, 1),
            history_start,
            end,
            None,
            "next",
            cal,
        )
        for qd in qt_dates:
            scheduled_events.append(
                _ScheduledEvent(
                    qd, 960_000, "spend", "bank", "family", "society_maint", "upi", "ser_rwa"
                )
            )
        series_list.append(
            Series(
                "ser_rwa",
                "society_maint",
                "spend",
                "quarterly on the 10th",
                None,
                date(2024, 7, 1),
                None,
            )
        )
    else:
        # Standard unentered OTT + Gym + Yearly insurance.
        _add_unscheduled_monthly(
            rng=rng,
            series_id="ser_netflix",
            payee_key="netflix",
            cadence="monthly on the 18th",
            rule="FREQ=MONTHLY;BYMONTHDAY=18",
            starts=history_start,
            ends=None,
            base_paise=64_900,
            jitter_frac=0.0,
            account=primary_card_id,
            category_id="entertainment",
            payee_mode="card",
            holiday_adjust="none",
            cal=cal,
            history_start=history_start,
            end=end,
            scheduled_events=scheduled_events,
            series_list=series_list,
        )
        _add_unscheduled_monthly(
            rng=rng,
            series_id="ser_gym",
            payee_key="cult_fit",
            cadence="monthly on the 2nd",
            rule="FREQ=MONTHLY;BYMONTHDAY=2",
            starts=history_start,
            ends=None,
            base_paise=149_900,
            jitter_frac=0.0,
            account=primary_card_id if key == "tight_card" else "bank",
            category_id="health",
            payee_mode="card" if key == "tight_card" else "upi",
            holiday_adjust="none",
            cal=cal,
            history_start=history_start,
            end=end,
            scheduled_events=scheduled_events,
            series_list=series_list,
        )

    # Electricity, Water, Gas, Insurance
    if key == "seasonal_bills":
        elec_dates = _expand(
            "FREQ=MONTHLY;BYMONTHDAY=14", history_start, history_start, end, None, "next", cal
        )
        for ed in elec_dates:
            if ed.month in (4, 5, 6):
                elec_amt = rng.randint(680, 920) * 1_000
            elif ed.month in (3, 10):
                elec_amt = rng.randint(340, 440) * 1_000
            else:
                elec_amt = rng.randint(200, 270) * 1_000
            scheduled_events.append(
                _ScheduledEvent(
                    ed, elec_amt, "spend", "bank", "utilities", "bescom_power", "upi", "ser_elec"
                )
            )
        series_list.append(
            Series(
                "ser_elec",
                "bescom_power",
                "spend",
                "monthly on the 14th (summer peak)",
                None,
                history_start,
                None,
            )
        )

        water_dates = _expand(
            "FREQ=MONTHLY;BYMONTHDAY=16", history_start, history_start, end, None, "next", cal
        )
        for wd in water_dates:
            w_mult = 1.35 if wd.month in (3, 4, 5) else 1.0
            w_amt = int(round(rng.randint(65, 130) * 1_000 * w_mult))
            scheduled_events.append(
                _ScheduledEvent(
                    wd, w_amt, "spend", "bank", "utilities", "bwssb_water", "upi", "ser_water"
                )
            )
        series_list.append(
            Series(
                "ser_water",
                "bwssb_water",
                "spend",
                "monthly on the 16th (variable)",
                None,
                history_start,
                None,
            )
        )

        gas_dates = _expand(
            "FREQ=MONTHLY;BYMONTHDAY=23", history_start, history_start, end, None, "none", cal
        )
        for gd in gas_dates:
            g_amt = rng.randint(85, 135) * 1_000
            scheduled_events.append(
                _ScheduledEvent(
                    gd, g_amt, "spend", "bank", "utilities", "indane_gas", "upi", "ser_gas"
                )
            )
        series_list.append(
            Series(
                "ser_gas",
                "indane_gas",
                "spend",
                "monthly on the 23rd (variable)",
                None,
                history_start,
                None,
            )
        )

        ins_dates = _expand(
            "FREQ=YEARLY;BYMONTH=9;BYMONTHDAY=18",
            history_start,
            history_start,
            end,
            None,
            "next",
            cal,
        )
        for ind in ins_dates:
            scheduled_events.append(
                _ScheduledEvent(
                    ind, 3_600_000, "spend", "bank", "health", "hdfc_life", "nach", "ser_ins"
                )
            )
        series_list.append(
            Series(
                "ser_ins", "hdfc_life", "spend", "yearly in September", None, history_start, None
            )
        )
    else:
        _add_unscheduled_monthly(
            rng=rng,
            series_id="ser_elec",
            payee_key="bescom_power",
            cadence="monthly on the 14th",
            rule="FREQ=MONTHLY;BYMONTHDAY=14",
            starts=history_start,
            ends=None,
            base_paise=230_000,
            jitter_frac=0.22,
            account="bank",
            category_id="utilities",
            payee_mode="upi",
            holiday_adjust="next",
            cal=cal,
            history_start=history_start,
            end=end,
            scheduled_events=scheduled_events,
            series_list=series_list,
        )
        if key != "new_user":
            ins_dates = _expand(
                "FREQ=YEARLY;BYMONTH=8;BYMONTHDAY=21",
                history_start,
                history_start,
                end,
                None,
                "next",
                cal,
            )
            for ind in ins_dates:
                scheduled_events.append(
                    _ScheduledEvent(
                        ind, 1_450_000, "spend", "bank", "health", "hdfc_life", "nach", "ser_ins"
                    )
                )
            series_list.append(
                Series(
                    "ser_ins", "hdfc_life", "spend", "yearly in August", None, history_start, None
                )
            )

    diwali_windows: tuple[tuple[date, date], ...] = (
        (date(2024, 10, 18), date(2024, 11, 8)),
        (date(2025, 10, 10), date(2025, 10, 31)),
        (date(2026, 10, 15), date(2026, 10, 31)),
    )

    movements = _simulate_ledger(
        rng=rng,
        history_start=history_start,
        end=end,
        cards=cards,
        scheduled_events=scheduled_events,
        cat_params=cat_params,
        persona_key=key,
        cal=cal,
        salary_dates=salary_dates,
        diwali_windows=diwali_windows,
    )

    target_breach_share = {
        "salaried_steady": 0.08,
        "heavy_tails": 0.16,
        "family_runs": 0.16,
        "lifestyle_drift": 0.18,
        "rare_large": 0.16,
        "freelancer": 0.22,
        "tight_card": 0.20,
        "new_user": 0.16,
        "messy_recurring": 0.15,
        "seasonal_bills": 0.16,
    }[key]

    base_opening = 10_000_000 if key != "tight_card" else 6_000_000
    min_cushion = 1_500_000 if key in ("tight_card", "freelancer") else 2_200_000
    opening_bank_paise, floor_paise = _calibrate_opening_and_floor(
        history_start=history_start,
        end=end,
        movements=movements,
        base_opening=base_opening,
        target_share=target_breach_share,
        min_cushion=min_cushion,
    )

    descriptions = {
        "salaried_steady": "Steady salaried Indian household with predictable monthly cashflows.",
        "heavy_tails": (
            "Salaried household with heavy-tailed food and shopping spikes (Rs 15k-60k)."
        ),
        "family_runs": (
            "Family with correlated outing/festival regimes, payday bump, quarterly school fees."
        ),
        "lifestyle_drift": (
            "Household whose spend drifts +1.5%/mo and rent rises +8%/yr on a stale schedule."
        ),
        "rare_large": "Household hit every 3-5 months by Pareto-sized Rs 20k-2L rare expenses.",
        "freelancer": (
            "Freelancer with fixed retainer, unconfirmed variable client, and ad-hoc projects."
        ),
        "tight_card": (
            "Card-heavy household with two low-limit cards that bind and occasional early payments."
        ),
        "new_user": (
            "New user starting March 2026 with stated per-day category baselines off by +-30%."
        ),
        "messy_recurring": (
            "Irregular salary/bonus plus price-changed, skipped, double, cancelled, new, "
            "fortnightly, and quarterly subscriptions."
        ),
        "seasonal_bills": (
            "Summer electricity peak (Apr-Jun), variable water/gas, Diwali surge, yearly insurance."
        ),
    }

    return Persona(
        key=key,
        seed=seed,
        description=descriptions[key],
        history_start=history_start,
        end=end,
        opening_bank_paise=opening_bank_paise,
        floor_paise=floor_paise,
        cards=cards,
        categories=categories,
        schedules=tuple(schedules_list),
        goals=goals,
        movements=movements,
        series=tuple(series_list),
    )


def bank_balance(persona: Persona, day: date) -> int:
    """Compute end-of-day bank balance on `day` from movements only."""
    balance = persona.opening_bank_paise
    for movement in persona.movements:
        if movement.date > day:
            continue
        if movement.kind == "income" and movement.account == "bank":
            balance += movement.amount_paise
        elif movement.kind == "spend" and movement.account == "bank":
            balance -= movement.amount_paise
        elif movement.kind == "refund" and movement.account == "bank":
            balance += movement.amount_paise
        elif movement.kind == "card_payment":
            balance -= movement.amount_paise
        elif movement.kind == "goal_contribution" and movement.account == "bank":
            balance -= movement.amount_paise
    return balance


def card_outstanding(persona: Persona, card_id: str, day: date) -> int:
    """Compute end-of-day unpaid balance on `card_id` on `day` from movements only."""
    if not any(card.id == card_id for card in persona.cards):
        raise ValueError(f"Unknown card id {card_id!r} for persona {persona.key!r}")
    outstanding = 0
    for movement in persona.movements:
        if movement.date <= day and movement.account == card_id:
            if movement.kind == "spend":
                outstanding += movement.amount_paise
            elif movement.kind == "refund":
                outstanding -= movement.amount_paise
            elif movement.kind == "card_payment":
                outstanding -= movement.amount_paise
    return outstanding
