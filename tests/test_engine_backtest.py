"""Backtest tests over synthetic personas and the real ledger, with no licensed data here."""

from __future__ import annotations

from datetime import date, timedelta
from functools import cache

import pytest

from arno.engine import backtest, personas
from arno.engine import series as series_mod
from arno.engine.inputs import Occurrence, build_inputs


@cache
def _persona(key: str, seed: int):
    """Personas are frozen; reuse a generated household across tests instead of regenerating it."""
    return personas.generate(key, seed)


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
    persona = _persona("salaried_steady", personas.TUNING_SEEDS[0])
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
    persona = _persona("messy_recurring", personas.TUNING_SEEDS[0])
    ledger = backtest._ledger(persona)
    as_of = persona.end - timedelta(days=120)

    snapshot = backtest.persona_snapshot(ledger, as_of)

    assert snapshot.attributed is True
    assert snapshot.series
    known_schedule_ids = {s.id for s in persona.schedules if s.created <= as_of}
    confirmed_series_ids = {s.id for s in persona.series if s.schedule_id in known_schedule_ids}
    confirmed_texts = {
        series_mod.normalize(m.payee)
        for m in persona.movements
        if m.series_id in confirmed_series_ids and m.kind == "spend" and m.date < as_of
    }
    assert confirmed_texts
    assert not any(
        forecast.id == f"det_{text}" for forecast in snapshot.series for text in confirmed_texts
    )
    assert any(forecast.id == "det_netflix entertainment" for forecast in snapshot.series)

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


_ENTERTAINMENT = personas.Category("entertainment", "Entertainment", True, None)


def _synthetic_persona(
    movements: tuple[personas.Movement, ...],
    *,
    schedules: tuple[personas.Schedule, ...] = (),
    series: tuple[personas.Series, ...] = (),
) -> personas.Persona:
    return personas.Persona(
        key="test",
        seed=0,
        description="",
        history_start=date(2024, 1, 1),
        end=date(2026, 6, 30),
        opening_bank_paise=100_000_000,
        floor_paise=1_000_000,
        cards=(personas.Card("visa", "Visa", 15, 5, 20_000_000),),
        categories=(_ENTERTAINMENT,),
        schedules=schedules,
        goals=(),
        movements=movements,
        series=series,
    )


def _netflix(
    movement_id: str,
    day: date,
    amount_paise: int,
    *,
    key: str = "netflix",
    account: str = "bank",
    series_id: str | None = None,
) -> personas.Movement:
    return personas.Movement(
        id=movement_id,
        date=day,
        amount_paise=amount_paise,
        kind="spend",
        account=account,
        category_id="entertainment",
        payee="NETFLIX ENTERTAINMENT",
        payee_key=key,
        series_id=series_id,
    )


