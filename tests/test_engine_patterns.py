"""Tests for the pure coverage and cadence helpers in ``arno.engine.patterns``."""

from __future__ import annotations

from arno.engine.patterns import (
    FAMILY_INTERVAL_DAYS,
    KnownSchedule,
    rule_interval_days,
    same_charge,
)


def test_a_clamped_month_end_rule_is_monthly_not_twice_monthly() -> None:
    # normalize_rule writes 31,-1;BYSETPOS=1 for a month-end charge; it is still one a month.
    assert rule_interval_days("FREQ=MONTHLY;BYMONTHDAY=31,-1;BYSETPOS=1") == 30
    assert rule_interval_days("FREQ=MONTHLY;BYMONTHDAY=1,15") == 15
    assert rule_interval_days("FREQ=MONTHLY;BYMONTHDAY=15,31,-1;BYSETPOS=1,2") == 15


def test_a_daily_rule_reports_its_interval() -> None:
    assert rule_interval_days("FREQ=DAILY;INTERVAL=28") == 28
    assert rule_interval_days("FREQ=DAILY") == 1


def test_a_schedule_without_a_known_cadence_never_covers_by_charge() -> None:
    schedule = KnownSchedule(
        id="sch_rent",
        name="Rent",
        normalized="rent",
        amount_paise=2_800_000,
        category_id="cat_rent",
        interval_days=None,
    )
    assert same_charge(schedule, {"cat_rent"}, 2_800_000, FAMILY_INTERVAL_DAYS["monthly"]) is False


def test_a_matching_cadence_and_amount_covers_the_charge() -> None:
    schedule = KnownSchedule(
        id="sch_rent",
        name="Rent",
        normalized="rent",
        amount_paise=2_800_000,
        category_id="cat_rent",
        interval_days=FAMILY_INTERVAL_DAYS["monthly"],
    )
    assert same_charge(schedule, {"cat_rent"}, 2_800_000, FAMILY_INTERVAL_DAYS["monthly"]) is True
    assert same_charge(schedule, {"cat_rent"}, 2_800_000, FAMILY_INTERVAL_DAYS["weekly"]) is False
