"""Backtest and HackerRank scorer tests, with no real or licensed data in the repository."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from fin_agent.engine import backtest, hackerrank


def test_synthetic_history_is_deterministic_and_has_twelve_month_starts():
    one = backtest.generate_synthetic_history()
    two = backtest.generate_synthetic_history()
    assert one.balances == two.balances
    assert len(one.month_starts) == 12
    assert one.month_starts[0] < one.month_starts[-1]
    assert one.balances[one.month_starts[0]] > 0


def test_synthetic_backtest_reports_error_and_band_calibration():
    result = backtest.backtest_synthetic(runs=100)
    assert result["source"] == "synthetic 12-month history"
    assert result["months"] == 12
    assert result["median_absolute_error_paise"] is not None
    assert result["median_absolute_error_paise"] >= 0
    assert 0.0 <= result["band_calibration"] <= 1.0
    assert all("in_band" in row for row in result["rows"])
    report = backtest.format_report(result)
    assert "synthetic 12-month history" in report
    assert "P10-P90" in report


def test_db_backtest_refuses_without_a_twelve_month_backfill(demo_ledger):
    with pytest.raises(ValueError, match="12 months"):
        backtest.backtest(demo_ledger, source="db", runs=20)


def test_auto_backtest_falls_back_to_synthetic_when_the_ledger_is_short(demo_ledger):
    result = backtest.backtest(demo_ledger, source="auto", runs=50)
    assert result["source"] == "synthetic 12-month history"
    assert result["months"] == 12


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _sample_dataset(root: Path) -> Path:
    _write_csv(
        root / "financial_profiles.csv",
        [
            "user_id",
            "home_currency",
            "current_available_balance",
            "minimum_balance_to_keep",
            "financial_priorities",
            "expense_categories_to_protect",
            "expense_categories_user_is_willing_to_reduce",
            "expense_categories_user_is_willing_to_stop",
            "payment_methods_user_will_consider",
            "max_installment_months",
        ],
        [
            [
                "user_01",
                "INR",
                "100000",
                "10000",
                "",
                "",
                "",
                "",
                "full_payment",
                "",
            ]
        ],
    )
    _write_csv(
        root / "financial_events.csv",
        [
            "event_id",
            "user_id",
            "event_type",
            "description",
            "category",
            "direction",
            "amount",
            "currency",
            "event_date",
            "settlement_date",
            "status",
            "linked_event_id",
            "flexibility",
            "minimum_allowed_amount",
        ],
        [],
    )
    _write_csv(
        root / "request_payment_options.csv",
        [
            "payment_option_id",
            "request_id",
            "payment_method",
            "payment_amount",
            "number_of_payments",
            "first_payment_date",
            "payment_frequency_days",
            "financing_fee",
            "total_payable_amount",
        ],
        [],
    )
    _write_csv(
        root / "sample_requests.csv",
        [
            "request_id",
            "user_id",
            "request_date",
            "request_type",
            "requested_amount",
            "desired_completion_date",
            "allows_partial_payment",
            "request_text",
            "amount_safe_to_pay",
            "affordability_status",
            "recommended_payment_method",
            "payment_plan",
            "earliest_date_for_full_payment",
            "spending_changes_needed",
            "decision_explanation",
        ],
        [
            [
                "request_01",
                "user_01",
                "2026-01-01",
                "purchase",
                "5000",
                "2026-01-10",
                "true",
                "Can I buy this?",
                "5000",
                "affordable_now",
                "full_payment",
                "2026-01-01:5000",
                "2026-01-01",
                "none",
                "Safe.",
            ]
        ],
    )
    return root


def test_hackerrank_scorer_reads_a_local_dataset_and_scores_it(tmp_path, monkeypatch):
    dataset = _sample_dataset(tmp_path)
    monkeypatch.setenv(hackerrank.DATASET_ENV, str(dataset))
    assert hackerrank.find_dataset() == dataset
    result = hackerrank.score(runs=30)
    assert result["available"] is True
    assert result["samples"] == 1
    assert result["score"] == 1.0
    assert result["status_accuracy"] == 1.0
    assert "HackerRank" in hackerrank.format_report(result)


def test_hackerrank_scorer_says_plainly_when_the_dataset_is_missing(monkeypatch):
    monkeypatch.delenv(hackerrank.DATASET_ENV, raising=False)
    monkeypatch.setattr(hackerrank, "find_dataset", lambda: None)
    result = hackerrank.score(runs=10)
    assert result["available"] is False
    assert result["score"] is None
    assert "not on this machine" in result["reason"]
    assert "not run" in hackerrank.format_report(result)


def test_hackerrank_dataset_is_never_committed():
    root = Path(__file__).resolve().parents[1]
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    assert "private/" in ignored
    assert not (root / "dataset" / "sample_requests.csv").exists()
