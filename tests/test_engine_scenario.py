"""Pure scenario transforms and validation for multi-event what-ifs."""

from __future__ import annotations

import itertools
from copy import deepcopy
from datetime import date

import pytest

from arno.engine.affordability import project_scenario
from arno.engine.projection import (
    MAX_HORIZON_DAYS,
    CashEvent,
    GoalPlan,
    LiquidAccount,
    ProjectionInput,
    SeriesForecast,
    project,
)
from arno.engine.scenario import (
    MAX_CHANGES,
    AddRecurring,
    ChangeAmount,
    OneTime,
    PauseSchedule,
    StopIncome,
    apply_changes,
    scenario_horizon_days,
)


def _inputs(
    *,
    as_of: date = date(2026, 1, 1),
    start: int = 1_000_000,
    floor: int = 100_000,
    events: tuple[CashEvent, ...] = (),
    goals: tuple[GoalPlan, ...] = (),
    series: tuple[SeriesForecast, ...] = (),
    horizon: int = 180,
) -> ProjectionInput:
    return ProjectionInput(
        as_of=as_of,
        floor_paise=floor,
        liquid=(LiquidAccount(id="acc_1", name="Bank", balance_paise=start),),
        events=events,
        goals=goals,
        series=series,
        ask_horizon_days=horizon,
    )


def test_stop_income_removes_confirmed_and_unconfirmed_income_and_keeps_owed_receipts():
    base = _inputs(
        events=(
            CashEvent(
                date=date(2026, 1, 31),
                amount_paise=15_000_000,
                name="Salary",
                kind="income_fixed",
                source_id="sch_sal",
                confirmed=True,
            ),
            CashEvent(
                date=date(2026, 2, 15),
                amount_paise=2_500_000,
                name="Freelance",
                kind="income_variable",
                source_id="sch_free",
                confirmed=False,
            ),
            CashEvent(
                date=date(2026, 1, 20),
                amount_paise=500_000,
                name="Ravi owes you",
                kind="owed",
                confirmed=True,
            ),
            CashEvent(
                date=date(2026, 2, 5),
                amount_paise=-2_800_000,
                name="Rent",
                kind="obligation",
                source_id="sch_rent",
            ),
        )
    )
    snapshot = deepcopy(base)

    applied = apply_changes(
        base,
        [StopIncome(start=date(2026, 1, 15), until=date(2026, 2, 20))],
        income_source_ids={"sch_sal", "sch_free"},
    )
    assert base == snapshot

    kept_names = [event.name for event in applied.inputs.events]
    assert kept_names == ["Ravi owes you", "Rent"]
    assert applied.changes[0].occurrences == 2
    assert applied.changes[0].amount_paise == 17_500_000


def test_stop_income_for_one_schedule_keeps_other_income():
    base = _inputs(
        events=(
            CashEvent(
                date=date(2026, 1, 31),
                amount_paise=15_000_000,
                name="Salary",
                kind="income_fixed",
                source_id="sch_sal",
            ),
            CashEvent(
                date=date(2026, 2, 15),
                amount_paise=2_500_000,
                name="Freelance",
                kind="income_variable",
                source_id="sch_free",
                confirmed=False,
            ),
        )
    )
    applied = apply_changes(
        base,
        [StopIncome(start=date(2026, 2, 1), source_id="sch_free", label_name="Freelance")],
        income_source_ids={"sch_sal", "sch_free"},
    )
    assert [event.name for event in applied.inputs.events] == ["Salary"]
    assert applied.changes[0].occurrences == 1
    assert applied.changes[0].amount_paise == 2_500_000


