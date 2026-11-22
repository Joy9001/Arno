"""Tests for the pure recurring payment series detector."""

from __future__ import annotations

import itertools
import random
from datetime import date, timedelta
from fractions import Fraction

from arno.engine import series as series_mod
from arno.engine.series import (
    _E,
    _END_GRID,
    _JITTER_GRID,
    _MAX_CANDIDATES,
    _SKIP_GRID,
    PROJECT_CONFIDENCE,
    PROPOSE_ALIVE,
    PROPOSE_CONFIDENCE,
    UNRELATED_SHARE,
    Detection,
    Payment,
    Rule,
    _censoring_skip_probs,
    _detect_with,
    _dp_likelihood_ratio,
    _generate_candidates,
    _jitter_weights,
    _precompute_matches,
    _summarize_amounts,
    detect,
    normalize,
    price_step,
    stepped_level,
    upcoming,
)
from arno.india.workdays import calendar_for


def _brute_force_lr(
    payment_ordinals: list[int],
    expected_ordinals: list[int],
    *,
    win_start_ord: int,
    as_of_ord: int,
    q: float,
    w: int,
    c: float,
    unrelated_share: float = UNRELATED_SHARE,
) -> tuple[Fraction, Fraction]:
    """Exact Fraction enumeration over every (s, t, labeling) for the generative model."""
    m = len(payment_ordinals)
    n = len(expected_ordinals)
    t_days = as_of_ord - win_start_ord

    q_f = Fraction(str(q))
    c_f = Fraction(str(c))
    u_f = Fraction(str(unrelated_share))
    e_f = Fraction(str(_E))
    one_minus_q = 1 - q_f
    one_minus_c = 1 - c_f
    pi_f = Fraction(1, n)
    denom = (w + 1) * (w + 1)
    lambda_f = Fraction(m, 1) / (e_f * t_days)
    l0_f = lambda_f**m

    def f_frac(d: int) -> Fraction:
        ad = abs(d)
        if ad > w:
            return Fraction(0)
        return Fraction(w + 1 - ad, denom)

    q_j: list[Fraction] = []
    for eo in expected_ordinals:
        cens = Fraction(0)
        for d in range(-w, w + 1):
            day = eo + d
            # A payment due on or after as_of may not have posted yet: censored, like one before
            # the window.
            if day < win_start_ord or day >= as_of_ord:
                cens += f_frac(d)
        q_j.append(q_f + one_minus_q * cens)

    total_l = Fraction(0)
    alive_l = Fraction(0)

    for s in range(n):
        for t in range(s, n):
            p_end = Fraction(1) if t == n - 1 else c_f
            p_t_given_s = p_end * (one_minus_c ** (t - s))
            active_len = t - s + 1
            max_k = min(m, active_len)
            term_sum = Fraction(0)

            for k in range(max_k + 1):
                unrelated_factor = (u_f * lambda_f) ** (m - k)
                for obs_idxs in itertools.combinations(range(m), k):
                    for exp_idxs in itertools.combinations(range(s, t + 1), k):
                        prod = unrelated_factor
                        possible = True
                        for r in range(k):
                            f_val = f_frac(
                                payment_ordinals[obs_idxs[r]] - expected_ordinals[exp_idxs[r]]
                            )
                            if f_val == 0:
                                possible = False
                                break
                            prod *= one_minus_q * f_val
                        if not possible:
                            continue
                        matched_j = set(exp_idxs)
                        for j in range(s, t + 1):
                            if j not in matched_j:
                                prod *= q_j[j]
                        term_sum += prod

            contrib = pi_f * p_t_given_s * term_sum
            total_l += contrib
            if t == n - 1:
                alive_l += contrib

    total_lr = total_l / l0_f
    alive = (alive_l / total_l) if total_l > 0 else Fraction(0)
    return total_lr, alive