_MONTHLY_NETFLIX = tuple(
    _netflix(
        f"m{index:02d}",
        date(2024 + index // 12, index % 12 + 1, 18),
        64_900,
        key="netflix_card" if index % 4 else "netflix_bank",
        account="visa" if index % 4 else "bank",
    )
    for index in range(24)
)


def test_persona_detections_group_one_bill_across_payee_keys():
    ledger = backtest._ledger(_synthetic_persona(_MONTHLY_NETFLIX))

    detections = backtest._persona_detections(ledger, date(2025, 12, 29))

    assert len(detections) == 1
    detected = detections[0]
    assert detected.payee_text == "netflix entertainment"
    # Attribution still comes from the matched movements, not the group's name.
    assert detected.card_id == "visa"
    assert detected.category_id == "entertainment"
    matched = set(detected.detection.matched)
    assert matched <= {movement.id for movement in _MONTHLY_NETFLIX}
    assert {m.payee_key for m in _MONTHLY_NETFLIX if m.id in matched} == {
        "netflix_card",
        "netflix_bank",
    }


def test_a_schedule_seen_only_after_the_origin_cannot_hide_an_earlier_payee():
    as_of = date(2026, 1, 1)
    schedule = personas.Schedule(
        id="sched_new",
        name="Streaming plan",
        kind="obligation",
        rule="FREQ=MONTHLY;BYMONTHDAY=18",
        starts=date(2026, 2, 1),
        ends=None,
        amount_paise=79_900,
        holiday_adjust="none",
        card_id=None,
        category_id=None,
        goal_id=None,
        confirmed=True,
        created=date(2025, 12, 1),
    )
    persona = _synthetic_persona(
        _MONTHLY_NETFLIX + (_netflix("future", date(2026, 2, 18), 79_900, series_id="ser_new"),),
        schedules=(schedule,),
        series=(
            personas.Series(
                "ser_new", "netflix", "spend", "monthly", "sched_new", date(2026, 2, 1), None
            ),
        ),
    )

    snapshot = backtest.persona_snapshot(backtest._ledger(persona), as_of)

    # The future statement text must not stand in for a payee the origin had not met yet.
    assert any(forecast.id == "det_netflix entertainment" for forecast in snapshot.series)


def test_a_schedule_created_after_the_origin_does_not_hide_its_spend():
    as_of = date(2026, 2, 20)
    movements = (
        _netflix("m1", date(2026, 1, 15), 64_900, series_id="ser_late"),
        _netflix("m2", date(2026, 2, 15), 79_900, series_id="ser_late"),
    )
    schedule = personas.Schedule(
        id="sched_late",
        name="Netflix",
        kind="obligation",
        rule="FREQ=MONTHLY;BYMONTHDAY=18",
        starts=date(2026, 3, 1),
        ends=None,
        amount_paise=79_900,
        holiday_adjust="none",
        card_id=None,
        category_id="entertainment",
        goal_id=None,
        confirmed=True,
        created=date(2026, 3, 1),
    )
    persona = _synthetic_persona(
        movements,
        schedules=(schedule,),
        series=(
            personas.Series(
                "ser_late", "netflix", "spend", "monthly", "sched_late", date(2026, 3, 1), None
            ),
        ),
    )
    ledger = backtest._ledger(persona)

    before = backtest.persona_snapshot(ledger, as_of)

    assert not before.occurrences
    # The schedule was unknown then, so its spend stays in the forecast's history.
    assert sum(row.amount_paise for row in before.spend) == 64_900 + 79_900

    after = backtest.persona_snapshot(ledger, date(2026, 3, 10))

    assert after.occurrences
    assert all(row.amount_paise not in (64_900, 79_900) for row in after.spend)


def test_empirical_flat_is_the_probabilistic_pair_of_schedule_mean():
    persona = _persona("salaried_steady", personas.TUNING_SEEDS[0])
    ledger = backtest._ledger(persona)
    snapshot = backtest.persona_snapshot(ledger, persona.end - timedelta(days=120))
    projection = build_inputs(snapshot)

    assert "empirical_flat" in backtest.BASELINES
    probabilistic = backtest.empirical_flat(projection, 7)
    assert probabilistic.shape == (backtest.DEFAULT_RUNS, backtest.LAST_HORIZON + 1)
    assert len(set(probabilistic[:, 30].tolist())) > 1
    point = backtest.schedule_mean(projection, 7)
    assert point.shape == (1, backtest.LAST_HORIZON + 1)


def test_the_persona_backtest_scores_each_forecaster_against_the_baselines(monkeypatch):
    origins = backtest.persona_origins
    monkeypatch.setattr(backtest, "persona_origins", lambda persona: origins(persona)[-4:])
    result = backtest.backtest_personas(
        seeds=(personas.HOLDOUT_SEEDS[0],),
        keys=("salaried_steady",),
        names=("flat", "schedule_mean"),
        workers=1,
    )

    assert result["cases"] == 4
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


def test_outcomes_leave_out_income_the_base_case_does_not_count(monkeypatch):
    seed = personas.TUNING_SEEDS[0]
    persona = _persona("freelancer", seed)
    final_origin = backtest.persona_origins(persona)[-1]
    monkeypatch.setattr(backtest, "persona_origins", lambda _: [final_origin])
    (case,) = backtest.persona_cases("freelancer", seed)
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
    # Every demo-ledger origin has an unusable setup or unlinked income, so all are dropped:
    # there is no scored case and no metric, and the report must say exactly that.
    assert result["cases"] == 0
    assert result["dropped"] > 0
    assert result["metrics"] == []


def test_model_from_name_parses_switch_grammar_and_refuses_unknown_switches():
    assert backtest.model_from_name("legacy") == backtest.model_from_name("legacy+legacy")
    model = backtest.model_from_name("day+resample+L2+hl60+rare4+keepcard+prior:0,4,8")
    assert model.pooling == "day"
    assert model.pool_resampling is True
    assert model.block_length == 2
    assert model.recency_half_life_days == 60
    assert model.rare_mad_multiple == 4
    assert model.limit_spend is False
    assert model.prior_quarters == (0, 4, 8)
    with pytest.raises(ValueError, match="unknown model switch"):
        backtest.model_from_name("bogus")
