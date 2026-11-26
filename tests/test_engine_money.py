"""The tested money maths the agent must call instead of writing formulas."""

from __future__ import annotations

import random
from datetime import date
from decimal import ROUND_DOWN, Decimal, localcontext
from fractions import Fraction

import pytest

from arno.engine.money import (
    MAX_PAISE,
    CardPayoff,
    _add_months,
    _compute_apr,
    add_months,
    amortize,
    card_emi,
    card_payoff,
    compare_offers,
    compound,
    infer_rate,
)
from arno.india.formatting import format_paise


def test_offer_comparison_separates_each_charge_and_preserves_order():
    offers = compare_offers(
        [
            {"label": "Cash", "cash_price_paise": 10_000, "discount_paise": 1_000},
            {
                "label": "Card",
                "cash_price_paise": 10_000,
                "financing": {
                    "kind": "card_emi",
                    "annual_rate_bps": 1200,
                    "months": 2,
                    "processing_fee_paise": 500,
                },
            },
        ]
    )
    cash, card = offers
    assert cash["total_paid_paise"] == 9_000
    assert cash["difference_vs_cash_paise"] == -1_000
    assert cash["complete_cost"] is True
    assert card["label"] == "Card"
    assert card["interest_paise"] == 150
    assert card["gst_on_interest_paise"] == 27
    assert card["processing_fee_paise"] == 500
    assert card["gst_on_fee_paise"] == 90
    assert card["total_paid_paise"] == 10_767
    assert card["difference_vs_cash_paise"] == 767
    assert card["apr"] is not None
    assert card["complete_cost"] is False
    assert "foreclosure and prepayment charges" in card["not_included"]
    assert "best" not in card
    # The gap between offers comes from code, both ways, and the instalments sum to the total.
    assert cash["difference_vs_offers"] == [{"label": "Card", "difference_paise": -1_767}]
    assert card["difference_vs_offers"] == [{"label": "Cash", "difference_paise": 1_767}]
    assert cash["instalments_paise"] == []
    assert sum(card["instalments_paise"]) == card["total_paid_paise"]


def test_debt_costs_identify_omitted_charges_and_rate_resets():
    loan = amortize(10_000, 12, 2)
    card = card_payoff(10_000, annual_rate_bps=1200, payment=6_000)
    assert loan["complete_cost"] is False
    assert "future floating-rate resets not supplied" in loan["not_included"]
    assert card["complete_cost"] is False
    assert "late and contingent charges" in card["not_included"]


def test_a_stated_reset_schedule_still_leaves_other_charges_incomplete():
    with_resets = amortize(10_000, 10, 12, rate_changes=[(6, 11)])
    assert "future floating-rate resets not supplied" not in with_resets["not_included"]
    assert "foreclosure and prepayment charges" in with_resets["not_included"]
    assert "late and contingent charges" in with_resets["not_included"]
    assert with_resets["complete_cost"] is False


def test_offer_loan_fee_gst_and_cashback_are_not_counted_as_interest():
    cash, loan = compare_offers(
        [
            {"label": "Cash", "cash_price_paise": 10_000},
            {
                "label": "Loan",
                "cash_price_paise": 10_000,
                "cashback_paise": 100,
                "financing": {
                    "kind": "loan",
                    "annual_rate_bps": 0,
                    "months": 2,
                    "processing_fee_paise": 500,
                },
            },
        ]
    )
    assert cash["total_paid_paise"] == 10_000
    assert loan["interest_paise"] == 0
    assert loan["gst_on_interest_paise"] == 0
    assert loan["processing_fee_paise"] == 500
    assert loan["gst_on_fee_paise"] == 90
    assert loan["total_paid_paise"] == 10_590
    assert loan["net_cost_paise"] == 10_490
    assert loan["difference_vs_cash_paise"] == 490
    assert loan["apr"] is not None
    assert "cashback eligibility and payment conditions" in loan["not_included"]


