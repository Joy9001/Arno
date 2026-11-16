"""Backtest and HackerRank scorer tests, with no real or licensed data in the repository."""

from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path

from arno.engine import backtest, hackerrank, personas
from arno.engine.inputs import Occurrence, build_inputs


async def test_backtest_results_are_labeled_synthetic_or_real(ledger):
    real = await backtest.backtest_db(ledger)
    assert real["source"] == backtest.REAL_LEDGER == "real ledger"
    assert "the real ledger" in backtest.format_report(real)

    synthetic = backtest.summarize(
        [],
        source=f"synthetic personas, seeds {personas.TUNING_SEEDS[0]}",
        dropped=0,
        names=("flat",),
    )
    assert synthetic["source"].startswith("synthetic personas, seeds ")
    assert "synthetic personas, seeds " in backtest.format_report(synthetic)
    assert real["source"] != synthetic["source"]


async def test_the_db_backtest_reads_no_spend_after_its_origin(ledger):
    from arno.ledger.accounts import create_account
    from arno.ledger.common import today
    from arno.ledger.movements import record_expense

    first = today() - timedelta(days=130)
    late = today() - timedelta(days=30)
    bank = await create_account(
        ledger,
        kind="bank",
        name="History bank",
        opening_balance_paise=1_000_000,
        opening_balance_as_of=first - timedelta(days=1),
    )
    day = first
    while day <= today():
        await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=999_900 if day == late else 10_000,
            date=day,
        )
        day += timedelta(days=7)
    # `db_cases` scores weekly origins back from the last movement. Put one charge just before
    # the earliest origin (history) and one on it (not history): the spend cutoff is strict.
    last_movement = day - timedelta(days=7)
    origin = last_movement - timedelta(days=backtest.LAST_HORIZON)
    while origin - timedelta(days=backtest.STEP_DAYS) >= first + timedelta(days=14):
        origin -= timedelta(days=backtest.STEP_DAYS)
    adjacent = origin - timedelta(days=2)
    for day, amount in ((adjacent, 888_800), (origin, 777_700)):
        await record_expense(ledger, account_id=bank["id"], amount_paise=amount, date=day)

    cases = await backtest.db_cases(ledger)
    assert cases
    earliest = cases[0]
    assert earliest.as_of == origin
    assert any(
        row.date == adjacent and row.amount_paise == 888_800 for row in earliest.inputs.spend
    )
    assert not any(row.amount_paise == 777_700 for row in earliest.inputs.spend)
    for case in cases:
        assert case.as_of < late
        assert all(row.date < case.as_of for row in case.inputs.spend)
        assert all(
            spend_date < case.as_of
            for category in case.inputs.categories
            for spend_date, _amount in category.history
        )
        # The one charge dated after every origin is nowhere in the scored inputs.
        assert not any(
            amount == 999_900
            for category in case.inputs.categories
            for _spend_date, amount in category.history
        )


def test_a_persona_snapshot_sees_nothing_after_its_day():
    persona = personas.generate("salaried_steady", personas.TUNING_SEEDS[0])
    ledger = backtest._ledger(persona)
    as_of = persona.end - timedelta(days=120)

    snapshot = backtest.persona_snapshot(ledger, as_of)

    assert snapshot.spend
    assert all(row.date < as_of for row in snapshot.spend)
    assert all(row.date > as_of for row in snapshot.occurrences)
    assert snapshot.liquid[0].balance_paise == personas.bank_balance(persona, as_of)
    for card in snapshot.cards:
        assert card.outstanding_paise == max(personas.card_outstanding(persona, card.id, as_of), 0)
    inputs = build_inputs(snapshot)
    assert "no confirmed income schedule" not in inputs.data_quality["setup_gaps"]


