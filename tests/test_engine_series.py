"""Tests for the gap-clustering recurring payment series detector."""

from __future__ import annotations

import random
from datetime import date, timedelta

from arno.engine.series import (
    PROJECT_CONFIDENCE,
    PROPOSE_ALIVE,
    PROPOSE_CONFIDENCE,
    Payment,
    _summarize_amounts,
    detect,
    normalize,
    price_step,
    stepped_level,
    upcoming,
)


def _make_payments(dates: list[date], amounts: list[int] | int = 50_000) -> list[Payment]:
    if isinstance(amounts, int):
        amt_list = [amounts] * len(dates)
    else:
        amt_list = amounts
    return [
        Payment(movement_id=f"m_{idx:03d}", date=d, amount_paise=a)
        for idx, (d, a) in enumerate(zip(dates, amt_list, strict=True))
    ]


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


def test_normalize_indian_statements_and_devanagari() -> None:
    assert normalize("UPI/DR/412345678901/SWIGGY/YESB/swiggy@ybl") == "swiggy yesb swiggy ybl"
    assert normalize("NACH DR NETFLIX ENTERTAINMENT 98213") == "netflix entertainment"
    assert normalize("POS 4591XXXXXX1234 AMAZON PAY IN") == "amazon pay in"
    assert normalize("UPI/DR/9988776655/किराना स्टोर PVT LTD") == "किराना स्टोर"
    assert normalize(None) == ""
    assert normalize("  --- /// 123456  ") == ""


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


def test_price_change_stepped_amounts() -> None:
    dates = [date(2026, m, 7) for m in range(1, 9)]
    amounts = [19_900, 19_900, 19_900, 19_900, 24_900, 24_900, 24_900, 24_900]
    res = detect(
        _make_payments(dates, amounts), as_of=date(2026, 8, 15), window_start=date(2025, 12, 1)
    )
    assert res is not None
    assert res.rule.family == "monthly"
    assert res.amounts.kind == "stepped"
    assert res.amounts.level_paise == 24_900
    assert res.amounts.low_paise == 24_900
    assert res.amounts.high_paise == 24_900


def _every(start: date, days: int, count: int) -> list[date]:
    return [start + timedelta(days=days * index) for index in range(count)]


def test_clean_monthly_on_the_5th() -> None:
    dates = [date(2026, month, 5) for month in range(1, 9)]
    res = detect(
        _make_payments(dates, 199_00), as_of=date(2026, 8, 20), window_start=date(2025, 8, 1)
    )
    assert res is not None
    assert res.rule.family == "monthly"
    assert res.rule.rrule == "FREQ=MONTHLY;BYMONTHDAY=5"
    assert res.rule.description == "monthly on the 5th"
    assert res.confidence == 1.0
    assert res.alive == 1.0
    assert res.matched == tuple(f"m_{i:03d}" for i in range(8))
    assert res.amounts.kind == "fixed"
    assert res.amounts.level_paise == 199_00
    assert upcoming(res, after=date(2026, 8, 20), until=date(2026, 10, 31)) == [
        date(2026, 9, 5),
        date(2026, 10, 5),
    ]


def test_each_cadence_maps_to_its_family_and_projects_forward() -> None:
    # 2025-12-01 is a Monday; 2026-01-02 is a Friday.
    cases = [
        (date(2025, 12, 1), 7, 8, "weekly", "weekly on Monday"),
        (date(2025, 12, 1), 14, 6, "fortnightly", "fortnightly on Monday"),
        (date(2025, 12, 1), 28, 6, "four_weekly", "every 4 weeks"),
        (date(2025, 1, 2), 91, 4, "quarterly", "quarterly on the 2nd"),
    ]
    for start, days, count, family, description in cases:
        dates = _every(start, days, count)
        as_of = dates[-1] + timedelta(days=2)
        res = detect(_make_payments(dates), as_of=as_of, window_start=start)
        assert res is not None, family
        assert res.rule.family == family
        if family != "quarterly":
            assert res.rule.description == description
            expected = [dates[-1] + timedelta(days=days)]
            assert upcoming(res, after=as_of, until=expected[0]) == expected
        else:
            assert res.rule.description.startswith("quarterly on the ")