def test_a_loan_offer_keeps_gst_off_interest_per_the_loan_path():
    _cash, loan = compare_offers(
        [
            {"label": "Cash", "cash_price_paise": 10_000},
            {
                "label": "Loan",
                "cash_price_paise": 10_000,
                "financing": {"kind": "loan", "annual_rate_bps": 1200, "months": 2},
            },
        ]
    )
    assert loan["interest_paise"] == 150
    assert loan["gst_on_interest_paise"] == 0
    assert loan["processing_fee_paise"] == 0
    assert loan["gst_on_fee_paise"] == 0
    assert loan["total_paid_paise"] == 10_150
    assert loan["difference_vs_cash_paise"] == 150


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


def test_amortize_rejects_a_rate_reset_beyond_the_tenure():
    with pytest.raises(ValueError, match="beyond the 12-month tenure"):
        amortize(1_000_000, 8.0, 12, rate_changes=[(24, 20.0)])
    # A reset on the last EMI is inside the horizon.
    assert amortize(1_000_000, 8.0, 12, rate_changes=[(12, 9.0)], reset="emi")["months"] == 12


def test_amortize_notes_a_balloon_from_a_coarse_emi_unit():
    result = amortize(2_000_000, 8.73, 240, emi_unit_paise=100)
    assert result["emi"]["paise"] == 17_600
    assert result["schedule"][-1]["principal_paise"] + result["schedule"][-1]["interest_paise"] == (
        49_057
    )
    assert any("the last instalment is ₹490.57, not ₹176" in note for note in result["assumptions"])


def test_prepay_tenure_keeps_the_emi_charged_when_a_rate_reset_coincides():
    reset = amortize(10_000_000, 8.0, 24, rate_changes=[(13, 12.0)], reset="emi")
    result = amortize(
        10_000_000,
        8.0,
        24,
        rate_changes=[(13, 12.0)],
        reset="emi",
        prepay={"amount_paise": 2_000_000, "at_emi": 13, "reduce": "tenure"},
    )
    applied = result["prepay"]
    assert applied is not None
    # The EMI kept is the one the reset put in force at EMI 13, not the pre-reset one.
    month_13 = reset["schedule"][12]
    assert applied["new_emi"]["paise"] == month_13["principal_paise"] + month_13["interest_paise"]
    assert applied["new_emi"]["paise"] != 452_273
    assert applied["months_saved"] > 0


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
    # Known bank figure round-trip: 8.5% = 85_000 bps_x100, and one unit reproduces the EMI.
    fit = infer_rate(1_000_000_00, 240, 867_823)
    assert fit["exact"] is True
    assert fit["low_bps_x100"] == 85_000
    assert fit["high_bps_x100"] == 85_000

    # Several rate units can round to the same EMI; that is an interval, not an exact rate.
    plateau = infer_rate(10_000_000, 240, 86_782)
    assert plateau["low_bps_x100"] == 84_999
    assert plateau["high_bps_x100"] == 85_000
    assert plateau["exact"] is False
    assert "8.4999% to 8.5000% a year" in plateau["note"]

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
        assert fit["low_bps_x100"] is not None and fit["high_bps_x100"] is not None
        assert fit["low_bps_x100"] <= unit <= fit["high_bps_x100"]
        # exact means one unit reproduces the EMI; a plateau is an interval, not a rate.
        assert fit["exact"] is (fit["low_bps_x100"] == fit["high_bps_x100"])
        if not fit["exact"]:
            assert " to " in fit["note"]


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


def test_card_emi_stops_when_a_small_principal_clears_early():
    # 72 paise over 48 months rounds to a 2-paise instalment that clears the loan in 36.
    result = card_emi(72, annual_rate_bps=0, months=48)
    assert result["emi_paise"] == 2
    assert len(result["schedule"]) == 36
    assert result["schedule"][-1]["principal_paise"] == 2
    assert sum(row["principal_paise"] for row in result["schedule"]) == 72
    assert sum(row["total_paise"] for row in result["schedule"]) == result["total_paise"]
    assert result["months"] == 48