def test_dp_matches_brute_force_oracle() -> None:
    """Verify DP likelihood ratio and alive probability against exact Fraction brute force."""
    rng = random.Random(20261115)
    base = date(2026, 1, 10)
    win_start_ord = base.toordinal()

    # Curated edge cases + random small instances (25 instances x 12 grid points x 3 u = 900).
    instances: list[tuple[int, list[int], list[int]]] = [
        # 1. Censoring at both ends
        (
            win_start_ord + 12,
            [win_start_ord, win_start_ord + 6, win_start_ord + 12],
            [win_start_ord - 2, win_start_ord + 5, win_start_ord + 12],
        ),
        # 2. Start after first expected date, end before last expected date
        (
            win_start_ord + 40,
            [win_start_ord + 15, win_start_ord + 25],
            [win_start_ord + 5, win_start_ord + 15, win_start_ord + 25, win_start_ord + 35],
        ),
        # 3. Double payments near one expected date
        (
            win_start_ord + 30,
            [win_start_ord + 10, win_start_ord + 11, win_start_ord + 20, win_start_ord + 20],
            [win_start_ord + 10, win_start_ord + 20, win_start_ord + 29],
        ),
        # 4. All-unrelated observations
        (
            win_start_ord + 45,
            [win_start_ord + 2, win_start_ord + 18, win_start_ord + 41],
            [win_start_ord + 10, win_start_ord + 25, win_start_ord + 35],
        ),
        # 5. Tightly packed 5 obs and 5 censored/overlapping expected dates
        (
            win_start_ord + 8,
            [win_start_ord + d for d in (0, 2, 4, 6, 8)],
            [
                win_start_ord - 1,
                win_start_ord + 1,
                win_start_ord + 4,
                win_start_ord + 7,
                win_start_ord + 8,
            ],
        ),
    ]

    for _ in range(20):
        span = rng.randint(12, 60)
        as_of_ord = win_start_ord + span
        n_exp = rng.randint(1, 5)
        m_obs = rng.randint(1, 5)
        exp_ords = sorted(rng.sample(range(win_start_ord - 3, as_of_ord + 1), k=n_exp))
        obs_ords = sorted(rng.choices(range(win_start_ord, as_of_ord + 1), k=m_obs))
        instances.append((as_of_ord, obs_ords, exp_ords))

    assert len(instances) == 25
    checks = 0
    for u in (1.0, 0.1, 0.37):
        for as_of_ord, obs_ords, exp_ords in instances:
            t_days = float(as_of_ord - win_start_ord)
            m = len(obs_ords)
            n = len(exp_ords)
            background_scale = _E * t_days / m
            for w in _JITTER_GRID:
                f_weights = _jitter_weights(w)
                matches_by_j = _precompute_matches(obs_ords, exp_ords, w=w, f_weights=f_weights)
                for q in _SKIP_GRID:
                    q_j = _censoring_skip_probs(
                        exp_ords,
                        win_start_ord=win_start_ord,
                        as_of_ord=as_of_ord,
                        q=q,
                        w=w,
                        f_weights=f_weights,
                    )
                    for c in _END_GRID:
                        dp_lr, dp_exp2, dp_alive = _dp_likelihood_ratio(
                            m,
                            n,
                            q=q,
                            c=c,
                            background_scale=background_scale,
                            q_j=q_j,
                            matches_by_j=matches_by_j,
                            unrelated_share=u,
                        )
                        dp_val = dp_lr * (2.0**dp_exp2)
                        bf_lr, bf_alive = _brute_force_lr(
                            obs_ords,
                            exp_ords,
                            win_start_ord=win_start_ord,
                            as_of_ord=as_of_ord,
                            q=q,
                            w=w,
                            c=c,
                            unrelated_share=u,
                        )
                        bf_val = float(bf_lr)
                        if bf_val == 0.0:
                            assert dp_val == 0.0
                        else:
                            assert abs(dp_val - bf_val) / bf_val < 1e-12
                        assert abs(dp_alive - float(bf_alive)) < 1e-12
                        checks += 1
    assert checks == 900


def _make_payments(dates: list[date], amounts: list[int] | int = 50_000) -> list[Payment]:
    if isinstance(amounts, int):
        amt_list = [amounts] * len(dates)
    else:
        amt_list = amounts
    return [
        Payment(movement_id=f"m_{idx:03d}", date=d, amount_paise=a)
        for idx, (d, a) in enumerate(zip(dates, amt_list, strict=True))
    ]


def test_clean_monthly_on_5th() -> None:
    dates = [date(2026, m, 5) for m in range(1, 9)]
    res = detect(
        _make_payments(dates, 199_00),
        as_of=date(2026, 8, 20),
        window_start=date(2025, 8, 1),
    )
    assert res is not None
    assert res.rule.family == "monthly"
    assert res.confidence > 0.9
    assert res.matched == tuple(f"m_{i:03d}" for i in range(8))
    assert res.amounts.kind == "fixed"
    assert res.amounts.level_paise == 199_00
    assert res.next_dates[0][0] == date(2026, 9, 5)