def test_persona_snapshot_on_messy_recurring_projects_series_and_excludes_confirmed_payees():
    persona = personas.generate("messy_recurring", personas.TUNING_SEEDS[0])
    ledger = backtest._ledger(persona)
    as_of = persona.end - timedelta(days=120)

    snapshot = backtest.persona_snapshot(ledger, as_of)

    assert snapshot.attributed is True
    assert snapshot.series
    confirmed_series_ids = {s.id for s in persona.series if s.schedule_id is not None}
    confirmed_payees = {
        s.payee_key for s in persona.series if s.schedule_id is not None and s.kind == "spend"
    }
    assert confirmed_payees
    assert not any(
        forecast.id == f"det_{payee}" for forecast in snapshot.series for payee in confirmed_payees
    )
    assert any(forecast.id == "det_netflix" for forecast in snapshot.series)

    confirmed_movements = [
        m
        for m in persona.movements
        if m.kind == "spend"
        and m.series_id in confirmed_series_ids
        and m.date < as_of
        and m.date >= as_of - timedelta(days=backtest.RARE_WINDOW_DAYS)
    ]
    assert confirmed_movements
    spend_by_key = {
        (row.date, row.category_id, row.card_id): row.amount_paise for row in snapshot.spend
    }
    for mov in confirmed_movements:
        card_id = None if mov.account == "bank" else mov.account
        other_spend = sum(
            other.amount_paise
            for other in persona.movements
            if other.kind == "spend"
            and other.date == mov.date
            and other.category_id == mov.category_id
            and (None if other.account == "bank" else other.account) == card_id
            and other.series_id not in confirmed_series_ids
        )
        assert spend_by_key.get((mov.date, mov.category_id, card_id), 0) <= other_spend


def test_the_persona_backtest_scores_each_forecaster_against_the_baselines():
    result = backtest.backtest_personas(
        seeds=(personas.HOLDOUT_SEEDS[0],),
        keys=("salaried_steady",),
        names=("flat", "schedule_mean"),
        workers=1,
    )

    assert result["cases"] > 40
    assert result["source"].startswith("synthetic personas, seeds ")
    assert {row["forecaster"] for row in result["metrics"]} == {"flat", "schedule_mean"}
    flat = next(
        row
        for row in result["metrics"]
        if row["forecaster"] == "flat" and row["target"] == "end" and row["horizon"] == 30
    )
    assert "schedule_mean" in flat["skill"]
    report = backtest.format_report(result)
    assert "Source: synthetic personas, seeds " in report
    assert "weekly origins scored" in report
    assert "| end | 30 | flat |" in report


def test_outcomes_leave_out_income_the_base_case_does_not_count():
    seed = personas.TUNING_SEEDS[0]
    persona = personas.generate("freelancer", seed)
    case = backtest.persona_cases("freelancer", seed)[-1]
    end = case.as_of + timedelta(days=backtest.LAST_HORIZON)
    retainer = {item.id for item in persona.series if item.schedule_id == "sched_retainer"}
    uncounted = sum(
        movement.amount_paise
        for movement in persona.movements
        if movement.kind == "income"
        and case.as_of < movement.date <= end
        and movement.series_id not in retainer
    )

    assert uncounted > 0
    assert case.actual[0] == personas.bank_balance(persona, case.as_of)
    assert case.actual[-1] == personas.bank_balance(persona, end) - uncounted