def _pv_at_bps_x100(payments: list[int], bps_x100: int) -> Fraction:
    monthly_rate = Fraction(bps_x100, 12_000_000)
    one_plus_r = 1 + monthly_rate
    pv = Fraction(0)
    for payment in reversed(payments):
        pv = (pv + payment) / one_plus_r
    return pv


def test_amortize_fee_and_apr_hand_computed():
    # ₹1,000 (100,000 paise), 12% p.a. (1% per month), 1 month, ₹10 (1,000 paise) fee, 18% GST:
    #   month 1 interest = 100,000 * 1% = 1,000; EMI = 101,000 (paid at month 1)
    #   fee GST = 1,000 * 18% = 180; total fee = 1,180
    #   net disbursed = 100,000 - 1,180 = 98,820
    #   total cost = 1,000 + 1,180 = 2,180
    #   monthly IRR r = 101,000 / 98,820 - 1 = 2,180 / 98,820 = 109 / 4,941
    #   annual APR = 12 * r * 100% = 130,800 / 4,941 = 26.472374...% (264,723 to 264,724 bps_x100)
    one_month = amortize(
        100_000,
        12.0,
        1,
        processing_fee_paise=1_000,
    )
    assert one_month["processing_fee"] == {"paise": 1_000, "text": "₹10"}
    assert one_month["fee_gst"] == {"paise": 180, "text": "₹1.80"}
    assert one_month["total_fee"] == {"paise": 1_180, "text": "₹11.80"}
    assert one_month["net_disbursed"] == {"paise": 98_820, "text": "₹988.20"}
    assert one_month["total_cost"] == {"paise": 2_180, "text": "₹21.80"}
    assert one_month["emi"]["paise"] == 101_000
    assert one_month["apr"]["low_bps_x100"] == 264_723
    assert one_month["apr"]["high_bps_x100"] == 264_724
    assert one_month["apr"]["exact"] is False
    assert any("deducted at disbursal" in note for note in one_month["assumptions"])

    # 12-month loan with a rounded last payment: ₹1,000 at 12% for 12 months has
    # 11 EMIs of 8,885 and a final payment of 8,884 (total_paid = 106,619).
    # With ₹20 (2,000 paise) fee + 18% GST (360) = 2,360 total fee, net disbursed = 97,640.
    # total cost = 6,619 + 2,360 = 8,979.
    twelve_month = amortize(100_000, 12.0, 12, processing_fee_paise=2_000)
    assert twelve_month["total_fee"]["paise"] == 2_360
    assert twelve_month["net_disbursed"]["paise"] == 97_640
    assert twelve_month["total_cost"]["paise"] == 8_979
    assert twelve_month["apr"]["low_bps_x100"] is not None
    assert twelve_month["apr"]["low_bps_x100"] > 120_000


def test_amortize_fee_apr_above_100_percent_and_zero_gst():
    # ₹1,000 at 0% for one month with a ₹100 fee and no GST: the EMI is 100,000,
    # net disbursed is 90,000, so r = 10,000 / 90,000 = 1/9 a month and
    # APR = 12/9 = 133.3333...% a year (1,333,333.33 bps_x100).
    no_gst = amortize(100_000, 0.0, 1, processing_fee_paise=10_000, gst_bps=0)
    assert no_gst["fee_gst"] == {"paise": 0, "text": "₹0"}
    assert no_gst["total_fee"]["paise"] == 10_000
    assert no_gst["net_disbursed"]["paise"] == 90_000
    assert no_gst["total_cost"]["paise"] == 10_000
    assert no_gst["apr"]["low_bps_x100"] == 1_333_333
    assert no_gst["apr"]["high_bps_x100"] == 1_333_334
    assert no_gst["apr"]["exact"] is False
    assert "133.3333%" in no_gst["apr"]["note"]

    # Fee one paisa below the principal: net is 1 paisa and the EMI is 100,000, so
    # 1 + r = 100,000, r = 99,999 a month, and APR = 12 * 99,999 = 1,199,988 = 119,998,800%
    # a year exactly, on unit 1,199,988,000,000.
    one_paisa = amortize(100_000, 0.0, 1, processing_fee_paise=99_999, gst_bps=0)
    assert one_paisa["net_disbursed"]["paise"] == 1
    assert one_paisa["apr"]["low_bps_x100"] == 1_199_988_000_000
    assert one_paisa["apr"]["high_bps_x100"] == 1_199_988_000_000
    assert one_paisa["apr"]["exact"] is True
    assert "119998800.0000%" in one_paisa["apr"]["note"]