def test_gaps_within_three_days_of_the_cadence_still_match() -> None:
    dates = [date(2026, 1, 5), date(2026, 2, 4), date(2026, 3, 6), date(2026, 4, 8)]
    res = detect(_make_payments(dates), as_of=date(2026, 4, 10), window_start=date(2025, 12, 1))
    assert res is not None
    assert res.rule.family == "monthly"
    assert res.confidence == 1.0
    assert sum(prob for _offset, prob in res.jitter) == 1.0


def test_a_skipped_month_lowers_confidence_but_keeps_the_cadence() -> None:
    dates = [date(2026, m, 5) for m in (1, 2, 3, 5, 6, 7, 8, 9)]
    res = detect(_make_payments(dates), as_of=date(2026, 9, 20), window_start=date(2025, 12, 1))
    assert res is not None
    assert res.rule.family == "monthly"
    assert res.confidence == 6 / 7
    assert res.confidence < PROJECT_CONFIDENCE


def test_a_series_that_stopped_is_not_alive() -> None:
    dates = [date(2026, m, 5) for m in range(1, 5)]
    res = detect(_make_payments(dates), as_of=date(2026, 8, 20), window_start=date(2025, 12, 1))
    assert res is not None
    assert res.alive == 0.0
    alive = detect(_make_payments(dates), as_of=date(2026, 5, 20), window_start=date(2025, 12, 1))
    assert alive is not None and alive.alive == 1.0


def test_irregular_payments_are_not_a_series() -> None:
    rng = random.Random(7)
    as_of = date(2026, 11, 15)
    dates = sorted(as_of - timedelta(days=rng.randrange(1, 800)) for _ in range(20))
    res = detect(_make_payments(dates), as_of=as_of, window_start=as_of - timedelta(days=800))
    # A median gap can land near a cadence by chance; the gaps then do not agree with it.
    assert res is None or res.confidence < PROPOSE_CONFIDENCE
    # A daily habit has no cadence in the table either.
    assert (
        detect(
            _make_payments(_every(date(2026, 1, 1), 1, 40)),
            as_of=date(2026, 3, 1),
            window_start=date(2025, 12, 1),
        )
        is None
    )


def test_fewer_than_three_payments_in_the_window_is_none() -> None:
    assert (
        detect(
            _make_payments([date(2025, 12, 5), date(2026, 4, 5), date(2026, 5, 5)]),
            as_of=date(2026, 5, 10),
            window_start=date(2026, 1, 1),
        )
        is None
    )


def test_a_payment_dated_as_of_is_not_an_observation() -> None:
    dates = [date(2026, 1, 5), date(2026, 2, 5), date(2026, 3, 5)]
    assert (
        detect(_make_payments(dates), as_of=date(2026, 3, 5), window_start=date(2025, 12, 1))
        is None
    )


def test_upcoming_is_empty_for_an_empty_window() -> None:
    dates = [date(2026, m, 5) for m in range(1, 6)]
    res = detect(_make_payments(dates), as_of=date(2026, 5, 10), window_start=date(2025, 12, 1))
    assert res is not None
    assert upcoming(res, after=date(2026, 5, 10), until=date(2026, 5, 10)) == []


def test_confidence_threshold_constants() -> None:
    assert PROPOSE_CONFIDENCE == 0.9
    assert PROPOSE_ALIVE == 0.5
    assert PROJECT_CONFIDENCE == 1.0


def test_a_stray_payment_before_the_series_is_left_out_of_it() -> None:
    dates = [date(2026, 9, 12), date(2026, 10, 3), date(2026, 10, 10), date(2026, 10, 17)]
    res = detect(
        _make_payments(dates, [30_000, 40_000, 40_000, 40_000]),
        as_of=date(2026, 11, 1),
        window_start=date(2026, 1, 1),
    )
    assert res is not None
    assert res.rule.family == "weekly"
    assert res.confidence == 1.0
    assert res.matched == ("m_001", "m_002", "m_003")
    assert res.amounts.level_paise == 40_000