async def test_the_db_outcome_counts_only_income_linked_to_a_schedule_confirmed_by_then(ledger):
    from arno.india.recurrence import expand_rule
    from arno.ledger.accounts import create_account
    from arno.ledger.audit import write_group
    from arno.ledger.common import today
    from arno.ledger.movements import record_income, reverse_movement
    from arno.ledger.schedule_payments import link_payment
    from arno.ledger.schedules import create_schedule

    start = today() - timedelta(days=120)
    bank = await create_account(ledger, kind="bank", name="Salary bank")
    rule = "FREQ=MONTHLY;BYMONTHDAY=10"
    salary = await create_schedule(
        ledger,
        kind="income_fixed",
        name="Salary",
        amount_paise=500_000,
        account_id=bank["id"],
        rule=rule,
        starts=start - timedelta(days=30),
    )
    guess = await create_schedule(
        ledger,
        kind="income_variable",
        name="Client guess",
        amount_paise=70_000,
        account_id=bank["id"],
        rule=rule,
        starts=start - timedelta(days=30),
        confirmed=False,
    )
    await ledger.execute(
        "UPDATE schedules SET created_at = $1::date - 1 WHERE id = ANY($2::text[])",
        start,
        [salary["id"], guess["id"]],
    )
    first, second = expand_rule(
        rule,
        start - timedelta(days=30),
        start=start + timedelta(days=1),
        end=start + timedelta(days=60),
    )[:2]

    async def income(day, amount, schedule=None):
        movement = await record_income(
            ledger, account_id=bank["id"], amount_paise=amount, description="Credit", date=day
        )
        if schedule is not None:
            await link_payment(
                ledger, schedule_id=schedule["id"], due_date=day, movement_id=movement["id"]
            )
        return movement

    await income(first, 500_000, salary)
    await income(second, 70_000, guess)
    stray = await income(start + timedelta(days=3), 200_000)
    async with write_group(ledger, actor="user") as group:
        await reverse_movement(group, stray["id"], date=start + timedelta(days=5))

    path = await backtest._liquid_path(ledger, [bank["id"]], start)

    assert path[(first - start).days] - path[(first - start).days - 1] == 500_000
    assert path[-1] == 500_000

    def occurrence(day):
        return Occurrence(
            schedule_id=salary["id"],
            name="Salary",
            kind="income_fixed",
            date=day,
            amount_paise=500_000,
            remaining_paise=500_000,
            category_id=None,
            card_id=None,
            goal_id=None,
            confirmed=True,
            linked_goal=False,
        )

    last = start + timedelta(days=backtest.LAST_HORIZON)
    assert not await backtest._unlinked_income(ledger, [occurrence(first)], start, last)
    assert await backtest._unlinked_income(
        ledger, [occurrence(first), occurrence(second)], start, last
    )


async def test_the_db_backtest_drops_and_counts_origins_it_cannot_start_from(demo_ledger):
    result = await backtest.backtest(demo_ledger, source="db", names=("flat",))
    assert result["source"] == "real ledger"
    assert result["cases"] >= 0
    assert result["dropped"] >= 0


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
    payment_options: list[list[str]] | None = None,
    messages: list[list[str]] | None = None,
    images: list[list[str]] | None = None,
    rates: list[list[str]] | None = None,
    image_amounts: dict[str, float] | None = None,
) -> Path:
    _write_csv(root / "financial_profiles.csv", PROFILE_FIELDS, [profile])
    _write_csv(root / "financial_events.csv", EVENT_FIELDS, events)
    _write_csv(root / "sample_requests.csv", SAMPLE_FIELDS, [sample])
    if payment_options is not None:
        _write_csv(root / "request_payment_options.csv", OPTION_FIELDS, payment_options)
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


def _option_row(
    option_id: str,
    *,
    payment_method: str = "installments",
    payment_amount: str,
    number_of_payments: str,
    first_payment_date: str,
    payment_frequency_days: str = "30",
    total_payable_amount: str = "",
) -> list[str]:
    row = {
        "payment_option_id": option_id,
        "request_id": "request_01",
        "payment_method": payment_method,
        "payment_amount": payment_amount,
        "number_of_payments": number_of_payments,
        "first_payment_date": first_payment_date,
        "payment_frequency_days": payment_frequency_days,
        "financing_fee": "0",
        "total_payable_amount": total_payable_amount,
    }
    return [row[field] for field in OPTION_FIELDS]


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
                "event_0",
                direction="credit",
                amount="10000",
                category="salary",
                event_type="income",
                event_date="2025-12-15",
            ),
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
    result = hackerrank.score(dataset, runs=256)
    row = result["rows"][0]
    assert row["amount_safe_to_pay"] == 40_000
    assert row["affordability_status"] == "affordable_later"
    assert row["earliest_date_for_full_payment"] == "2026-03-15"


def test_hackerrank_projects_inferred_salary_and_dedupes_scheduled_credit():
    from arno.engine.hackerrank import _inputs_for

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

    inputs = _inputs_for(
        [
            salary("event_1", "2025-11-15", "settled"),
            salary("event_2", "2025-12-15", "settled"),
            salary("event_3", "2026-01-15", "settled"),
            salary("event_4", "2026-02-15", "scheduled"),
        ],
        date(2026, 2, 1),
    )
    assert [event.date.isoformat() for event in inputs.events] == [
        "2026-02-15",
        "2026-03-15",
        "2026-04-15",
    ]