def test_monthly_next_working_day_across_weekends_and_indian_holiday() -> None:
    # 26th of each month in 2026 with holiday_adjust='next':
    # includes Republic Day (Jan 26 -> 27), Ram Navami (Mar 26 -> 27),
    # Muharram (Jun 26 -> 29), Eid-e-Milad (Aug 26 -> 27), and weekends (Apr 26, Jul 26, Sep 26).
    win_start = date(2025, 11, 1)
    as_of = date(2026, 11, 10)
    cal = calendar_for(date(2025, 10, 1), date(2027, 12, 31))
    assert cal.is_holiday(date(2026, 1, 26))
    assert cal.is_weekend(date(2026, 4, 26))
    raw_dates = [
        date(2026, 1, 27),
        date(2026, 2, 26),
        date(2026, 3, 27),
        date(2026, 4, 27),
        date(2026, 5, 26),
        date(2026, 6, 29),
        date(2026, 7, 27),
        date(2026, 8, 27),
        date(2026, 9, 28),
        date(2026, 10, 26),
    ]
    res = detect(
        _make_payments(raw_dates, 2_500_00), as_of=as_of, window_start=win_start, calendar=cal
    )
    assert res is not None
    assert res.rule.family == "monthly"
    assert res.rule.holiday_adjust == "next"
    assert res.confidence > 0.9


def test_salary_last_working_day() -> None:
    cal = calendar_for(date(2025, 10, 1), date(2027, 12, 31))
    from arno.india.workdays import last_working_day

    dates = [last_working_day(2026, m, cal) for m in range(1, 11)]
    res = detect(
        _make_payments(dates, 15_000_000),
        as_of=date(2026, 11, 5),
        window_start=date(2025, 11, 1),
        calendar=cal,
    )
    assert res is not None
    assert res.rule.family == "last_working_day"
    assert res.rule.holiday_adjust == "previous"
    assert res.confidence > 0.9


def test_skipped_month_still_detects() -> None:
    # Monthly on the 10th, missing May
    dates = [date(2026, m, 10) for m in (1, 2, 3, 4, 6, 7, 8, 9)]
    res = detect(
        _make_payments(dates, 49_900),
        as_of=date(2026, 9, 20),
        window_start=date(2025, 10, 1),
    )
    assert res is not None
    assert res.rule.family == "monthly"
    assert res.confidence > 0.9
    assert len(res.matched) == 8


def test_double_payment_in_one_month() -> None:
    payments = [
        Payment("p1", date(2026, 1, 15), 100_000),
        Payment("p2", date(2026, 2, 15), 100_000),
        Payment("p3a", date(2026, 3, 15), 100_000),
        Payment("p3b", date(2026, 3, 16), 100_000),
        Payment("p4", date(2026, 4, 15), 100_000),
        Payment("p5", date(2026, 5, 15), 100_000),
        Payment("p6", date(2026, 6, 15), 100_000),
    ]
    res = detect(payments, as_of=date(2026, 6, 25), window_start=date(2025, 12, 1))
    assert res is not None
    assert res.rule.family == "monthly"
    assert res.confidence > 0.9
    assert len(res.matched) == 6
    assert ("p3a" in res.matched) != ("p3b" in res.matched)


def test_price_change_stepped_amounts() -> None:
    dates = [date(2026, m, 7) for m in range(1, 9)]
    amounts = [19_900, 19_900, 19_900, 19_900, 24_900, 24_900, 24_900, 24_900]
    res = detect(
        _make_payments(dates, amounts),
        as_of=date(2026, 8, 15),
        window_start=date(2025, 12, 1),
    )
    assert res is not None
    assert res.rule.family == "monthly"
    assert res.amounts.kind == "stepped"
    assert res.amounts.level_paise == 24_900
    assert res.amounts.low_paise == 24_900
    assert res.amounts.high_paise == 24_900
    assert abs(res.amounts.coverage - (4 - 1) / (4 + 1)) < 1e-12


