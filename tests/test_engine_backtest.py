"""Backtest and HackerRank scorer tests, with no real or licensed data in the repository."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from arno.engine import backtest, hackerrank


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


async def test_db_backtest_refuses_without_a_twelve_month_backfill(demo_ledger):
    with pytest.raises(ValueError, match="12 months"):
        await backtest.backtest(demo_ledger, source="db", runs=20)


async def test_auto_backtest_falls_back_to_synthetic_when_the_ledger_is_short(demo_ledger):
    result = await backtest.backtest(demo_ledger, source="auto", runs=50)
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
    result = hackerrank.score()
    assert result["available"] is True
    assert result["samples"] == 1
    assert result["score"] == 1.0
    assert result["status_accuracy"] == 1.0
    assert "HackerRank" in hackerrank.format_report(result)


def test_hackerrank_scorer_says_plainly_when_the_dataset_is_missing(monkeypatch):
    monkeypatch.delenv(hackerrank.DATASET_ENV, raising=False)
    monkeypatch.setattr(hackerrank, "find_dataset", lambda: None)
    result = hackerrank.score()
    assert result["available"] is False
    assert result["score"] is None
    assert "not on this machine" in result["reason"]
    assert "not run" in hackerrank.format_report(result)


def test_hackerrank_dataset_is_never_committed():
    root = Path(__file__).resolve().parents[1]
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    assert "private/" in ignored
    assert not (root / "dataset" / "sample_requests.csv").exists()


PROFILE_FIELDS = [
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
]
EVENT_FIELDS = [
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
]
OPTION_FIELDS = [
    "payment_option_id",
    "request_id",
    "payment_method",
    "payment_amount",
    "number_of_payments",
    "first_payment_date",
    "payment_frequency_days",
    "financing_fee",
    "total_payable_amount",
]
SAMPLE_FIELDS = [
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
]


def _profile_row(**overrides: str) -> list[str]:
    row = {field: "" for field in PROFILE_FIELDS}
    row.update(
        {
            "user_id": "user_01",
            "home_currency": "INR",
            "current_available_balance": "100000",
            "minimum_balance_to_keep": "10000",
            "payment_methods_user_will_consider": "full_payment",
        }
    )
    row.update({key: str(value) for key, value in overrides.items()})
    return [row[field] for field in PROFILE_FIELDS]


def _event_row(
    event_id: str,
    *,
    direction: str,
    amount: str,
    event_date: str,
    status: str = "settled",
    category: str = "expense",
    event_type: str = "expense",
    settlement_date: str = "",
    description: str = "",
    currency: str = "INR",
    flexibility: str = "fixed",
    minimum_allowed_amount: str = "",
) -> list[str]:
    row = {
        "event_id": event_id,
        "user_id": "user_01",
        "event_type": event_type,
        "description": description or category,
        "category": category,
        "direction": direction,
        "amount": amount,
        "currency": currency,
        "event_date": event_date,
        "settlement_date": settlement_date or event_date,
        "status": status,
        "linked_event_id": "",
        "flexibility": flexibility,
        "minimum_allowed_amount": minimum_allowed_amount,
    }
    return [row[field] for field in EVENT_FIELDS]


def _sample_row(
    *,
    request_date: str,
    requested_amount: str,
    amount_safe_to_pay: str,
    affordability_status: str,
    recommended_payment_method: str,
    earliest_date_for_full_payment: str = "",
    desired_completion_date: str = "",
    allows_partial_payment: str = "false",
) -> list[str]:
    row = {
        "request_id": "request_01",
        "user_id": "user_01",
        "request_date": request_date,
        "request_type": "purchase",
        "requested_amount": requested_amount,
        "desired_completion_date": desired_completion_date,
        "allows_partial_payment": allows_partial_payment,
        "request_text": "Can I afford this?",
        "amount_safe_to_pay": amount_safe_to_pay,
        "affordability_status": affordability_status,
        "recommended_payment_method": recommended_payment_method,
        "payment_plan": "none",
        "earliest_date_for_full_payment": earliest_date_for_full_payment,
        "spending_changes_needed": "none",
        "decision_explanation": "Synthetic.",
    }
    return [row[field] for field in SAMPLE_FIELDS]


def _scenario(
    root: Path,
    *,
    profile: list[str],
    events: list[list[str]],
    sample: list[str],
    messages: list[list[str]] | None = None,
    images: list[list[str]] | None = None,
    rates: list[list[str]] | None = None,
    image_amounts: dict[str, float] | None = None,
) -> Path:
    _write_csv(root / "financial_profiles.csv", PROFILE_FIELDS, [profile])
    _write_csv(root / "financial_events.csv", EVENT_FIELDS, events)
    _write_csv(root / "sample_requests.csv", SAMPLE_FIELDS, [sample])
    if messages is not None:
        _write_csv(root / "messages.csv", ["message_id", "user_id", "message_text"], messages)
    if images is not None:
        _write_csv(root / "images.csv", ["image_id", "user_id", "related_event_id"], images)
    if rates is not None:
        _write_csv(
            root / "exchange_rates.csv",
            ["rate_date", "from_currency", "to_currency", "rate"],
            rates,
        )
    if image_amounts is not None:
        (root / "image_amounts.json").write_text(json.dumps(image_amounts), encoding="utf-8")
    return root


def test_hackerrank_converts_foreign_currency_with_direct_and_inverse_rates():
    from arno.engine.hackerrank import _convert_paise, _Rate

    rates = (_Rate(date(2026, 1, 1), "USD", "INR", 80.0),)
    assert _convert_paise(8_000, "USD", "INR", date(2026, 2, 1), rates) == 640_000
    assert _convert_paise(8_000, "INR", "USD", date(2026, 2, 1), rates) == 100


def test_hackerrank_report_keeps_an_in_repo_dataset_path_relative():
    from arno.engine.hackerrank import _display_dataset

    inside = hackerrank.REPO_ROOT / "private" / "demo-dataset"
    assert _display_dataset(inside) == "private/demo-dataset"


def test_hackerrank_converts_a_scheduled_foreign_salary_at_the_settlement_rate(tmp_path):
    dataset = _scenario(
        tmp_path,
        profile=_profile_row(current_available_balance="50000"),
        events=[
            _event_row(
                "event_1",
                direction="credit",
                amount="1000",
                currency="USD",
                category="salary",
                event_type="income",
                event_date="2026-03-01",
                status="scheduled",
            ),
            _event_row(
                "event_2",
                direction="debit",
                amount="100000",
                category="housing",
                event_date="2026-03-15",
                status="scheduled",
            ),
        ],
        sample=_sample_row(
            request_date="2026-02-15",
            requested_amount="40000",
            amount_safe_to_pay="30000",
            affordability_status="not_affordable",
            recommended_payment_method="not_recommended",
        ),
        rates=[
            ["2026-01-01", "USD", "INR", "80"],
            ["2026-02-01", "USD", "INR", "90"],
        ],
    )
    result = hackerrank.score(dataset)
    row = result["rows"][0]
    assert row["amount_safe_to_pay"] == 30_000
    assert row["affordability_status"] == "not_affordable"


def test_hackerrank_fills_a_blank_event_amount_from_the_local_image_map(tmp_path):
    dataset = _scenario(
        tmp_path,
        profile=_profile_row(current_available_balance="100000"),
        events=[
            _event_row(
                "event_2",
                direction="debit",
                amount="",
                category="utilities",
                event_date="2026-02-06",
                settlement_date="2026-02-09",
                status="pending",
            )
        ],
        sample=_sample_row(
            request_date="2026-02-01",
            requested_amount="86000",
            amount_safe_to_pay="85000",
            affordability_status="not_affordable",
            recommended_payment_method="not_recommended",
        ),
        images=[["image_01", "user_01", "event_2"]],
        image_amounts={"event_2": 5_000},
    )
    result = hackerrank.score(dataset)
    row = result["rows"][0]
    assert row["amount_safe_to_pay"] == 85_000
    assert row["affordability_status"] == "not_affordable"


def test_hackerrank_applies_a_salary_change_from_a_message(tmp_path):
    dataset = _scenario(
        tmp_path,
        profile=_profile_row(current_available_balance="50000"),
        events=[
            _event_row(
                "event_1",
                direction="credit",
                amount="10000",
                category="salary",
                event_type="income",
                event_date="2026-01-15",
            ),
            _event_row(
                "event_2",
                direction="credit",
                amount="10000",
                category="salary",
                event_type="income",
                event_date="2026-02-15",
            ),
        ],
        sample=_sample_row(
            request_date="2026-02-20",
            requested_amount="55000",
            amount_safe_to_pay="40000",
            affordability_status="affordable_later",
            recommended_payment_method="wait",
            earliest_date_for_full_payment="2026-03-15",
        ),
        messages=[
            [
                "message_1",
                "user_01",
                "Your monthly salary has increased to INR 20000. "
                "The change applies from 2026-03-15.",
            ]
        ],
    )
    result = hackerrank.score(dataset)
    row = result["rows"][0]
    assert row["amount_safe_to_pay"] == 40_000
    assert row["affordability_status"] == "affordable_later"
    assert row["earliest_date_for_full_payment"] == "2026-03-15"


def test_hackerrank_continues_a_stream_from_one_settled_and_one_scheduled_salary():
    from arno.engine.hackerrank import _events_for

    def salary(event_id: str, day: str, status: str) -> dict[str, str]:
        return {
            "event_id": event_id,
            "status": status,
            "direction": "credit",
            "event_type": "income",
            "category": "salary",
            "event_date": day,
            "settlement_date": day,
            "amount": "30000",
            "currency": "INR",
            "description": "Payroll credit",
        }

    events, _covered, _sources = _events_for(
        [salary("event_1", "2026-01-15", "settled"), salary("event_2", "2026-02-15", "scheduled")],
        date(2026, 2, 1),
    )
    assert [event.date.isoformat() for event in events] == [
        "2026-02-15",
        "2026-03-15",
        "2026-04-15",
    ]


def test_hackerrank_stops_a_flexible_expense_to_make_full_payment_safe(tmp_path):
    dataset = _scenario(
        tmp_path,
        profile=_profile_row(
            current_available_balance="36000",
            expense_categories_user_is_willing_to_stop="streaming",
        ),
        events=[
            _event_row(
                "event_1",
                direction="debit",
                amount="2000",
                category="streaming",
                event_type="subscription",
                event_date="2025-11-10",
                flexibility="stoppable",
            ),
            _event_row(
                "event_2",
                direction="debit",
                amount="2000",
                category="streaming",
                event_type="subscription",
                event_date="2025-12-10",
                flexibility="stoppable",
            ),
            _event_row(
                "event_3",
                direction="debit",
                amount="2000",
                category="streaming",
                event_type="subscription",
                event_date="2026-01-10",
                flexibility="stoppable",
            ),
        ],
        sample=_sample_row(
            request_date="2026-02-01",
            requested_amount="24000",
            amount_safe_to_pay="20000",
            affordability_status="affordable_with_plan",
            recommended_payment_method="full_payment",
        ),
    )
    result = hackerrank.score(dataset)
    row = result["rows"][0]
    assert row["amount_safe_to_pay"] == 20_000
    assert row["affordability_status"] == "affordable_with_plan"
    assert row["recommended_payment_method"] == "full_payment"
    assert row["spending_changes_needed"].startswith("stop:event_")