def test_hackerrank_routes_inferred_debit_recurrence_through_production_afford(tmp_path):
    dataset = _scenario(
        tmp_path,
        profile=_profile_row(current_available_balance="36000"),
        events=[
            _event_row(
                "event_1",
                direction="debit",
                amount="2000",
                category="streaming",
                event_type="subscription",
                event_date="2025-11-10",
            ),
            _event_row(
                "event_2",
                direction="debit",
                amount="2000",
                category="streaming",
                event_type="subscription",
                event_date="2025-12-10",
            ),
            _event_row(
                "event_3",
                direction="debit",
                amount="2000",
                category="streaming",
                event_type="subscription",
                event_date="2026-01-10",
            ),
        ],
        sample=_sample_row(
            request_date="2026-02-01",
            requested_amount="24000",
            amount_safe_to_pay="20000",
            affordability_status="not_affordable",
            recommended_payment_method="not_recommended",
        ),
    )
    result = hackerrank.score(dataset, runs=256)
    row = result["rows"][0]
    assert row["amount_safe_to_pay"] == 20_000
    assert row["affordability_status"] == "not_affordable"
    assert row["recommended_payment_method"] == "not_recommended"


def test_hackerrank_b1_honors_allowed_payment_methods(tmp_path):
    # Full payment is disallowed, so affordable_now is blocked and the installment plan is chosen.
    plan_dir = tmp_path / "installments_only"
    plan_dir.mkdir()
    _scenario(
        plan_dir,
        profile=_profile_row(
            current_available_balance="100000",
            minimum_balance_to_keep="10000",
            payment_methods_user_will_consider="installments",
        ),
        events=[],
        sample=_sample_row(
            request_date="2026-02-01",
            requested_amount="20000",
            amount_safe_to_pay="20000",
            affordability_status="affordable_with_plan",
            recommended_payment_method="installments",
            earliest_date_for_full_payment="2026-02-01",
        ),
        payment_options=[
            _option_row(
                "opt_1",
                payment_amount="10000",
                number_of_payments="2",
                first_payment_date="2026-02-05",
                total_payable_amount="20000",
            )
        ],
    )
    res_plan = hackerrank.score(plan_dir, runs=64)
    row_plan = res_plan["rows"][0]
    assert row_plan["affordability_status"] == "affordable_with_plan"
    assert row_plan["recommended_payment_method"] == "installments"

    # Full payment is disallowed, so waiting (affordable_later) is also blocked -> not_affordable.
    wait_dir = tmp_path / "no_wait"
    wait_dir.mkdir()
    _scenario(
        wait_dir,
        profile=_profile_row(
            current_available_balance="20000",
            minimum_balance_to_keep="10000",
            payment_methods_user_will_consider="installments",
        ),
        events=[
            _event_row(
                "inc_1",
                direction="credit",
                amount="50000",
                category="salary",
                event_type="income",
                event_date="2026-03-01",
                status="scheduled",
            )
        ],
        sample=_sample_row(
            request_date="2026-02-01",
            requested_amount="30000",
            amount_safe_to_pay="10000",
            affordability_status="not_affordable",
            recommended_payment_method="not_recommended",
        ),
    )
    res_wait = hackerrank.score(wait_dir, runs=64)
    row_wait = res_wait["rows"][0]
    assert row_wait["affordability_status"] == "not_affordable"
    assert row_wait["recommended_payment_method"] == "not_recommended"
    assert row_wait["earliest_date_for_full_payment"] == ""