def test_amortize_zero_fee_leaves_return_unchanged_and_zero_fee_apr_matches_rate():
    baseline = amortize(1_000_000_00, 8.5, 240, start_date=date(2026, 1, 1))
    explicit_zero = amortize(
        1_000_000_00,
        8.5,
        240,
        start_date=date(2026, 1, 1),
        processing_fee_paise=0,
    )
    assert set(explicit_zero.keys()) == set(baseline.keys())
    assert explicit_zero == baseline
    assert set(baseline.keys()) == {
        "emi",
        "months",
        "total_interest",
        "total_paid",
        "payoff_date",
        "schedule",
        "prepay",
        "assumptions",
        "complete_cost",
        "not_included",
    }

    # Internal APR helper on a zero-fee schedule matches the loan rate within schedule rounding.
    payments = [row["interest_paise"] + row["principal_paise"] for row in baseline["schedule"]]
    zero_fee_apr = _compute_apr(1_000_000_00, payments)
    assert zero_fee_apr["low_bps_x100"] is not None
    assert zero_fee_apr["high_bps_x100"] is not None
    assert abs(zero_fee_apr["low_bps_x100"] - 85_000) <= 1

    zero_rate = amortize(120_000_00, 0.0, 12)
    zero_payments = [
        row["interest_paise"] + row["principal_paise"] for row in zero_rate["schedule"]
    ]
    zero_apr = _compute_apr(120_000_00, zero_payments)
    assert zero_apr["low_bps_x100"] == 0
    assert zero_apr["high_bps_x100"] == 0
    assert zero_apr["exact"] is True


def test_compute_apr_refuses_a_negative_irr():
    # Payments that total less than the net amount disbursed need a negative rate.
    with pytest.raises(ValueError, match="APR is negative"):
        _compute_apr(10_000, [4_000, 4_000])
    # Exactly zero is still a valid, exact 0% APR.
    zero = _compute_apr(10_000, [10_000])
    assert zero == {
        "low_bps_x100": 0,
        "high_bps_x100": 0,
        "exact": True,
        "note": "exact APR 0.0000% a year",
    }


def test_rbi_kfs_annex_b_apr_matches_the_published_vector():
    # RBI circular 12663, Annex B illustration (lines 525-551 of the published reprint): a
    # ₹20,000 loan, ₹400 charges deducted, ₹19,600 net disbursed, 24 monthly instalments of
    # ₹969.73 before rupee rounding, at a nominal APR of 17.07% (12 * monthly IRR).
    # https://www.rbi.org.in/scripts/NotificationUser.aspx?Id=12987&Mode=0
    fit = _compute_apr(1_960_000, [96_973] * 24)
    low, high = fit["low_bps_x100"], fit["high_bps_x100"]
    assert low is not None and high is not None
    assert (low, high) == (170_702, 170_703)
    midpoint = (low + high) / 2 / 10_000
    assert 17.0702 <= midpoint <= 17.0703
    assert f"{midpoint:.2f}" == "17.07"

    # The loan path reaches the same vector: 15% for 24 months is that EMI, and the fee lifts
    # the nominal APR by about two points.
    loan = amortize(2_000_000, 15.0, 24, processing_fee_paise=40_000, gst_bps=0)
    assert loan["emi"]["paise"] == 96_973
    assert loan["net_disbursed"]["paise"] == 1_960_000
    apr = loan["apr"]
    apr_low, apr_high = apr["low_bps_x100"], apr["high_bps_x100"]
    assert apr_low is not None and apr_high is not None
    assert f"{(apr_low + apr_high) / 2 / 10_000:.2f}" == "17.07"