def test_a_price_step_is_reported_at_the_first_new_payment() -> None:
    step = price_step([19_900, 19_900, 19_900, 24_900, 24_900])
    assert step is not None
    assert (step.old_paise, step.new_paise, step.index) == (19_900, 24_900, 3)


def test_a_price_fall_is_reported_the_same_way() -> None:
    step = price_step([29_900, 29_900, 29_900, 24_900])
    assert step is not None
    assert (step.old_paise, step.new_paise, step.index) == (29_900, 24_900, 3)


def test_one_odd_payment_that_returns_to_the_old_level_is_not_a_step() -> None:
    assert price_step([19_900, 19_900, 19_900, 24_900, 19_900]) is None


def test_a_variable_bill_is_never_a_step() -> None:
    # An electricity bill: no fixed run, so ordinary variation is not a price change.
    assert price_step([32_000, 41_000, 28_000, 37_000, 45_000, 30_000]) is None


def test_two_payments_at_the_old_level_are_not_a_step() -> None:
    assert price_step([19_900, 19_900, 24_900, 24_900]) is None


def test_a_change_of_two_percent_or_less_is_not_a_step() -> None:
    # 2% of ₹199 is ₹3.98, so this move is inside the amount tolerance.
    assert price_step([19_900, 19_900, 19_900, 20_298, 20_298]) is None


def test_the_latest_of_two_price_steps_is_reported() -> None:
    step = price_step([19_900, 19_900, 19_900, 24_900, 24_900, 24_900, 29_900])
    assert step is not None
    assert (step.old_paise, step.new_paise, step.index) == (24_900, 29_900, 6)


def test_price_step_boundaries_directions_and_medians() -> None:
    assert price_step([]) is None
    assert price_step([19_900]) is None
    assert price_step([19_900, 19_900, 19_900, 19_900]) is None
    assert price_step([0, 0, 0, 19_900]) is None
    assert stepped_level([19_900], from_paise=0) is None
    assert stepped_level([19_900], from_paise=-100) is None

    # Exact 2% is ignored on both rises and falls; 1 paisa past 2% steps.
    assert price_step([10_000, 10_000, 10_000, 9_800]) is None
    fall = price_step([10_000, 10_000, 10_000, 9_799])
    assert fall is not None and (fall.old_paise, fall.new_paise, fall.index) == (10_000, 9_799, 3)
    rise = price_step([19_900, 19_900, 19_900, 20_299])
    assert rise is not None and (rise.old_paise, rise.new_paise, rise.index) == (19_900, 20_299, 3)

    # New payments moving in opposite directions or not forming a fixed run are rejected.
    assert price_step([19_900, 19_900, 19_900, 24_900, 14_900]) is None
    assert price_step([19_900, 19_900, 19_900, 24_900, 29_900]) is None

    # Even-length runs use integer floor median matching Amounts.
    even = price_step([10_000, 10_100, 10_000, 10_100, 12_000, 12_200])
    assert even is not None
    assert (even.old_paise, even.new_paise, even.index) == (10_050, 12_100, 4)
    det = detect(
        _make_payments(
            [date(2026, m, 5) for m in range(1, 7)],
            [10_000, 10_100, 10_000, 10_100, 12_000, 12_200],
        ),
        as_of=date(2026, 6, 20),
        window_start=date(2025, 12, 1),
    )
    assert det is not None and det.amounts.kind == "stepped"
    assert det.amounts.level_paise == even.new_paise


def test_recent_subscription_in_800_day_window() -> None:
    as_of = date(2026, 11, 15)
    window_start = as_of - timedelta(days=800)
    dates = [date(2026, 9, 5), date(2026, 10, 5), date(2026, 11, 5)]
    res = detect(_make_payments(dates, 64_900), as_of=as_of, window_start=window_start)
    assert res is not None
    assert res.rule.family == "monthly"
    assert res.confidence > 0.8
    assert res.alive > 0.8


def test_cancelled_series_low_alive() -> None:
    as_of = date(2026, 11, 15)
    window_start = date(2025, 10, 1)
    # Monthly on 10th from Nov 2025 through Jul 2026, then nothing Aug/Sep/Oct/Nov (4 misses)
    dates = [
        date(2025, 11, 10),
        date(2025, 12, 10),
        date(2026, 1, 10),
        date(2026, 2, 10),
        date(2026, 3, 10),
        date(2026, 4, 10),
        date(2026, 5, 10),
        date(2026, 6, 10),
        date(2026, 7, 10),
    ]
    res = detect(_make_payments(dates, 79_900), as_of=as_of, window_start=window_start)
    assert res is not None
    assert res.rule.family == "monthly"
    assert res.alive < 0.2


