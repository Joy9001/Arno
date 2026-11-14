"""The tested money maths the agent must call instead of writing formulas."""

from __future__ import annotations

import random
from datetime import date
from fractions import Fraction

import pytest

from arno.engine.money import (
    _add_months,
    add_months,
    allocate,
    amortize,
    card_emi,
    compound,
    infer_rate,
    scale_cumulative,
)


def _fraction_round_half_up(value: Fraction) -> int:
    num = value.numerator
    den = value.denominator
    if num >= 0:
        return (2 * num + den) // (2 * den)
    return -((2 * (-num) + den) // (2 * den))


def test_amortize_matches_a_known_emi():
    result = amortize(5_000_000_00, 8.5, 240, start_date=date(2026, 1, 1))
    # ₹50 lakh at 8.5% for 20 years is about ₹43,391 a month.
    assert 4_330_000 <= result["emi"]["paise"] <= 4_350_000
    assert result["months"] == 240
    assert result["total_interest"]["paise"] > 0
    assert result["schedule"][0]["date"] == "2026-02-01"
    assert result["schedule"][-1]["balance_paise"] < 1000


def test_amortize_known_bank_figure_ten_lakh():
    # ₹10,00,000 at 8.5% for 240 months gives an EMI of ₹8,678.23.
    result = amortize(1_000_000_00, 8.5, 240)
    assert result["emi"]["paise"] == 867_823
    assert result["emi"]["text"] == "₹8,678.23"


def test_defaults_reproduce_pinned_outputs():
    r1 = amortize(100, 0, 3)
    assert r1["emi"]["paise"] == 33
    assert r1["months"] == 3
    assert r1["total_interest"]["paise"] == 0
    assert r1["total_paid"]["paise"] == 100
    assert r1["schedule"][-1]["principal_paise"] == 34

    r2 = amortize(100_000, 12, 12)
    assert r2["emi"]["paise"] == 8_885
    assert r2["months"] == 12
    assert r2["total_interest"]["paise"] == 6_619
    assert r2["total_paid"]["paise"] == 106_619

    r3 = amortize(1_000_000_00, 8.5, 240)
    assert r3["emi"]["paise"] == 867_823
    assert r3["total_interest"]["paise"] == 108_277_663
    assert r3["total_paid"]["paise"] == 208_277_663

    r4 = amortize(5_000_000_00, 8.5, 240, start_date=date(2026, 1, 1))
    assert r4["emi"]["paise"] == 4_339_116
    assert r4["total_interest"]["paise"] == 541_387_944
    assert r4["total_paid"]["paise"] == 1_041_387_944
    assert r4["payoff_date"] == "2046-01-01"

    r5 = amortize(
        5_000_000_00,
        8.5,
        240,
        prepay={"amount_paise": 5_000_000, "at_emi": 12, "reduce": "tenure"},
    )
    assert r5["prepay"] is not None
    assert r5["prepay"]["new_emi"]["paise"] == 4_339_116
    assert r5["prepay"]["new_months"] == 235
    assert r5["prepay"]["new_total_interest"]["paise"] == 521_633_572
    assert r5["prepay"]["interest_saved"]["paise"] == 19_754_372
    assert r5["prepay"]["months_saved"] == 5

    r6 = amortize(
        5_000_000_00,
        8.5,
        240,
        prepay={"amount_paise": 5_000_000, "at_emi": 12, "reduce": "emi"},
    )
    assert r6["prepay"] is not None
    assert r6["prepay"]["new_emi"]["paise"] == 4_294_922
    assert r6["prepay"]["new_months"] == 240
    assert r6["prepay"]["new_total_interest"]["paise"] == 536_267_185
    assert r6["prepay"]["interest_saved"]["paise"] == 5_120_759
    assert r6["prepay"]["months_saved"] == 0

    r7 = amortize(338_800_000, 8.5, 240, emi_paise=3_150_000, start_date=date(2026, 11, 10))
    assert r7["emi"]["paise"] == 3_150_000
    assert r7["months"] == 204
    assert r7["total_interest"]["paise"] == 301_551_240
    assert r7["total_paid"]["paise"] == 640_351_240
    assert r7["payoff_date"] == "2043-11-10"


def test_amortize_zero_rate_is_principal_over_months():
    result = amortize(120_000_00, 0, 12)
    assert result["emi"]["paise"] == 10_000_00
    assert result["total_interest"]["paise"] == 0
    assert result["months"] == 12


def test_amortize_prepay_shortens_tenure_and_saves_interest():
    base = amortize(5_000_000_00, 8.5, 240)
    prepaid = amortize(
        5_000_000_00,
        8.5,
        240,
        prepay={"amount_paise": 5_000_000, "at_emi": 12, "reduce": "tenure"},
    )
    applied = prepaid["prepay"]
    assert applied is not None
    assert applied["new_months"] < base["months"]
    assert applied["interest_saved"]["paise"] > 0
    assert applied["months_saved"] > 0


def test_amortize_prepay_lowers_the_emi():
    result = amortize(
        5_000_000_00,
        8.5,
        240,
        prepay={"amount_paise": 5_000_000, "at_emi": 12, "reduce": "emi"},
    )
    applied = result["prepay"]
    assert applied is not None
    assert applied["new_emi"]["paise"] < result["emi"]["paise"]


def test_an_emi_that_never_repays_is_refused():
    with pytest.raises(ValueError, match="never repays"):
        amortize(100_000_00, 60, 24, emi_paise=1_00)


def test_compound_pays_at_the_start_of_each_month():
    result = compound(500_000, 12, 120)
    # ₹5,000 a month at 12% for 10 years is about ₹11.6 lakh.
    assert result["final"]["paise"] == 116_169_528
    assert result["contributed"]["paise"] == 60_000_000
    assert result["growth"]["paise"] > 0
    assert result["yearly"][-1]["year"] == 10
    assert "assumed" in result["assumptions"][0]


def test_add_months_clamps_to_month_end_and_keeps_alias():
    assert add_months is _add_months
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert add_months(date(2026, 1, 31), 2) == date(2026, 3, 31)
    assert add_months(date(2026, 11, 15), 14) == date(2028, 1, 15)


def test_conventions_emi_rounding_and_interest_unit():
    base = amortize(1_000_000_00, 8.5, 240)
    rupee_up = amortize(
        1_000_000_00,
        8.5,
        240,
        emi_rounding="up",
        emi_unit_paise=100,
    )
    assert rupee_up["emi"]["paise"] % 100 == 0
    assert rupee_up["emi"]["paise"] >= base["emi"]["paise"]
    assert rupee_up["emi"]["paise"] == 867_900
    assert any("EMI rounded up to 100 paise" in note for note in rupee_up["assumptions"])

    rupee_interest = amortize(1_000_000_00, 8.5, 24, interest_unit_paise=100)
    assert all(row["interest_paise"] % 100 == 0 for row in rupee_interest["schedule"])
    assert any(
        "interest rounded half-up to 100 paise" in note for note in rupee_interest["assumptions"]
    )


def test_conventions_daily_rest_and_day_count():
    with pytest.raises(ValueError, match="start_date"):
        amortize(1_000_000_00, 8.5, 12, rest="daily")

    monthly = amortize(1_000_000_00, 12.0, 12, start_date=date(2026, 1, 1))
    daily_30_360 = amortize(
        1_000_000_00, 12.0, 12, start_date=date(2026, 1, 1), rest="daily", day_count="30/360"
    )
    assert daily_30_360["schedule"] == monthly["schedule"]
    assert any("daily rest (30/360)" in note for note in daily_30_360["assumptions"])

    daily_act_365 = amortize(
        1_000_000_00, 12.0, 12, start_date=date(2026, 1, 1), rest="daily", day_count="actual/365"
    )
    # January has 31 days (31/365 > 1/12); February 2026 has 28 days (28/365 < 1/12).
    assert daily_act_365["schedule"][0]["interest_paise"] > monthly["schedule"][0]["interest_paise"]
    assert daily_act_365["schedule"][1]["interest_paise"] < monthly["schedule"][1]["interest_paise"]
    assert any("daily rest (actual/365)" in note for note in daily_act_365["assumptions"])


def test_conventions_floating_rate_changes_tenure_and_emi_reset():
    base = amortize(1_000_000_00, 8.5, 120)
    hike_tenure = amortize(
        1_000_000_00,
        8.5,
        120,
        rate_changes=((13, 10.5),),
        reset="tenure",
    )
    assert hike_tenure["emi"]["paise"] == base["emi"]["paise"]
    assert hike_tenure["months"] > base["months"]
    assert hike_tenure["total_interest"]["paise"] > base["total_interest"]["paise"]
    assert any("rate resets (tenure)" in note for note in hike_tenure["assumptions"])

    cut_tenure = amortize(
        1_000_000_00,
        8.5,
        120,
        rate_changes=((13, 6.5),),
        reset="tenure",
    )
    assert cut_tenure["emi"]["paise"] == base["emi"]["paise"]
    assert cut_tenure["months"] < base["months"]
    assert cut_tenure["total_interest"]["paise"] < base["total_interest"]["paise"]

    hike_emi = amortize(
        1_000_000_00,
        8.5,
        120,
        rate_changes=((13, 10.5),),
        reset="emi",
    )
    assert hike_emi["months"] == 120
    row13 = hike_emi["schedule"][12]
    assert row13["principal_paise"] + row13["interest_paise"] > base["emi"]["paise"]
    assert any("rate resets (emi)" in note for note in hike_emi["assumptions"])

    with pytest.raises(ValueError, match="increasing"):
        amortize(1_000_000_00, 8.5, 120, rate_changes=((12, 9.0), (12, 10.0)))


def test_edge_case_emi_too_low_keeps_paying_and_reports_real_months():
    result = amortize(1_000_000_00, 8.5, 120, emi_paise=900_000)
    assert result["months"] > 120
    assert sum(row["principal_paise"] for row in result["schedule"]) == 1_000_000_00
    assert result["schedule"][-1]["balance_paise"] == 0
    assert any(
        f"this EMI takes {result['months']} months, not 120" in note
        for note in result["assumptions"]
    )
    for row in result["schedule"][:-1]:
        assert row["principal_paise"] + row["interest_paise"] == 900_000

    # Even 1 paisa short of full repayment in target months extends by one month.
    short = amortize(100, 0, 3, emi_paise=33)
    assert short["months"] == 4
    assert [row["principal_paise"] for row in short["schedule"]] == [33, 33, 33, 1]
    assert any("this EMI takes 4 months, not 3" in note for note in short["assumptions"])


def test_edge_case_prepayment_larger_than_balance_raises_with_balance():
    with pytest.raises(ValueError) as exc_info:
        amortize(
            100_000,
            12.0,
            12,
            prepay={"amount_paise": 200_000, "at_emi": 1, "reduce": "tenure"},
        )
    assert "100000" in str(exc_info.value)

    # Exact full payoff at at_emi succeeds.
    full = amortize(
        100_000,
        12.0,
        12,
        prepay={"amount_paise": 100_000, "at_emi": 1, "reduce": "tenure"},
    )
    assert full["prepay"] is not None
    assert full["prepay"]["new_months"] == 0
    assert full["prepay"]["new_total_interest"]["paise"] == 0


def test_infer_rate_round_trip_and_edge_cases():
    # Known bank figure round-trip: 8.5% = 85_000 bps_x100
    fit = infer_rate(1_000_000_00, 240, 867_823)
    assert fit["exact"] is True
    assert fit["low_bps_x100"] is not None and fit["high_bps_x100"] is not None
    assert fit["low_bps_x100"] <= 85_000 <= fit["high_bps_x100"]

    # Zero-rate loan
    zero = infer_rate(120_000_00, 12, 10_000_00)
    assert zero["exact"] is True
    assert zero["low_bps_x100"] == 0
    assert "zero-rate" in zero["note"]

    # Below principal/months: cannot repay
    too_low = infer_rate(120_000_00, 12, 9_000_00)
    assert too_low["exact"] is False
    assert too_low["low_bps_x100"] is None
    assert too_low["high_bps_x100"] is None
    assert "cannot repay" in too_low["note"]

    # Above 100% rate range
    too_high = infer_rate(100_000_00, 12, 50_000_00)
    assert too_high["exact"] is False
    assert too_high["low_bps_x100"] is None
    assert too_high["high_bps_x100"] is None

    # No exact match when rounded to rupees and observed EMI has paise
    bracket = infer_rate(
        1_000_000_00,
        240,
        867_850,
        emi_rounding="up",
        emi_unit_paise=100,
    )
    assert bracket["exact"] is False
    assert bracket["low_bps_x100"] is not None and bracket["high_bps_x100"] is not None
    assert bracket["high_bps_x100"] == bracket["low_bps_x100"] + 1


def test_infer_rate_property_round_trips():
    from typing import Literal

    rng = random.Random(20261114)
    roundings: tuple[Literal["half_up", "up"], ...] = ("half_up", "up")
    for _ in range(100):
        principal = rng.randint(10_000_00, 5_000_000_00)
        months = rng.randint(6, 360)
        unit = rng.randint(0, 250_000)  # 0% to 25%
        annual_rate = unit / 10_000
        rounding = rng.choice(roundings)
        unit_paise = rng.choice([1, 100])
        emi = amortize(
            principal,
            annual_rate,
            months,
            emi_rounding=rounding,
            emi_unit_paise=unit_paise,
        )["emi"]["paise"]
        fit = infer_rate(
            principal,
            months,
            emi,
            emi_rounding=rounding,
            emi_unit_paise=unit_paise,
        )
        assert fit["exact"] is True
        assert fit["low_bps_x100"] is not None and fit["high_bps_x100"] is not None
        assert fit["low_bps_x100"] <= unit <= fit["high_bps_x100"]


def test_allocate_properties_and_edge_cases():
    # Ties go to lower index; zero weights get 0
    assert allocate(100, [1, 1, 1]) == [34, 33, 33]
    assert allocate(100, [0, 1, 0, 1, 1]) == [0, 34, 0, 33, 33]
    # Negative total works by symmetry
    assert allocate(-100, [1, 1, 1]) == [-34, -33, -33]
    assert allocate(0, [1, 2, 3]) == [0, 0, 0]

    with pytest.raises(ValueError):
        allocate(100, [])
    with pytest.raises(ValueError):
        allocate(100, [0, 0, 0])
    with pytest.raises(ValueError):
        allocate(100, [1, -1, 2])

    rng = random.Random(42)
    for _ in range(200):
        total = rng.randint(-1_000_000, 1_000_000)
        n_weights = rng.randint(1, 20)
        weights = [rng.randint(0, 1000) for _ in range(n_weights)]
        if sum(weights) == 0:
            weights[0] = 1
        parts = allocate(total, weights)
        assert sum(parts) == total
        total_weight = sum(weights)
        for part, weight in zip(parts, weights, strict=True):
            if weight == 0:
                assert part == 0
            exact = Fraction(total * weight, total_weight)
            assert abs(Fraction(part) - exact) < 1


def test_scale_cumulative_properties_and_negatives():
    # Half-up away from zero for positive and negative amounts
    assert scale_cumulative([1, 1, 1], 1, 2) == [1, 0, 1]
    assert scale_cumulative([-1, -1, -1], 1, 2) == [-1, 0, -1]
    assert scale_cumulative([-3], 1, 2) == [-2]
    assert scale_cumulative([1, 1], -1, -2) == [1, 0]
    assert scale_cumulative([], 1, 3) == []

    with pytest.raises(ValueError):
        scale_cumulative([100], 1, 0)

    rng = random.Random(99)
    for _ in range(200):
        amounts = [rng.randint(-50_000, 50_000) for _ in range(rng.randint(1, 50))]
        numerator = rng.randint(-500, 500)
        denominator = rng.randint(1, 500)
        scaled = scale_cumulative(amounts, numerator, denominator)
        assert len(scaled) == len(amounts)
        prefix_in = 0
        prefix_out = 0
        for amount, out in zip(amounts, scaled, strict=True):
            prefix_in += amount
            prefix_out += out
            exact = Fraction(prefix_in * numerator, denominator)
            assert abs(Fraction(prefix_out) - exact) <= Fraction(1, 2)


def test_card_emi_matches_fraction_oracle_on_issuer_example():
    # ₹10,000, 6 months, 1.33% a month (1596 bps a year), GST 18% of interest
    principal = 1_000_000
    months = 6
    annual_rate_bps = 1596
    gst_bps = 1800

    result = card_emi(principal, annual_rate_bps=annual_rate_bps, months=months, gst_bps=gst_bps)

    monthly_rate = Fraction(annual_rate_bps, 120_000)
    gst_rate = Fraction(gst_bps, 10_000)
    factor = (1 + monthly_rate) ** months
    expected_emi = _fraction_round_half_up(principal * monthly_rate * factor / (factor - 1))

    assert result["emi_paise"] == expected_emi
    assert sum(row["principal_paise"] for row in result["schedule"]) == principal

    balance = principal
    oracle_rows: list[tuple[int, int, int]] = []
    for _ in range(months):
        interest = _fraction_round_half_up(balance * monthly_rate)
        prin = min(expected_emi - interest, balance)
        balance -= prin
        oracle_rows.append((prin, interest, _fraction_round_half_up(interest * gst_rate)))
    if balance > 0:
        last_p, last_i, last_g = oracle_rows[-1]
        oracle_rows[-1] = (last_p + balance, last_i, last_g)

    for row, (exp_p, exp_i, exp_g) in zip(result["schedule"], oracle_rows, strict=True):
        assert row["principal_paise"] == exp_p
        assert row["interest_paise"] == exp_i
        assert row["gst_paise"] == exp_g
        assert row["total_paise"] == exp_p + exp_i + exp_g

    assert result["total_interest_paise"] == sum(row[1] for row in oracle_rows)
    assert result["total_gst_paise"] == sum(row[2] for row in oracle_rows)
    assert result["fee_paise"] == 0
    assert result["cost_paise"] == result["total_interest_paise"] + result["total_gst_paise"]
    assert result["total_paise"] == principal + result["cost_paise"]


def test_card_emi_fee_no_cost_and_validation():
    no_cost = card_emi(
        1_000_000,
        annual_rate_bps=0,
        months=6,
        processing_fee_paise=19_900,
        gst_bps=1800,
    )
    assert no_cost["total_interest_paise"] == 0
    assert no_cost["fee_paise"] == 19_900
    assert no_cost["schedule"][0]["fee_paise"] == 19_900
    assert no_cost["schedule"][0]["fee_gst_paise"] == 3_582
    assert no_cost["schedule"][1]["fee_paise"] == 0
    assert no_cost["schedule"][1]["fee_gst_paise"] == 0
    assert no_cost["total_gst_paise"] == 3_582
    assert no_cost["cost_paise"] == 23_482
    assert no_cost["total_paise"] == 1_023_482
    assert sum(row["total_paise"] for row in no_cost["schedule"]) == no_cost["total_paise"]

    with pytest.raises(ValueError, match="2 and 48"):
        card_emi(100_000, annual_rate_bps=1500, months=1)
    with pytest.raises(ValueError, match="2 and 48"):
        card_emi(100_000, annual_rate_bps=1500, months=49)