def test_amortize_fee_validation_repricing_monotone_and_prepay():
    with pytest.raises(ValueError, match="negative"):
        amortize(100_000, 12.0, 12, processing_fee_paise=-1)
    with pytest.raises(ValueError, match="negative"):
        amortize(100_000, 12.0, 12, processing_fee_paise=1_000, gst_bps=-1)
    # 85,000 + 18% GST (15,300) = 100,300 > 100,000 principal
    with pytest.raises(ValueError, match="less than principal"):
        amortize(100_000, 12.0, 12, processing_fee_paise=85_000)
    # Exact equality: 100,000 + 0% GST == 100,000 principal
    with pytest.raises(ValueError, match="less than principal"):
        amortize(100_000, 12.0, 12, processing_fee_paise=100_000, gst_bps=0)

    # Repricing & monotonicity across fees (including uneven last payment and prepay baseline).
    prev_low = 0
    for fee in (100_00, 500_00, 2_500_00, 10_000_00):
        res = amortize(5_000_000_00, 8.5, 240, processing_fee_paise=fee)
        payments = [row["interest_paise"] + row["principal_paise"] for row in res["schedule"]]
        apr = res["apr"]
        net = res["net_disbursed"]["paise"]
        assert apr["low_bps_x100"] is not None and apr["high_bps_x100"] is not None
        assert apr["high_bps_x100"] - apr["low_bps_x100"] <= 1
        assert _pv_at_bps_x100(payments, apr["low_bps_x100"]) >= Fraction(net)
        assert _pv_at_bps_x100(payments, apr["high_bps_x100"]) <= Fraction(net)
        assert apr["low_bps_x100"] > prev_low
        prev_low = apr["low_bps_x100"]

    # With prepay given, APR describes the baseline schedule only.
    base_fee = amortize(1_000_000_00, 9.0, 60, processing_fee_paise=10_000_00)
    prepay_fee = amortize(
        1_000_000_00,
        9.0,
        60,
        processing_fee_paise=10_000_00,
        prepay={"amount_paise": 200_000_00, "at_emi": 12, "reduce": "tenure"},
    )
    assert prepay_fee["apr"] == base_fee["apr"]
    assert prepay_fee["prepay"] is not None
    assert prepay_fee["prepay"]["months_saved"] > 0


def _assert_card_payoff_invariants(balance_paise: int, res: CardPayoff) -> None:
    schedule = res["schedule"]
    assert res["months"] == len(schedule)
    assert len(schedule) > 0
    assert schedule[0]["opening_paise"] == balance_paise
    assert schedule[-1]["closing_paise"] == 0
    for idx, row in enumerate(schedule):
        assert row["n"] == idx + 1
        assert (
            row["closing_paise"]
            == row["opening_paise"]
            + row["finance_charge_paise"]
            + row["gst_paise"]
            - row["payment_paise"]
        )
        if idx + 1 < len(schedule):
            assert row["closing_paise"] == schedule[idx + 1]["opening_paise"]
    total_fc = sum(row["finance_charge_paise"] for row in schedule)
    total_gst = sum(row["gst_paise"] for row in schedule)
    total_paid = sum(row["payment_paise"] for row in schedule)
    assert res["total_finance_charge"]["paise"] == total_fc
    assert res["total_gst"]["paise"] == total_gst
    assert res["cost_of_carrying"]["paise"] == total_fc + total_gst
    assert res["total_paid"]["paise"] == total_paid
    assert total_paid == balance_paise + total_fc + total_gst