def test_censoring_treats_the_as_of_day_as_unobserved() -> None:
    # The expected day is as_of itself: it may not have posted yet, so it can never be a skip.
    expected = date(2026, 6, 15).toordinal()
    q_j = _censoring_skip_probs(
        [expected],
        win_start_ord=expected - 30,
        as_of_ord=expected,
        q=0.05,
        w=0,
        f_weights=_jitter_weights(0),
    )
    assert q_j == [1.0]
    # The day before as_of was observed, so it is skipped with the plain probability.
    q_j = _censoring_skip_probs(
        [expected - 1],
        win_start_ord=expected - 30,
        as_of_ord=expected,
        q=0.05,
        w=0,
        f_weights=_jitter_weights(0),
    )
    assert q_j == [0.05]


def test_a_payment_due_on_as_of_is_not_yet_missed() -> None:
    # Five monthly payments on the 15th, evaluated on the day the sixth is due: the sixth may not
    # have posted, so the series is alive, not stopped.
    dates = [date(2026, month, 15) for month in range(1, 6)]
    res = detect(
        _make_payments(dates, 64_900),
        as_of=date(2026, 6, 15),
        window_start=date(2025, 1, 1),
    )
    assert res is not None
    assert res.rule.family == "monthly"
    assert res.alive > 0.9


def test_fortnightly_vs_monthly() -> None:
    start = date(2026, 1, 2)  # Friday
    fort_dates = [start + timedelta(days=14 * k) for k in range(10)]
    res_f = detect(
        _make_payments(fort_dates, 150_000),
        as_of=date(2026, 5, 20),
        window_start=date(2025, 12, 1),
    )
    assert res_f is not None
    assert res_f.rule.family == "fortnightly"
    assert res_f.confidence > 0.9

    mon_dates = [date(2026, m, 12) for m in range(1, 9)]
    res_m = detect(
        _make_payments(mon_dates, 150_000),
        as_of=date(2026, 8, 25),
        window_start=date(2025, 12, 1),
    )
    assert res_m is not None
    assert res_m.rule.family == "monthly"
    assert res_m.confidence > 0.9


def test_quarterly() -> None:
    dates = [
        date(2025, 2, 15),
        date(2025, 5, 15),
        date(2025, 8, 15),
        date(2025, 11, 15),
        date(2026, 2, 15),
        date(2026, 5, 15),
        date(2026, 8, 15),
    ]
    res = detect(
        _make_payments(dates, 300_000),
        as_of=date(2026, 9, 1),
        window_start=date(2025, 1, 1),
    )
    assert res is not None
    assert res.rule.family == "quarterly"
    assert res.confidence > 0.9


def test_yearly_three_years() -> None:
    dates = [date(2024, 3, 18), date(2025, 3, 18), date(2026, 3, 18)]
    res = detect(
        _make_payments(dates, 1_200_000),
        as_of=date(2026, 10, 1),
        window_start=date(2024, 1, 1),
    )
    assert res is not None
    assert res.rule.family == "yearly"
    assert res.confidence > 0.9


def test_random_uniform_dates_low_confidence() -> None:
    rng = random.Random(7)
    win_start = date(2024, 9, 1)
    as_of = date(2026, 11, 10)
    span = (as_of - win_start).days
    offsets = sorted(rng.sample(range(span + 1), k=8))
    dates = [win_start + timedelta(days=o) for o in offsets]
    res = detect(_make_payments(dates, 50_000), as_of=as_of, window_start=win_start)
    assert res is not None
    assert res.confidence < 0.5


def test_random_uniform_20_payments_800_days_low_confidence() -> None:
    as_of = date(2026, 11, 15)
    window_start = as_of - timedelta(days=800)
    span = (as_of - window_start).days
    cal = calendar_for(window_start - timedelta(days=14), as_of + timedelta(days=410))
    for seed in (11, 23, 37, 53, 79):
        rng = random.Random(seed)
        offsets = sorted(rng.sample(range(span + 1), k=20))
        dates = [window_start + timedelta(days=o) for o in offsets]
        res = detect(
            _make_payments(dates, 50_000), as_of=as_of, window_start=window_start, calendar=cal
        )
        assert res is not None
        assert res.confidence < 0.5