def test_pause_schedule_on_goal_contribution_lowers_goal_funding_and_series_dates():
    goal = GoalPlan(
        id="goal_goa",
        name="Goa trip",
        target_paise=1_000_000,
        saved_paise=400_000,
        deadline=date(2026, 5, 1),
    )
    base = _inputs(
        start=2_000_000,
        events=(
            CashEvent(
                date=date(2026, 1, 10),
                amount_paise=-200_000,
                name="Goa SIP",
                kind="goal_contribution",
                source_id="sch_sip",
                goal_id="goal_goa",
            ),
            CashEvent(
                date=date(2026, 2, 10),
                amount_paise=-200_000,
                name="Goa SIP",
                kind="goal_contribution",
                source_id="sch_sip",
                goal_id="goal_goa",
            ),
            CashEvent(
                date=date(2026, 3, 10),
                amount_paise=-200_000,
                name="Goa SIP",
                kind="goal_contribution",
                source_id="sch_sip",
                goal_id="goal_goa",
            ),
        ),
        goals=(goal,),
        series=(
            SeriesForecast(
                id="ser_gym",
                name="Gym",
                expected=(date(2026, 1, 15), date(2026, 2, 15), date(2026, 3, 15)),
                jitter=((0, 1.0),),
                skip=0.0,
                alive=1.0,
                amounts=(100_000,),
            ),
        ),
    )

    applied = apply_changes(
        base,
        [
            PauseSchedule(
                start=date(2026, 2, 1),
                until=date(2026, 2, 28),
                source_id="sch_sip",
                label_name="Goa SIP",
            ),
            PauseSchedule(
                start=date(2026, 1, 1),
                until=date(2026, 2, 20),
                series_id="ser_gym",
                label_name="Gym",
            ),
        ],
    )
    assert len(applied.inputs.events) == 2
    assert [e.date for e in applied.inputs.events] == [date(2026, 1, 10), date(2026, 3, 10)]
    assert applied.inputs.series[0].expected == (date(2026, 3, 15),)

    unchanged = project(base, runs=30)
    changed = project(applied.inputs, runs=30)
    assert unchanged["goals"][0]["reached"] is True
    assert changed["goals"][0]["reached"] is False
    assert (
        changed["goals"][0]["remaining_after_plan_paise"]
        > unchanged["goals"][0]["remaining_after_plan_paise"]
    )


def test_change_amount_preserves_event_direction():
    base = _inputs(
        events=(
            CashEvent(
                date=date(2026, 1, 5),
                amount_paise=-2_500_000,
                name="Rent",
                kind="obligation",
                source_id="sch_rent",
            ),
            CashEvent(
                date=date(2026, 2, 5),
                amount_paise=-2_500_000,
                name="Rent",
                kind="obligation",
                source_id="sch_rent",
            ),
            CashEvent(
                date=date(2026, 1, 31),
                amount_paise=10_000_000,
                name="Salary",
                kind="income_fixed",
                source_id="sch_sal",
            ),
        )
    )
    applied = apply_changes(
        base,
        [
            ChangeAmount(
                source_id="sch_rent",
                amount_paise=3_000_000,
                start=date(2026, 2, 1),
                label_name="Rent",
            ),
            ChangeAmount(
                source_id="sch_sal",
                amount_paise=12_000_000,
                start=date(2026, 1, 1),
                label_name="Salary",
            ),
        ],
        income_source_ids={"sch_sal"},
    )
    amounts = {(event.name, event.date): event.amount_paise for event in applied.inputs.events}
    assert amounts[("Rent", date(2026, 1, 5))] == -2_500_000
    assert amounts[("Rent", date(2026, 2, 5))] == -3_000_000
    assert amounts[("Salary", date(2026, 1, 31))] == 12_000_000


def test_add_recurring_dates_for_month_ends_weeks_and_years():
    base = _inputs(as_of=date(2026, 1, 1), horizon=400)
    applied = apply_changes(
        base,
        [
            AddRecurring(
                name="MonthEndBill",
                amount_paise=10_000,
                direction="out",
                start=date(2026, 1, 31),
                until=date(2026, 4, 30),
                every="month",
            ),
            AddRecurring(
                name="WeeklyTutor",
                amount_paise=5_000,
                direction="out",
                start=date(2026, 1, 10),
                until=date(2026, 1, 25),
                every="week",
            ),
            AddRecurring(
                name="YearlyIns",
                amount_paise=50_000,
                direction="out",
                start=date(2026, 2, 1),
                until=date(2027, 2, 1),
                every="year",
            ),
        ],
    )
    by_name: dict[str, list[date]] = {}
    for event in applied.inputs.events:
        by_name.setdefault(event.name, []).append(event.date)
    assert by_name["MonthEndBill"] == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
    ]
    assert by_name["WeeklyTutor"] == [date(2026, 1, 10), date(2026, 1, 17), date(2026, 1, 24)]
    assert by_name["YearlyIns"] == [date(2026, 2, 1), date(2027, 2, 1)]