def test_card_payoff_three_month_minimum_due_hand_computed():
    # Balance ₹500 (50,000 paise), 36% p.a. (3,600 bps -> 3%/month), GST 18% (1,800 bps),
    # default minimum due: max(ceil(5% of statement), ₹200 = 20,000 paise), start 2026-01-15.
    # Month 1 (2026-02-15):
    #   opening = 50,000
    #   finance_charge = round_half_up(50,000 * 3,600 / 120,000) = 1,500
    #   gst = round_half_up(1,500 * 1,800 / 10,000) = 270
    #   statement = 50,000 + 1,500 + 270 = 51,770
    #   pct_due = ceil(51,770 * 500 / 10,000) = ceil(2,588.5) = 2,589
    #   payment = min(max(2,589, 20,000), 51,770) = 20,000
    #   closing = 51,770 - 20,000 = 31,770
    # Month 2 (2026-03-15):
    #   opening = 31,770
    #   finance_charge = round_half_up(31,770 * 3,600 / 120,000) = round_half_up(953.1) = 953
    #   gst = round_half_up(953 * 1,800 / 10,000) = round_half_up(171.54) = 172
    #   statement = 31,770 + 953 + 172 = 32,895
    #   pct_due = ceil(32,895 * 500 / 10,000) = ceil(1,644.75) = 1,645
    #   payment = min(max(1,645, 20,000), 32,895) = 20,000
    #   closing = 32,895 - 20,000 = 12,895
    # Month 3 (2026-04-15):
    #   opening = 12,895
    #   finance_charge = round_half_up(12,895 * 3,600 / 120,000) = round_half_up(386.85) = 387
    #   gst = round_half_up(387 * 1,800 / 10,000) = round_half_up(69.66) = 70
    #   statement = 12,895 + 387 + 70 = 13,352
    #   pct_due = ceil(13,352 * 500 / 10,000) = ceil(667.6) = 668
    #   payment = min(max(668, 20,000), 13,352) = 13,352
    #   closing = 13,352 - 13,352 = 0
    # Totals: fc = 2,840; gst = 512; carrying cost = 3,352; total paid = 53,352.
    res = card_payoff(
        50_000,
        annual_rate_bps=3600,
        payment="minimum",
        start_date=date(2026, 1, 15),
    )
    _assert_card_payoff_invariants(50_000, res)
    assert res["months"] == 3
    assert res["payoff_date"] == "2026-04-15"
    assert res["schedule"] == [
        {
            "n": 1,
            "date": "2026-02-15",
            "opening_paise": 50_000,
            "finance_charge_paise": 1_500,
            "gst_paise": 270,
            "payment_paise": 20_000,
            "closing_paise": 31_770,
        },
        {
            "n": 2,
            "date": "2026-03-15",
            "opening_paise": 31_770,
            "finance_charge_paise": 953,
            "gst_paise": 172,
            "payment_paise": 20_000,
            "closing_paise": 12_895,
        },
        {
            "n": 3,
            "date": "2026-04-15",
            "opening_paise": 12_895,
            "finance_charge_paise": 387,
            "gst_paise": 70,
            "payment_paise": 13_352,
            "closing_paise": 0,
        },
    ]
    assert res["total_finance_charge"]["paise"] == 2_840
    assert res["total_gst"]["paise"] == 512
    assert res["cost_of_carrying"]["paise"] == 3_352
    assert res["total_paid"]["paise"] == 53_352
    assert any("minimum due assumed" in note for note in res["assumptions"])
    assert any("monthly approximation" in note for note in res["assumptions"])
    assert any("no interest-free period" in note for note in res["assumptions"])