def test_random_uniform_30_payments_800_days_low_confidence() -> None:
    as_of = date(2026, 11, 15)
    window_start = as_of - timedelta(days=800)
    span = (as_of - window_start).days
    cal = calendar_for(window_start - timedelta(days=14), as_of + timedelta(days=410))
    for seed in (11, 23, 37, 53, 79):
        rng = random.Random(seed)
        offsets = sorted(rng.sample(range(span + 1), k=30))
        dates = [window_start + timedelta(days=o) for o in offsets]
        res = detect(
            _make_payments(dates, 50_000), as_of=as_of, window_start=window_start, calendar=cal
        )
        assert res is not None
        assert res.confidence < 0.5


def test_weekly_does_not_beat_no_pattern_on_monthly_payments() -> None:
    # 4 clean monthly payments on the 5th (May..Aug 2026: Tue, Fri, Sun, Wed).
    dates = [date(2026, m, 5) for m in (5, 6, 7, 8)]
    payments = _make_payments(dates, 99_900)
    win_start = date(2026, 4, 1)
    as_of = date(2026, 8, 20)
    weekly_candidates = [
        Rule(
            family="weekly",
            rrule=f"FREQ=WEEKLY;BYDAY={code}",
            starts=win_start - timedelta(days=14),
            holiday_adjust="none",
            description=f"weekly on {code}",
        )
        for code in ("TU", "FR", "SU", "WE")
    ]
    res = _detect_with(weekly_candidates, payments, as_of=as_of, window_start=win_start)
    assert res is not None
    assert res.confidence < 0.5


def test_normalize_indian_statements_and_devanagari() -> None:
    assert normalize("UPI/DR/412345678901/SWIGGY/YESB/swiggy@ybl") == "swiggy yesb swiggy ybl"
    assert normalize("NACH DR NETFLIX ENTERTAINMENT 98213") == "netflix entertainment"
    assert normalize("POS 4591XXXXXX1234 AMAZON PAY IN") == "amazon pay in"
    assert normalize("UPI/DR/9988776655/किराना स्टोर PVT LTD") == "किराना स्टोर"
    assert normalize(None) == ""
    assert normalize("  --- /// 123456  ") == ""


def test_upcoming_and_empty_inputs() -> None:
    dates = [date(2026, m, 5) for m in range(1, 6)]
    res = detect(
        _make_payments(dates, 25_000),
        as_of=date(2026, 5, 10),
        window_start=date(2025, 12, 1),
    )
    assert res is not None
    assert upcoming(res, after=date(2026, 5, 10), until=date(2026, 8, 10)) == [
        date(2026, 6, 5),
        date(2026, 7, 5),
        date(2026, 8, 5),
    ]
    assert upcoming(res, after=date(2026, 5, 10), until=date(2026, 5, 10)) == []

    # Fewer than 3 payments in window -> None
    assert (
        detect(
            [Payment("only1", date(2026, 5, 5), 1000)],
            as_of=date(2026, 5, 10),
            window_start=date(2026, 1, 1),
        )
        is None
    )


def test_two_payments_returns_none() -> None:
    assert (
        detect(
            _make_payments([date(2026, 4, 5), date(2026, 5, 5)], 50_000),
            as_of=date(2026, 5, 10),
            window_start=date(2026, 1, 1),
        )
        is None
    )
    # 3 total payments, but only 2 fall inside [window_start, as_of] -> None
    assert (
        detect(
            _make_payments([date(2025, 12, 5), date(2026, 4, 5), date(2026, 5, 5)], 50_000),
            as_of=date(2026, 5, 10),
            window_start=date(2026, 1, 1),
        )
        is None
    )


def test_confidence_threshold_constants() -> None:
    assert PROPOSE_CONFIDENCE == 0.9
    assert PROPOSE_ALIVE == 0.5
    assert PROJECT_CONFIDENCE == 0.999