def test_added_out_flow_is_obligation_and_goal_flow_reaches_goal():
    goal = GoalPlan(
        id="goal_trip",
        name="Trip",
        target_paise=300_000,
        saved_paise=0,
        deadline=date(2026, 4, 1),
    )
    base = _inputs(start=200_000, floor=0, goals=(goal,), horizon=120)

    # Obligation larger than cash on day 5 fails obligations_met.
    obl_result = project_scenario(
        base,
        [
            AddRecurring(
                name="Big EMI",
                amount_paise=500_000,
                direction="out",
                start=date(2026, 1, 10),
                until=date(2026, 1, 10),
            )
        ],
        runs=40,
    )
    assert obl_result["obligations_met"] is False
    assert obl_result["scenario"]["holds"] is False

    # Goal contributions reach the target and fund the goal.
    rich = _inputs(start=1_000_000, floor=0, goals=(goal,), horizon=120)
    goal_result = project_scenario(
        rich,
        [
            AddRecurring(
                name="Trip SIP",
                amount_paise=100_000,
                direction="out",
                start=date(2026, 1, 5),
                until=date(2026, 3, 5),
                goal_id="goal_trip",
                goal_name="Trip",
            ),
            OneTime(
                name="Bonus",
                amount_paise=200_000,
                direction="in",
                date=date(2026, 1, 2),
            ),
        ],
        runs=40,
    )
    assert goal_result["goals"][0]["reached"] is True
    assert goal_result["goals"][0]["completes_on"] == "2026-03-05"
    assert any(
        "unconfirmed" not in a and "assumed" in a.lower()
        for a in goal_result["scenario"]["assumptions"]
    )


@pytest.mark.parametrize(
    "changes",
    [
        [
            PauseSchedule(start=date(2026, 1, 10), until=date(2026, 2, 10), source_id="sch_1"),
            ChangeAmount(source_id="sch_1", amount_paise=50_000, start=date(2026, 2, 1)),
        ],
        [
            StopIncome(start=date(2026, 1, 1), until=date(2026, 3, 1)),
            StopIncome(start=date(2026, 2, 1), source_id="sch_sal"),
        ],
        [
            PauseSchedule(start=date(2026, 1, 1), series_id="ser_1"),
            PauseSchedule(start=date(2026, 2, 1), until=date(2026, 2, 28), series_id="ser_1"),
        ],
    ],
)
def test_overlapping_changes_raise(changes):
    base = _inputs()
    with pytest.raises(ValueError, match="overlap"):
        apply_changes(base, changes, income_source_ids={"sch_sal"})


def test_validation_errors_for_past_dates_empty_or_too_many():
    base = _inputs(as_of=date(2026, 1, 10))
    with pytest.raises(ValueError, match="before"):
        apply_changes(base, [StopIncome(start=date(2026, 1, 9))])
    with pytest.raises(ValueError, match="before"):
        apply_changes(
            base,
            [
                OneTime(
                    name="Late bill", amount_paise=10_000, direction="out", date=date(2026, 1, 1)
                )
            ],
        )
    with pytest.raises(ValueError, match="1 to 12"):
        apply_changes(base, [])
    too_many = [
        OneTime(name=f"Item {i}", amount_paise=1_000, direction="out", date=date(2026, 1, 11 + i))
        for i in range(MAX_CHANGES + 1)
    ]
    with pytest.raises(ValueError, match="1 to 12"):
        apply_changes(base, too_many)
    with pytest.raises(ValueError, match="horizon"):
        apply_changes(
            base,
            [
                OneTime(
                    name="Far",
                    amount_paise=1_000,
                    direction="out",
                    date=date.fromordinal(base.as_of.toordinal() + MAX_HORIZON_DAYS),
                )
            ],
        )


def test_one_time_outflow_is_obligation_that_fails_obligations_met():
    base = _inputs(as_of=date(2026, 1, 1), start=100_000, floor=0, horizon=90)
    result = project_scenario(
        base,
        [
            OneTime(
                name="Property tax", amount_paise=400_000, direction="out", date=date(2026, 1, 20)
            )
        ],
        runs=40,
    )
    assert result["obligations_met"] is False
    assert result["scenario"]["holds"] is False


def test_add_recurring_starting_on_as_of_counts_day_zero_goal_contribution():
    goal = GoalPlan(
        id="goal_trip",
        name="Trip",
        target_paise=200_000,
        saved_paise=0,
        deadline=date(2026, 3, 1),
    )
    base = _inputs(as_of=date(2026, 1, 1), start=500_000, floor=0, goals=(goal,), horizon=90)
    result = project_scenario(
        base,
        [
            AddRecurring(
                name="Trip SIP",
                amount_paise=100_000,
                direction="out",
                start=date(2026, 1, 1),
                until=date(2026, 2, 1),
                goal_id="goal_trip",
                goal_name="Trip",
            )
        ],
        runs=40,
    )
    assert result["goals"][0]["reached"] is True
    assert result["goals"][0]["completes_on"] == "2026-02-01"
    assert result["goals"][0]["remaining_after_plan_paise"] == 0


