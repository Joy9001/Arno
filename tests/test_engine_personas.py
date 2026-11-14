"""Tests for deterministic adversarial synthetic household personas."""

from __future__ import annotations

import ast
import calendar as _calendar
import math
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from arno.engine.personas import (
    END,
    HISTORY_START,
    HOLDOUT_SEEDS,
    PERSONA_KEYS,
    SCORED_DAYS,
    SCORED_ORIGINS,
    TUNING_SEEDS,
    Card,
    Persona,
    bank_balance,
    card_outstanding,
    generate,
)
from arno.india.recurrence import expand_rule
from arno.india.workdays import HolidayAdjust, calendar_for

_PERSONAS_PATH = Path(__file__).resolve().parents[1] / "src" / "arno" / "engine" / "personas.py"

_EXPECTED_KEYS = (
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


def _month_last_day(year: int, month: int) -> int:
    return _calendar.monthrange(year, month)[1]


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    return total // 12, (total % 12) + 1


def _statement_cycles(card: Card, start: date, end: date) -> list[tuple[date, date, date]]:
    """Return (cycle_start, close_date, due_date) for statements closing in [start, end]."""
    cycles: list[tuple[date, date, date]] = []
    y, m = start.year, start.month
    prev_close = start - timedelta(days=1)
    while True:
        close_day = date(y, m, min(card.statement_day, _month_last_day(y, m)))
        if close_day > end:
            break
        if close_day >= start:
            if card.due_day > card.statement_day:
                dy, dm = y, m
            else:
                dy, dm = _add_months(y, m, 1)
            due_day = date(dy, dm, min(card.due_day, _month_last_day(dy, dm)))
            cycles.append((prev_close + timedelta(days=1), close_day, due_day))
            prev_close = close_day
        y, m = _add_months(y, m, 1)
    return cycles


def _daily_bank_balances(persona: Persona) -> list[int]:
    days = (persona.end - persona.history_start).days + 1
    deltas = [0] * days
    for m in persona.movements:
        idx = (m.date - persona.history_start).days
        if m.kind in ("income", "refund") and m.account == "bank":
            deltas[idx] += m.amount_paise
        elif m.kind in ("spend", "goal_contribution") and m.account == "bank":
            deltas[idx] -= m.amount_paise
        elif m.kind == "card_payment":
            deltas[idx] -= m.amount_paise
    balances = [0] * days
    running = persona.opening_bank_paise
    for i, delta in enumerate(deltas):
        running += delta
        balances[i] = running
    return balances


def _floor_breach_share(persona: Persona) -> float:
    # The windows a backtest scores: weekly over the last year, each 90 days ahead.
    balances = _daily_bank_balances(persona)
    last = len(balances) - SCORED_DAYS - 1
    starts = [last - 7 * step for step in range(SCORED_ORIGINS) if last - 7 * step >= 14]
    assert starts
    breaches = sum(
        1 for i in starts if min(balances[i : i + SCORED_DAYS + 1]) < persona.floor_paise
    )
    return breaches / len(starts)


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0.0 or dy == 0.0:
        return 0.0
    return num / (dx * dy)


def test_ast_import_independence_rule() -> None:
    tree = ast.parse(_PERSONAS_PATH.read_text(encoding="utf-8"), filename=str(_PERSONAS_PATH))
    forbidden_prefixes = ("arno.engine", "arno.tools", "arno.ledger", "arno.db", "arno.api")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                assert not alias.name.startswith(forbidden_prefixes), (
                    f"Forbidden import: {alias.name}"
                )
                assert top in sys.stdlib_module_names, f"Non-stdlib import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "Relative imports are disallowed"
            mod = node.module or ""
            assert not mod.startswith(forbidden_prefixes), f"Forbidden import: {mod}"
            top = mod.split(".")[0]
            if top == "arno":
                assert mod == "arno.india" or mod.startswith("arno.india."), (
                    f"Only arno.india imports allowed, got {mod}"
                )
            else:
                assert top in sys.stdlib_module_names, f"Non-stdlib import: {mod}"


def test_keys_and_constants() -> None:
    assert PERSONA_KEYS == _EXPECTED_KEYS
    assert HISTORY_START == date(2024, 6, 1)
    assert END == date(2026, 10, 31)
    assert TUNING_SEEDS == (101, 102)
    assert HOLDOUT_SEEDS == (201, 202)
    with pytest.raises(ValueError):
        generate("unknown_persona", 101)


@pytest.mark.parametrize("key", PERSONA_KEYS)
def test_determinism_speed_and_seed_variation(key: str) -> None:
    t0 = time.perf_counter()
    p1 = generate(key, TUNING_SEEDS[0])
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"{key} took {elapsed:.3f}s"

    p1_repeat = generate(key, TUNING_SEEDS[0])
    assert p1 == p1_repeat

    p2 = generate(key, TUNING_SEEDS[1])
    assert p1.movements != p2.movements


@pytest.mark.parametrize("key", PERSONA_KEYS)
@pytest.mark.parametrize("seed", [TUNING_SEEDS[0], HOLDOUT_SEEDS[0]])
def test_accounting_and_invariants(key: str, seed: int) -> None:
    p = generate(key, seed)
    assert p.key == key
    assert p.seed == seed
    assert p.end == END
    assert len(p.cards) in (1, 2)
    assert len(p.goals) >= 1

    # Movement invariants
    seen_ids: set[str] = set()
    cat_ids = {c.id for c in p.categories}
    card_ids = {c.id for c in p.cards}
    goal_ids = {g.id for g in p.goals}
    series_by_id = {s.id: s for s in p.series}
    sched_by_id = {s.id: s for s in p.schedules}

    for idx, m in enumerate(p.movements):
        assert m.id not in seen_ids
        seen_ids.add(m.id)
        assert isinstance(m.amount_paise, int) and m.amount_paise > 0
        assert p.history_start <= m.date <= p.end
        if idx > 0:
            prev = p.movements[idx - 1]
            assert (prev.date, prev.id) <= (m.date, m.id)

        if m.kind in ("spend", "refund"):
            assert m.account == "bank" or m.account in card_ids
            assert m.category_id in cat_ids
            assert m.goal_id is None
        elif m.kind == "income":
            assert m.account == "bank"
            assert m.category_id is None
            assert m.goal_id is None
        elif m.kind == "card_payment":
            assert m.account in card_ids
            assert m.category_id is None
            assert m.goal_id is None
        elif m.kind == "goal_contribution":
            assert m.account == "bank"
            assert m.category_id is None
            assert m.goal_id in goal_ids
        else:
            pytest.fail(f"Unexpected movement kind: {m.kind}")

        if m.series_id is not None:
            ser = series_by_id[m.series_id]
            assert ser.payee_key == m.payee_key
            assert ser.start <= m.date
            if ser.end is not None:
                assert m.date <= ser.end

    # Series -> Schedule confirmation consistency
    for ser in p.series:
        if ser.schedule_id is not None:
            sched = sched_by_id[ser.schedule_id]
            assert sched.confirmed

    # Card statement reconciliation & daily credit limit invariant
    for card in p.cards:
        cycles = _statement_cycles(card, p.history_start, p.end)
        payments = [m for m in p.movements if m.kind == "card_payment" and m.account == card.id]
        matched_payment_ids: set[str] = set()
        for c_start, c_close, c_due in cycles:
            charges = sum(
                m.amount_paise
                for m in p.movements
                if m.kind == "spend" and m.account == card.id and c_start <= m.date <= c_close
            )
            refunds = sum(
                m.amount_paise
                for m in p.movements
                if m.kind == "refund" and m.account == card.id and c_start <= m.date <= c_close
            )
            bill = charges - refunds
            cycle_payments = [pm for pm in payments if c_close < pm.date <= c_due]
            if bill > 0 and c_due <= p.end:
                assert len(cycle_payments) == 1, (
                    f"{key} {card.id} cycle {c_close} due {c_due}: "
                    f"expected 1 payment, got {len(cycle_payments)}"
                )
                assert cycle_payments[0].amount_paise == bill
                matched_payment_ids.add(cycle_payments[0].id)
            else:
                assert len(cycle_payments) == 0
        assert len(matched_payment_ids) == len(payments)

        # Check every day's end-of-day outstanding against credit limit
        running_out = 0
        mov_by_day: dict[date, list[int]] = {}
        for m in p.movements:
            if m.account == card.id:
                delta = m.amount_paise if m.kind == "spend" else -m.amount_paise
                mov_by_day.setdefault(m.date, []).append(delta)
        cur = p.history_start
        while cur <= p.end:
            for d in mov_by_day.get(cur, []):
                running_out += d
            assert 0 <= running_out <= card.credit_limit_paise
            cur += timedelta(days=1)

        # Spot-check card_outstanding helper matches running balance
        mid = p.history_start + timedelta(days=90)
        assert card_outstanding(p, card.id, p.end) == running_out
        assert 0 <= card_outstanding(p, card.id, mid) <= card.credit_limit_paise

    # Bank balance helper matches daily sweep and stays non-negative
    bals = _daily_bank_balances(p)
    assert min(bals) >= 0
    assert bank_balance(p, p.end) == bals[-1]


@pytest.mark.parametrize("key", PERSONA_KEYS)
def test_schedule_rules_expand(key: str) -> None:
    p = generate(key, TUNING_SEEDS[0])
    cal = calendar_for(p.history_start - timedelta(days=14), p.end + timedelta(days=14))
    assert len(p.schedules) >= 4
    for sched in p.schedules:
        assert sched.created <= p.end
        assert sched.amount_paise > 0
        adj: HolidayAdjust = (
            sched.holiday_adjust if sched.holiday_adjust in ("none", "next", "previous") else "none"
        )
        dates = expand_rule(
            sched.rule,
            sched.starts,
            start=p.history_start,
            end=p.end,
            ends=sched.ends,
            holiday_adjust=adj,
            calendar=cal,
        )
        assert len(dates) >= 1


def test_floor_breaches_across_personas() -> None:
    breaching = 0
    for key in PERSONA_KEYS:
        p = generate(key, TUNING_SEEDS[0])
        share = _floor_breach_share(p)
        if share > 0.0:
            breaching += 1
        assert share <= 0.35, f"{key} breach share too high: {share:.2%}"
    assert breaching == len(PERSONA_KEYS)


def test_signature_salaried_steady_and_heavy_tails() -> None:
    steady = generate("salaried_steady", 101)
    heavy = generate("heavy_tails", 101)

    # Steady salary lands on last working day
    cal = calendar_for(steady.history_start - timedelta(days=14), steady.end + timedelta(days=14))
    sal_dates = [m.date for m in steady.movements if m.series_id == "ser_salary"]
    assert len(sal_dates) == 29
    for d in sal_dates:
        assert cal.is_working_day(d)

    # Heavy tails: top-1% day > 8x median spending day + Rs 15k-60k purchases
    daily_spend: dict[date, int] = {}
    cur = heavy.history_start
    while cur <= heavy.end:
        daily_spend[cur] = 0
        cur += timedelta(days=1)
    for m in heavy.movements:
        if m.kind == "spend":
            daily_spend[m.date] += m.amount_paise
    vals = sorted(v for v in daily_spend.values() if v > 0)
    median_day = statistics.median(vals)
    top_1pct_idx = max(0, int(0.99 * len(vals)) - 1)
    top_1pct_day = vals[top_1pct_idx]
    assert top_1pct_day > 8 * median_day

    big_spikes = [
        m
        for m in heavy.movements
        if m.kind == "spend"
        and m.category_id in ("shopping", "food")
        and 1_500_000 <= m.amount_paise <= 6_000_000
    ]
    assert len(big_spikes) >= 5


def test_signature_family_runs() -> None:
    p = generate("family_runs", 101)
    days = (p.end - p.history_start).days + 1
    food_daily = [0.0] * days
    ent_daily = [0.0] * days
    shop_daily = [0.0] * days
    salary_days: list[date] = []

    for m in p.movements:
        idx = (m.date - p.history_start).days
        if m.kind == "income" and m.series_id == "ser_salary":
            salary_days.append(m.date)
        elif m.kind == "spend" and m.series_id is None:
            if m.category_id == "food":
                food_daily[idx] += m.amount_paise
            elif m.category_id == "entertainment":
                ent_daily[idx] += m.amount_paise
            elif m.category_id == "shopping":
                shop_daily[idx] += m.amount_paise

    assert _pearson(food_daily, ent_daily) > 0.15
    assert _pearson(food_daily, shop_daily) > 0.10

    # Payday effect: higher discretionary spend in 0..5 days after salary
    post_sal: list[int] = []
    other_days: list[int] = []
    sal_set = set()
    for sd in salary_days:
        for k in range(6):
            sal_set.add(sd + timedelta(days=k))
    disc_by_day = [0] * days
    for m in p.movements:
        if m.kind == "spend" and m.series_id is None:
            disc_by_day[(m.date - p.history_start).days] += m.amount_paise
    for i in range(days):
        d = p.history_start + timedelta(days=i)
        if d in sal_set:
            post_sal.append(disc_by_day[i])
        else:
            other_days.append(disc_by_day[i])
    assert statistics.mean(post_sal) > 1.2 * statistics.mean(other_days)

    school_movs = [m for m in p.movements if m.series_id == "ser_school"]
    assert 8 <= len(school_movs) <= 10


def test_signature_lifestyle_drift() -> None:
    p = generate("lifestyle_drift", 101)
    rent_sched = next(s for s in p.schedules if s.id == "sched_rent")
    rent_movs = [m for m in p.movements if m.series_id == "ser_rent"]
    assert rent_movs[0].amount_paise == rent_sched.amount_paise
    assert rent_movs[-1].amount_paise > int(round(rent_sched.amount_paise * 1.15))

    early_end = p.history_start + timedelta(days=180)
    late_start = p.end - timedelta(days=180)
    early_spend = sum(
        m.amount_paise
        for m in p.movements
        if m.kind == "spend" and m.series_id is None and m.date < early_end
    )
    late_spend = sum(
        m.amount_paise
        for m in p.movements
        if m.kind == "spend" and m.series_id is None and m.date > late_start
    )
    assert late_spend > 1.25 * early_spend


def test_signature_rare_large() -> None:
    p = generate("rare_large", 101)
    shocks = [
        m
        for m in p.movements
        if m.kind == "spend" and m.series_id is None and 2_000_000 <= m.amount_paise <= 20_000_000
    ]
    assert len(shocks) >= 5
    card_shocks = [m for m in shocks if m.account != "bank"]
    bank_shocks = [m for m in shocks if m.account == "bank"]
    assert len(card_shocks) >= 2
    assert len(bank_shocks) >= 1


def test_signature_freelancer() -> None:
    p = generate("freelancer", 101)
    fixed = [s for s in p.schedules if s.kind == "income_fixed" and s.confirmed]
    unconf = [s for s in p.schedules if s.kind == "income_variable" and not s.confirmed]
    assert len(fixed) == 1
    assert len(unconf) == 1

    adhoc_inc = [m for m in p.movements if m.kind == "income" and m.series_id is None]
    assert len(adhoc_inc) >= 12
    assert _floor_breach_share(p) >= 0.05


def test_signature_tight_card() -> None:
    p = generate("tight_card", 101)
    assert len(p.cards) == 2
    assert max(c.credit_limit_paise for c in p.cards) <= 3_000_000

    # Hits >= 90% of card limit at least 3 distinct times (months)
    high_util_months: set[tuple[int, int]] = set()
    for card in p.cards:
        cur = p.history_start
        while cur <= p.end:
            out = card_outstanding(p, card.id, cur)
            if out >= int(0.85 * card.credit_limit_paise):
                high_util_months.add((cur.year, cur.month))
            cur += timedelta(days=1)
    assert len(high_util_months) >= 3

    # Pays at least one card bill 1-3 days early
    early_payments = 0
    for card in p.cards:
        for _c_start, c_close, c_due in _statement_cycles(card, p.history_start, p.end):
            for m in p.movements:
                if (
                    m.kind == "card_payment"
                    and m.account == card.id
                    and c_close < m.date < c_due
                    and 1 <= (c_due - m.date).days <= 3
                ):
                    early_payments += 1
    assert early_payments >= 1


def test_signature_new_user() -> None:
    p = generate("new_user", 101)
    assert p.history_start == date(2026, 3, 1)
    days = (p.end - p.history_start).days + 1
    assert days < 260
    rel_errors: list[float] = []
    for cat in p.categories:
        assert cat.stated_per_day_paise is not None
        actual_total = sum(
            m.amount_paise
            for m in p.movements
            if m.kind == "spend" and m.series_id is None and m.category_id == cat.id
        )
        actual_per_day = actual_total / days
        if actual_per_day > 0:
            rel_errors.append(abs(cat.stated_per_day_paise - actual_per_day) / actual_per_day)
    assert statistics.median(rel_errors) >= 0.15


def test_signature_messy_recurring() -> None:
    p = generate("messy_recurring", 101)
    cal = calendar_for(p.history_start - timedelta(days=14), p.end + timedelta(days=14))
    sal_sched = next(s for s in p.schedules if s.id == "sched_salary")
    expected_sal = set(
        expand_rule(
            sal_sched.rule,
            sal_sched.starts,
            start=p.history_start,
            end=p.end,
            holiday_adjust="previous",
            calendar=cal,
        )
    )
    sal_movs = [m for m in p.movements if m.series_id == "ser_salary"]
    actual_sal_dates = {m.date for m in sal_movs}
    assert actual_sal_dates != expected_sal
    assert any(m.amount_paise > int(1.5 * sal_sched.amount_paise) for m in sal_movs)

    # Price change (netflix)
    nf_amts = {m.amount_paise for m in p.movements if m.series_id == "ser_nf"}
    assert len(nf_amts) == 2

    # Skipped month (cult_fit has 28 instead of 29 months)
    gym_movs = [m for m in p.movements if m.series_id == "ser_gym"]
    assert len(gym_movs) == 28

    # Double charge (jio_fiber has 30 charges across 29 months)
    jio_movs = [m for m in p.movements if m.series_id == "ser_jio"]
    assert len(jio_movs) == 30

    # Cancelled series (prime_video ends before END)
    prime_ser = next(s for s in p.series if s.id == "ser_prime")
    assert prime_ser.end is not None and prime_ser.end < p.end
    assert all(m.date <= prime_ser.end for m in p.movements if m.series_id == "ser_prime")

    # Mid-history start (youtube_prem starts after history_start)
    yt_ser = next(s for s in p.series if s.id == "ser_yt")
    assert yt_ser.start > p.history_start
    assert all(m.date >= yt_ser.start for m in p.movements if m.series_id == "ser_yt")

    # Fortnightly & quarterly series
    milk_movs = [m for m in p.movements if m.series_id == "ser_milk"]
    rwa_movs = [m for m in p.movements if m.series_id == "ser_rwa"]
    assert len(milk_movs) >= 55
    assert 8 <= len(rwa_movs) <= 10

    # UPI payee text varies while payee_key stays stable
    jio_texts = {m.payee for m in jio_movs}
    assert len(jio_texts) == len(jio_movs)
    assert {m.payee_key for m in jio_movs} == {"jio_fiber"}


def test_signature_seasonal_bills() -> None:
    p = generate("seasonal_bills", 101)
    elec = [m for m in p.movements if m.series_id == "ser_elec"]
    summer_elec = [m.amount_paise for m in elec if m.date.month in (4, 5, 6)]
    winter_elec = [m.amount_paise for m in elec if m.date.month in (11, 12, 1, 2)]
    assert statistics.mean(summer_elec) > 2.2 * statistics.mean(winter_elec)

    water_amts = {m.amount_paise for m in p.movements if m.series_id == "ser_water"}
    gas_amts = {m.amount_paise for m in p.movements if m.series_id == "ser_gas"}
    assert len(water_amts) >= 10
    assert len(gas_amts) >= 10

    # Diwali surge in Oct/Nov
    diwali_days: list[int] = []
    other_days: list[int] = []
    days = (p.end - p.history_start).days + 1
    disc = [0] * days
    for m in p.movements:
        if (
            m.kind == "spend"
            and m.series_id is None
            and m.category_id
            in (
                "shopping",
                "food",
                "family",
                "entertainment",
            )
        ):
            disc[(m.date - p.history_start).days] += m.amount_paise
    for i in range(days):
        d = p.history_start + timedelta(days=i)
        if (
            date(2024, 10, 18) <= d <= date(2024, 11, 8)
            or date(2025, 10, 10) <= d <= date(2025, 10, 31)
            or date(2026, 10, 15) <= d <= date(2026, 10, 31)
        ):
            diwali_days.append(disc[i])
        else:
            other_days.append(disc[i])
    assert statistics.mean(diwali_days) > 1.5 * statistics.mean(other_days)

    ins = [m for m in p.movements if m.series_id == "ser_ins"]
    assert len(ins) == 3