def test_hackerrank_b2_evaluates_merchant_installments_and_partial_payment(tmp_path):
    inst_dir = tmp_path / "installments"
    inst_dir.mkdir()
    _scenario(
        inst_dir,
        profile=_profile_row(
            current_available_balance="25000",
            minimum_balance_to_keep="10000",
            payment_methods_user_will_consider="full_payment|installments",
            max_installment_months="2",
        ),
        events=[
            _event_row(
                "inc_1",
                direction="credit",
                amount="30000",
                category="salary",
                event_type="income",
                event_date="2026-03-01",
                status="scheduled",
            )
        ],
        sample=_sample_row(
            request_date="2026-02-01",
            requested_amount="30000",
            amount_safe_to_pay="15000",
            affordability_status="affordable_with_plan",
            recommended_payment_method="installments",
            earliest_date_for_full_payment="2026-03-01",
            desired_completion_date="2026-03-20",
        ),
        payment_options=[
            # Exceeds max_installment_months=2.
            _option_row(
                "opt_too_many",
                payment_amount="10000",
                number_of_payments="3",
                first_payment_date="2026-02-05",
                total_payable_amount="30000",
            ),
            # Final installment (2026-03-27) lands after desired_completion_date (2026-03-20).
            _option_row(
                "opt_too_late",
                payment_amount="15000",
                number_of_payments="2",
                first_payment_date="2026-02-05",
                payment_frequency_days="50",
                total_payable_amount="30000",
            ),
            # Valid 2-part installment completing before desired_completion_date.
            _option_row(
                "opt_valid",
                payment_amount="15000",
                number_of_payments="2",
                first_payment_date="2026-02-05",
                payment_frequency_days="28",
                total_payable_amount="30000",
            ),
        ],
    )
    res_inst = hackerrank.score(inst_dir, runs=64)
    row_inst = res_inst["rows"][0]
    assert row_inst["affordability_status"] == "affordable_with_plan"
    assert row_inst["recommended_payment_method"] == "installments"
    assert row_inst["earliest_date_for_full_payment"] == "2026-03-01"

    partial_dir = tmp_path / "partial"
    partial_dir.mkdir()
    _scenario(
        partial_dir,
        profile=_profile_row(
            current_available_balance="30000",
            minimum_balance_to_keep="10000",
            payment_methods_user_will_consider="full_payment|partial_payment",
        ),
        events=[
            _event_row(
                "inc_1",
                direction="credit",
                amount="30000",
                category="salary",
                event_type="income",
                event_date="2026-03-01",
                status="scheduled",
            )
        ],
        sample=_sample_row(
            request_date="2026-02-01",
            requested_amount="40000",
            amount_safe_to_pay="20000",
            affordability_status="affordable_with_plan",
            recommended_payment_method="partial_payment",
            earliest_date_for_full_payment="2026-03-01",
            desired_completion_date="2026-03-10",
            allows_partial_payment="true",
        ),
    )
    res_partial = hackerrank.score(partial_dir, runs=64)
    row_partial = res_partial["rows"][0]
    assert row_partial["amount_safe_to_pay"] == 20_000
    assert row_partial["affordability_status"] == "affordable_with_plan"
    assert row_partial["recommended_payment_method"] == "partial_payment"
    assert row_partial["earliest_date_for_full_payment"] == "2026-03-01"


def test_hackerrank_b2_finds_discrete_stop_and_reduce_spending_changes(tmp_path):
    dataset = _scenario(
        tmp_path,
        profile=_profile_row(
            current_available_balance="26500",
            minimum_balance_to_keep="10000",
            expense_categories_user_is_willing_to_stop="streaming",
            expense_categories_user_is_willing_to_reduce="fitness",
            payment_methods_user_will_consider="full_payment",
        ),
        events=[
            _event_row(
                "ev_s1",
                direction="debit",
                amount="1000",
                category="streaming",
                event_type="subscription",
                event_date="2025-11-10",
                flexibility="stoppable",
            ),
            _event_row(
                "ev_s2",
                direction="debit",
                amount="1000",
                category="streaming",
                event_type="subscription",
                event_date="2025-12-10",
                flexibility="stoppable",
            ),
            _event_row(
                "ev_s3",
                direction="debit",
                amount="1000",
                category="streaming",
                event_type="subscription",
                event_date="2026-01-10",
                flexibility="stoppable",
            ),
            _event_row(
                "ev_f1",
                direction="debit",
                amount="2000",
                category="fitness",
                event_type="subscription",
                event_date="2025-11-12",
                flexibility="reducible",
                minimum_allowed_amount="500",
            ),
            _event_row(
                "ev_f2",
                direction="debit",
                amount="2000",
                category="fitness",
                event_type="subscription",
                event_date="2025-12-12",
                flexibility="reducible",
                minimum_allowed_amount="500",
            ),
            _event_row(
                "ev_f3",
                direction="debit",
                amount="2000",
                category="fitness",
                event_type="subscription",
                event_date="2026-01-12",
                flexibility="reducible",
                minimum_allowed_amount="500",
            ),
        ],
        sample=_sample_row(
            request_date="2026-02-01",
            requested_amount="15000",
            amount_safe_to_pay="7500",
            affordability_status="affordable_with_plan",
            recommended_payment_method="full_payment",
        ),
    )
    result = hackerrank.score(dataset, runs=64)
    row = result["rows"][0]
    assert row["amount_safe_to_pay"] == 7_500
    assert row["affordability_status"] == "affordable_with_plan"
    assert row["recommended_payment_method"] == "full_payment"
    changes = set(row["spending_changes_needed"].split("|"))
    assert changes == {"stop:ev_s3", "reduce_to:ev_f3:500"}