def test_card_payoff_fixed_payment_and_floor_vs_percentage():
    # Fixed payment example: ₹1,000 (100,000 paise) at 36% p.a., fixed ₹600/month (60,000 paise):
    # Month 1: fc = 3,000, gst = 540, stmt = 103,540, pay = 60,000, close = 43,540
    # Month 2: fc = 1,306, gst = 235, stmt = 45,081, pay = 45,081, close = 0
    fixed = card_payoff(100_000, annual_rate_bps=3600, payment=60_000)
    _assert_card_payoff_invariants(100_000, fixed)
    assert fixed["months"] == 2
    assert fixed["payoff_date"] is None
    assert fixed["schedule"][0]["date"] is None
    assert fixed["total_finance_charge"]["paise"] == 4_306
    assert fixed["total_gst"]["paise"] == 775
    assert fixed["total_paid"]["paise"] == 105_081
    assert not any("minimum due assumed" in note for note in fixed["assumptions"])

    # Larger balance where 5% minimum due beats the ₹200 floor initially, then floor finishes it:
    # ₹25,000 (2,500,000 paise) at 42% p.a. (4,200 bps).
    revolving = card_payoff(
        2_500_000,
        annual_rate_bps=4200,
        payment="minimum",
        minimum_bps=500,
        minimum_floor_paise=20_000,
    )
    _assert_card_payoff_invariants(2_500_000, revolving)
    assert revolving["months"] < 1_200
    # Month 1 pays 5% of statement (> 20,000 floor); penultimate month pays 20,000 floor.
    assert revolving["schedule"][0]["payment_paise"] > 20_000
    assert revolving["schedule"][-2]["payment_paise"] == 20_000
    assert not any("assumed" in note for note in revolving["assumptions"])


