"""The tested money maths the agent must call instead of writing formulas."""

from __future__ import annotations

from datetime import date

from fin_agent.engine.money import amortize, compound


def test_amortize_matches_a_known_emi():
    result = amortize(5_000_000_00, 8.5, 240, start_date=date(2026, 1, 1))
    # ₹50 lakh at 8.5% for 20 years is about ₹43,391 a month.
    assert 4_330_000 <= result["emi"]["paise"] <= 4_350_000
    assert result["months"] == 240
    assert result["total_interest"]["paise"] > 0
    assert result["schedule"][0]["date"] == "2026-02-01"
    assert result["schedule"][-1]["balance_paise"] < 1000


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
    try:
        amortize(100_000_00, 60, 24, emi_paise=1_00)
    except ValueError as exc:
        assert "never repays" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected a refusal")


def test_compound_pays_at_the_start_of_each_month():
    result = compound(500_000, 12, 120)
    # ₹5,000 a month at 12% for 10 years is about ₹11.6 lakh.
    assert 115_000_000 <= result["final"]["paise"] <= 117_000_000
    assert result["contributed"]["paise"] == 60_000_000
    assert result["growth"]["paise"] > 0
    assert result["yearly"][-1]["year"] == 10
    assert "assumed" in result["assumptions"][0]