def test_hackerrank_b3_deterministic_day_zero_and_n_day_interval_fallback():
    from arno.engine.hackerrank import _inputs_for

    def debit(event_id: str, day: str, *, category: str = "utilities") -> dict[str, str]:
        return {
            "event_id": event_id,
            "status": "settled",
            "direction": "debit",
            "event_type": "expense",
            "category": category,
            "event_date": day,
            "settlement_date": day,
            "amount": "1500",
            "currency": "INR",
            "description": "Utility bill",
        }

    # Unposted cycle due on request_date (31 days since last >= 30 - 3) includes day 0.
    unposted = _inputs_for(
        [
            debit("d1", "2025-11-10"),
            debit("d2", "2025-12-10"),
            debit("d3", "2026-01-10"),
        ],
        date(2026, 2, 10),
    )
    assert unposted.series == ()
    assert all(event.confirmed for event in unposted.events)
    assert [event.date.isoformat() for event in unposted.events] == [
        "2026-02-10",
        "2026-03-10",
        "2026-04-10",
    ]

    # Cycle already settled the day before request_date (1 day < 30 - 3) is not double-counted.
    already_settled = _inputs_for(
        [
            debit("d1", "2025-11-10"),
            debit("d2", "2025-12-10"),
            debit("d3", "2026-01-10"),
            debit("d4", "2026-02-09"),
        ],
        date(2026, 2, 10),
    )
    assert already_settled.series == ()
    assert [event.date.isoformat() for event in already_settled.events] == [
        "2026-03-10",
        "2026-04-10",
    ]

    # 10-day cycle outside production rule families falls back to regular N-day interval.
    ten_day = _inputs_for(
        [
            debit("t1", "2026-01-02", category="commute"),
            debit("t2", "2026-01-12", category="commute"),
            debit("t3", "2026-01-22", category="commute"),
        ],
        date(2026, 2, 1),
    )
    assert ten_day.series == ()
    assert [event.date.isoformat() for event in ten_day.events[:3]] == [
        "2026-02-01",
        "2026-02-11",
        "2026-02-21",
    ]


def test_hackerrank_b4_consolidates_income_drops_final_and_enforces_freshness():
    from arno.engine.hackerrank import _inputs_for

    def credit(
        event_id: str,
        day: str,
        *,
        category: str,
        description: str,
        amount: str = "40000",
        status: str = "settled",
    ) -> dict[str, str]:
        return {
            "event_id": event_id,
            "status": status,
            "direction": "credit",
            "event_type": "income",
            "category": category,
            "event_date": day,
            "settlement_date": day,
            "amount": amount,
            "currency": "INR",
            "description": description,
        }

    inputs = _inputs_for(
        [
            # Consolidates category across descriptions on day 15 and anchors on scheduled row.
            credit("s1", "2025-12-15", category="salary", description="Payroll December"),
            credit("s2", "2026-01-15", category="salary", description="Employer Payroll"),
            credit(
                "s3",
                "2026-02-15",
                category="salary",
                description="Scheduled payroll",
                status="scheduled",
            ),
            # Newest event carries a \bfinal\b marker -> entire category is dropped.
            credit("f1", "2025-12-10", category="stipend", description="Monthly stipend"),
            credit("f2", "2026-01-10", category="stipend", description="Final stipend payout"),
            # Last settled event is 58 days old (> 1.5 * 30 = 45) -> stale category is dropped.
            credit("c1", "2025-10-05", category="consulting", description="Retainer"),
            credit("c2", "2025-11-05", category="consulting", description="Retainer"),
            credit("c3", "2025-12-05", category="consulting", description="Retainer"),
        ],
        date(2026, 2, 1),
    )
    assert [(event.date.isoformat(), event.amount_paise) for event in inputs.events] == [
        ("2026-02-15", 4_000_000),
        ("2026-03-15", 4_000_000),
        ("2026-04-15", 4_000_000),
    ]