def test_overlap_touching_one_day_vs_adjacent_windows():
    base = _inputs(
        events=(
            CashEvent(
                date=date(2026, 1, 15),
                amount_paise=-100_000,
                name="Rent",
                kind="obligation",
                source_id="sch_rent",
            ),
            CashEvent(
                date=date(2026, 2, 15),
                amount_paise=-100_000,
                name="Rent",
                kind="obligation",
                source_id="sch_rent",
            ),
        )
    )
    with pytest.raises(ValueError, match="overlap"):
        apply_changes(
            base,
            [
                PauseSchedule(start=date(2026, 1, 1), until=date(2026, 2, 1), source_id="sch_rent"),
                ChangeAmount(
                    source_id="sch_rent",
                    amount_paise=150_000,
                    start=date(2026, 2, 1),
                    until=date(2026, 2, 28),
                ),
            ],
        )

    applied = apply_changes(
        base,
        [
            PauseSchedule(start=date(2026, 1, 1), until=date(2026, 1, 31), source_id="sch_rent"),
            ChangeAmount(
                source_id="sch_rent",
                amount_paise=150_000,
                start=date(2026, 2, 1),
                until=date(2026, 2, 28),
            ),
        ],
    )
    assert [(e.date, e.amount_paise) for e in applied.inputs.events] == [
        (date(2026, 2, 15), -150_000)
    ]


def test_any_permutation_gives_identical_projection():
    base = _inputs(
        start=2_000_000,
        floor=100_000,
        events=(
            CashEvent(
                date=date(2026, 2, 5),
                amount_paise=-500_000,
                name="Rent",
                kind="obligation",
                source_id="sch_rent",
            ),
            CashEvent(
                date=date(2026, 1, 31),
                amount_paise=1_000_000,
                name="Salary",
                kind="income_fixed",
                source_id="sch_sal",
            ),
            CashEvent(
                date=date(2026, 1, 12),
                amount_paise=-10_000,
                name="Music",
                kind="obligation",
                source_id="sch_music",
            ),
        ),
        horizon=120,
    )
    specs = [
        ChangeAmount(
            source_id="sch_rent", amount_paise=700_000, start=date(2026, 2, 1), label_name="Rent"
        ),
        PauseSchedule(
            start=date(2026, 1, 1),
            until=date(2026, 1, 31),
            source_id="sch_music",
            label_name="Music",
        ),
        OneTime(name="Repair", amount_paise=250_000, direction="out", date=date(2026, 1, 20)),
        AddRecurring(
            name="Freelance", amount_paise=300_000, direction="in", start=date(2026, 2, 10)
        ),
    ]
    applied_first = apply_changes(base, specs, income_source_ids={"sch_sal"})
    # Unchanged events (Rent on Feb 5 before Salary on Jan 31) keep their original relative order.
    assert [event.name for event in applied_first.inputs.events[:2]] == ["Rent", "Salary"]

    perms = list(itertools.permutations(specs))[:6]
    first = project_scenario(base, list(perms[0]), income_source_ids={"sch_sal"}, runs=40)
    for perm in perms[1:]:
        applied = apply_changes(base, list(perm), income_source_ids={"sch_sal"})
        assert applied.inputs.events == applied_first.inputs.events
        nxt = project_scenario(base, list(perm), income_source_ids={"sch_sal"}, runs=40)
        assert nxt["floor_probability"] == first["floor_probability"]
        assert nxt["median_low"] == first["median_low"]
        assert nxt["first_floor_crossing"] == first["first_floor_crossing"]
        assert nxt["curve"] == first["curve"]
        assert nxt["scenario"] == first["scenario"]


def test_scenario_horizon_extends_at_least_90_days_past_latest_change():
    as_of = date(2026, 1, 1)
    assert (
        scenario_horizon_days(
            as_of,
            [OneTime(name="Tax", amount_paise=10_000, direction="out", date=date(2026, 4, 1))],
        )
        == 90 + 90
    )
    assert (
        scenario_horizon_days(
            as_of,
            [StopIncome(start=date(2026, 1, 10), until=date(2026, 10, 1))],
        )
        == (date(2026, 10, 1) - as_of).days
    )