def test_60_payments_800_days_detects_weekly() -> None:
    as_of = date(2026, 11, 15)
    window_start = as_of - timedelta(days=800)
    cal = calendar_for(window_start - timedelta(days=14), as_of + timedelta(days=410))
    # 52 weekly payments + 8 extra noise dates across distinct days = 60 payments over 800 days
    first = window_start + timedelta(days=10)
    dates = [first + timedelta(days=7 * i) for i in range(52)]
    dates.extend(window_start + timedelta(days=100 * k + 3) for k in range(1, 9))
    payments = _make_payments(dates, 30_000)

    res = detect(payments, as_of=as_of, window_start=window_start, calendar=cal)

    assert res is not None
    assert res.rule.family == "weekly"
    assert res.confidence > 0.9


def test_determinism() -> None:
    dates = [date(2026, m, 5) for m in range(1, 7)]
    payments = _make_payments(dates, [10_000, 10_100, 9_950, 10_050, 10_000, 10_020])
    cal = calendar_for(date(2025, 11, 1), date(2027, 12, 31))
    d1: Detection | None = detect(
        payments, as_of=date(2026, 6, 15), window_start=date(2025, 12, 1), calendar=cal
    )
    d2: Detection | None = detect(
        list(reversed(payments)),
        as_of=date(2026, 6, 15),
        window_start=date(2025, 12, 1),
        calendar=cal,
    )
    assert d1 is not None and d2 is not None
    assert d1 == d2


def test_a_dense_payee_is_capped_to_the_last_200_payments(monkeypatch) -> None:
    start = date(2024, 1, 1)
    payments = _make_payments([start + timedelta(days=offset) for offset in range(801)])
    seen: list[list[Payment]] = []
    original = series_mod._detect_with

    def spy(candidates, rows, **kwargs):
        seen.append(list(rows))
        return original(candidates, rows, **kwargs)

    monkeypatch.setattr(series_mod, "_detect_with", spy)
    detect(payments, as_of=start + timedelta(days=800), window_start=start)
    assert len(seen) == 1
    assert [payment.movement_id for payment in seen[0]] == [
        f"m_{index:03d}" for index in range(600, 800)
    ]


def test_a_payment_dated_as_of_is_not_an_observation() -> None:
    # The as_of day is censored as possibly unposted, so its payment is not evidence of a series.
    assert (
        detect(
            _make_payments([date(2026, 3, 5), date(2026, 4, 5), date(2026, 5, 5)]),
            as_of=date(2026, 5, 5),
            window_start=date(2026, 1, 1),
        )
        is None
    )


def test_summarized_amounts_follow_price_step() -> None:
    two_steps = _summarize_amounts([50_000] * 6 + [70_000] * 3 + [90_000] * 3)
    assert two_steps.kind == "stepped"
    assert two_steps.level_paise == 90_000
    assert two_steps.samples == (90_000, 90_000, 90_000)

    one_step = _summarize_amounts([50_000] * 3 + [90_000] * 3)
    assert one_step.kind == "stepped"
    assert one_step.level_paise == 90_000
    assert one_step.samples == (90_000, 90_000, 90_000)

    # Two payments at each level is below price_step's three-payment run, so it stays variable.
    short = _summarize_amounts([50_000, 50_000, 90_000, 90_000])
    assert short.kind == "variable"
    assert short.level_paise == 70_000

    fixed = _summarize_amounts([64_900] * 4)
    assert fixed.kind == "fixed"
    assert fixed.level_paise == 64_900


def test_the_top_eight_candidates_keep_a_twice_monthly_cadence() -> None:
    dates = [
        date(2025 + (month - 1) // 12, (month - 1) % 12 + 1, day)
        for month in range(1, 15)
        for day in (1, 15)
    ]
    as_of = date(2026, 3, 20)
    window_start = as_of - timedelta(days=800)
    payments = _make_payments(dates, 64_900)
    cal = calendar_for(window_start - timedelta(days=21), as_of + timedelta(days=421))
    candidates = _generate_candidates(payments, window_start=window_start)
    assert len(candidates) > _MAX_CANDIDATES
    # The full candidate set is the oracle; detect() scores only the top eight by pruning score.
    oracle = _detect_with(
        candidates, payments, as_of=as_of, window_start=window_start, calendar=cal
    )
    detected = detect(payments, as_of=as_of, window_start=window_start, calendar=cal)
    assert oracle is not None and detected is not None
    assert oracle.rule.family == "twice_monthly"
    assert (detected.rule.family, detected.rule.rrule) == (oracle.rule.family, oracle.rule.rrule)