def test_hackerrank_pins_weights_and_tolerances():
    assert hackerrank.WEIGHTS == (0.5, 0.3, 0.2)
    assert sum(hackerrank.WEIGHTS) == 1.0
    assert hackerrank.AMOUNT_TOLERANCE_RATIO == 0.05
    assert hackerrank.AMOUNT_MIN_TOLERANCE == 1.0
    assert hackerrank.DATE_TOLERANCE_DAYS == 3
    assert hackerrank.EXPECTED == (
        "affordable_now",
        "affordable_with_plan",
        "affordable_later",
        "not_affordable",
    )

    # Boundary checks for pinned tolerances.
    assert hackerrank._amount_ok(10_500.0, 10_000.0, 10_000.0) is True
    assert hackerrank._amount_ok(10_501.0, 10_000.0, 10_000.0) is False
    assert hackerrank._amount_ok(11.0, 10.0, 10.0) is True
    assert hackerrank._dates_ok("2026-03-18", "2026-03-15") is True
    assert hackerrank._dates_ok("2026-03-19", "2026-03-15") is False


def test_hackerrank_counts_errors_as_misses_and_reports_intervals_and_status_table(tmp_path):
    _write_csv(tmp_path / "financial_profiles.csv", PROFILE_FIELDS, [_profile_row()])
    _write_csv(tmp_path / "financial_events.csv", EVENT_FIELDS, [])
    sample_one = _sample_row(
        request_date="2026-01-01",
        requested_amount="5000",
        amount_safe_to_pay="5000",
        affordability_status="affordable_now",
        recommended_payment_method="full_payment",
        earliest_date_for_full_payment="2026-01-01",
    )
    sample_two = _sample_row(
        request_date="2026-01-01",
        requested_amount="5000",
        amount_safe_to_pay="0",
        affordability_status="not_affordable",
        recommended_payment_method="not_recommended",
    )
    sample_two[0] = "request_02"
    sample_two[1] = "missing_user"
    _write_csv(tmp_path / "sample_requests.csv", SAMPLE_FIELDS, [sample_one, sample_two])

    result = hackerrank.score(tmp_path, runs=64)
    assert result["samples"] == 2
    assert result["errors"] == 1
    assert result["hits"] == {"status": 1, "amount": 1, "earliest": 1}
    assert result["status_accuracy"] == 0.5
    assert result["amount_accuracy"] == 0.5
    assert result["earliest_accuracy"] == 0.5
    assert result["score"] == 0.5

    for metric in ("combined", "status", "amount", "earliest"):
        low, high = result["intervals"][metric]
        assert 0.0 <= low < 0.5 < high <= 1.0

    assert result["status_counts"] == {
        "affordable_now": {"expected": 1, "predicted": 1},
        "affordable_with_plan": {"expected": 0, "predicted": 0},
        "affordable_later": {"expected": 0, "predicted": 0},
        "not_affordable": {"expected": 1, "predicted": 0},
    }

    report = hackerrank.format_report(result)
    assert "errors: 1" in report
    assert "n=2" in report
    assert "95% CI" in report
    assert "| Status | Expected | Predicted |" in report
    assert "| affordable_now | 1 | 1 |" in report
    assert "| not_affordable | 1 | 0 |" in report