def test_card_payoff_zero_rate_edges_and_errors():
    # Zero rate divides the balance cleanly with zero carrying cost.
    zero = card_payoff(50_000, annual_rate_bps=0, payment=20_000)
    _assert_card_payoff_invariants(50_000, zero)
    assert [row["payment_paise"] for row in zero["schedule"]] == [20_000, 20_000, 10_000]
    assert zero["cost_of_carrying"]["paise"] == 0
    assert zero["total_paid"]["paise"] == 50_000

    # 1 paisa balance
    tiny = card_payoff(1, annual_rate_bps=3600, payment="minimum")
    _assert_card_payoff_invariants(1, tiny)
    assert tiny["months"] == 1
    assert tiny["total_paid"]["paise"] == 1

    # The signed 64-bit balance the kernel holds is the largest amount money accepts.
    with pytest.raises(ValueError, match="supported maximum"):
        card_payoff(MAX_PAISE + 1, annual_rate_bps=3600, payment=MAX_PAISE // 2)

    # Errors
    with pytest.raises(ValueError, match="balance must be positive"):
        card_payoff(0, annual_rate_bps=3600, payment="minimum")
    with pytest.raises(ValueError, match="balance must be positive"):
        card_payoff(-100, annual_rate_bps=3600, payment="minimum")
    with pytest.raises(ValueError, match="negative"):
        card_payoff(10_000, annual_rate_bps=-1, payment="minimum")
    with pytest.raises(ValueError, match="negative"):
        card_payoff(10_000, annual_rate_bps=3600, payment="minimum", gst_bps=-1)
    with pytest.raises(ValueError, match="minimum_bps"):
        card_payoff(10_000, annual_rate_bps=3600, payment="minimum", minimum_bps=0)
    with pytest.raises(ValueError, match="minimum_floor_paise"):
        card_payoff(10_000, annual_rate_bps=3600, payment="minimum", minimum_floor_paise=0)
    # Never-repaying fixed payment (<= first month's finance charge + GST: 3,000 + 540 = 3,540)
    with pytest.raises(ValueError, match="never clears"):
        card_payoff(100_000, annual_rate_bps=3600, payment=3_540)
    with pytest.raises(ValueError, match="never clears"):
        card_payoff(100_000, annual_rate_bps=3600, payment=0)
    # A minimum due at or below the month's charges can never shrink the balance.
    with pytest.raises(ValueError, match="never falls"):
        card_payoff(
            1_000_000,
            annual_rate_bps=7200,
            payment="minimum",
            minimum_bps=100,
            minimum_floor_paise=20_000,
        )
    # A balance that shrinks too slowly hits the same 600-month limit as a stated loan tenure.
    with pytest.raises(ValueError, match="600"):
        card_payoff(50_000_000, annual_rate_bps=4200, payment="minimum", minimum_bps=400)


def test_card_payoff_property_conservation_and_termination():
    rng = random.Random(20261115)
    for _ in range(100):
        balance = rng.randint(1, 10_000_000)
        rate_bps = rng.choice([0, rng.randint(100, 4800)])
        gst_bps = rng.choice([0, 1800])
        if rng.random() < 0.5:
            res = card_payoff(balance, annual_rate_bps=rate_bps, payment="minimum", gst_bps=gst_bps)
        else:
            first_fc = (2 * balance * rate_bps + 120_000) // (2 * 120_000)
            first_gst = (2 * first_fc * gst_bps + 10_000) // (2 * 10_000)
            min_pay = first_fc + first_gst + max(balance // 24, 1)
            fixed_pay = rng.randint(min_pay, min_pay + balance)
            res = card_payoff(balance, annual_rate_bps=rate_bps, payment=fixed_pay, gst_bps=gst_bps)
        _assert_card_payoff_invariants(balance, res)
        assert res["months"] <= 1_200


def test_money_refuses_amounts_and_tenures_beyond_the_engine_limits():
    with pytest.raises(ValueError, match="supported maximum"):
        amortize(MAX_PAISE + 1, 10, 12)
    with pytest.raises(ValueError, match="supported maximum"):
        amortize(100_000, 10, 12, processing_fee_paise=MAX_PAISE + 1)
    with pytest.raises(ValueError, match="supported maximum"):
        infer_rate(MAX_PAISE + 1, 12, 1_000)
    with pytest.raises(ValueError, match="supported maximum"):
        card_emi(MAX_PAISE + 1, annual_rate_bps=0, months=2)
    with pytest.raises(ValueError, match="supported maximum"):
        compound(MAX_PAISE + 1, 10, 12)
    with pytest.raises(ValueError, match="supported maximum"):
        compare_offers(
            [
                {"label": "Cash", "cash_price_paise": 10_000},
                {"label": "Pricier", "cash_price_paise": MAX_PAISE + 1},
            ]
        )
    with pytest.raises(ValueError, match="between 1 and 600"):
        amortize(100_000, 10, 601)
    with pytest.raises(ValueError, match="between 1 and 600"):
        infer_rate(100_000, 601, 10_000)
    with pytest.raises(ValueError, match="between 0 and 600"):
        compound(100_000, 10, 601)
    # 600 months is allowed; rounding may clear the loan one month early.
    assert 0 < amortize(100_000, 10, 600)["months"] <= 600


def test_an_explicit_emi_that_cannot_finish_inside_six_hundred_months_is_refused():
    # ₹10,000 at 8% with a ₹67 EMI first pays ₹66.67 of interest, so it would need 799 months.
    with pytest.raises(ValueError, match="600 months"):
        amortize(1_000_000, 8.0, 120, emi_paise=6_700)
    # Just inside the cap still completes and reports its real tenure.
    result = amortize(1_000_000, 8.0, 120, emi_paise=6_800)
    assert result["months"] == 592
    assert sum(row["principal_paise"] for row in result["schedule"]) == 1_000_000


def test_decimal_math_is_pinned_against_the_callers_context():
    baseline = amortize(1_000_000_00, 8.5, 240)
    fit = _compute_apr(1_960_000, [96_973] * 24)
    with localcontext() as ctx:
        ctx.prec = 5
        ctx.rounding = ROUND_DOWN
        assert amortize(1_000_000_00, 8.5, 240) == baseline
        assert _compute_apr(1_960_000, [96_973] * 24) == fit


def test_format_paise_rounds_half_up_instead_of_truncating():
    assert format_paise(Decimal("123.4")) == "₹1.23"
    assert format_paise(Decimal("123.5")) == "₹1.24"
    assert format_paise(Decimal("-123.5")) == "-₹1.24"
    assert format_paise(123) == "₹1.23"
