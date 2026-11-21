"""Tests for RRULE normalization: an inclusive UNTIL and month-end clamping."""

from __future__ import annotations

from datetime import date

from arno.india.recurrence import expand_rule, normalize_rule


def test_a_date_only_until_includes_its_day() -> None:
    for until in ("UNTIL=20260305", "UNTIL=20260305T000000"):
        dates = expand_rule(
            f"FREQ=MONTHLY;BYMONTHDAY=5;{until}",
            date(2026, 1, 1),
            start=date(2026, 1, 1),
            end=date(2026, 6, 30),
        )
        assert dates == [date(2026, 1, 5), date(2026, 2, 5), date(2026, 3, 5)]


def test_a_comma_month_end_keeps_every_charge_in_a_short_month() -> None:
    dates = expand_rule(
        "FREQ=MONTHLY;BYMONTHDAY=15,31",
        date(2026, 1, 1),
        start=date(2026, 1, 1),
        end=date(2026, 3, 31),
    )
    assert dates == [
        date(2026, 1, 15),
        date(2026, 1, 31),
        date(2026, 2, 15),
        date(2026, 2, 28),
        date(2026, 3, 15),
        date(2026, 3, 31),
    ]


def test_a_second_day_near_month_end_clamps_without_gaining_a_charge() -> None:
    dates = expand_rule(
        "FREQ=MONTHLY;BYMONTHDAY=5,30",
        date(2026, 1, 1),
        start=date(2026, 1, 1),
        end=date(2026, 2, 28),
    )
    assert dates == [date(2026, 1, 5), date(2026, 1, 30), date(2026, 2, 5), date(2026, 2, 28)]


def test_a_clamped_rule_normalizes_to_itself() -> None:
    first = normalize_rule("FREQ=MONTHLY;BYMONTHDAY=15,31")
    assert normalize_rule(first) == first
