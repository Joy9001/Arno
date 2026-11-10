"""A day list that names its month once ("8 and 18 September") is a set of dates, not counts."""

from __future__ import annotations

from arno.agent.number_check import check

TRIPS = {
    "trips": [
        {"date": "2026-09-08", "payee": "Goa"},
        {"date": "2026-09-18", "payee": "Pune"},
    ],
    "october": ["2026-10-08", "2026-10-18", "2026-10-28"],
}


def test_each_day_list_form_is_a_date():
    replies = [
        "You had two trips, on 8 and 18 September.",
        "8 & 18 Sep were trip days.",
        "Trips were on 8th and 18th September.",
        "September 8 and 18 had trips.",
        "Trips: 8, 18 and 28 Oct.",
        "Trips from 8 to 18 September.",
        "Do trips 8 aur 18 September ko thi.",
    ]
    for reply in replies:
        assert check(reply, TRIPS) == [], reply


def test_a_listed_day_no_date_traces_is_still_flagged():
    assert check("You had two trips, on 8 and 19 September.", TRIPS) == ["19"]
    assert check("September 8 and 19 had trips.", TRIPS) == ["19"]


def test_a_day_list_with_no_traced_date_flags_every_day():
    assert check("You had two trips, on 8 and 18 September.", {}) == ["8", "18"]


def test_a_day_list_in_your_own_message_traces_the_days():
    assert check("Trips on 8 and 18 September.", {}, ("trips on 8 and 18 September",)) == []


def test_a_bare_day_amount_and_count_are_checked_as_before():
    assert check("You had 8 trips.", TRIPS) == ["8"]
    assert check("Chai was ₹8.", TRIPS) == ["₹8"]
    assert check("8 of those were Swiggy.", TRIPS) == ["8"]
    assert check("Trips were on 8 and 18 months, twice.", TRIPS) == ["8", "18"]


def test_a_listed_day_in_the_wrong_month_is_flagged():
    september = {"trips": [{"date": "2026-09-08"}, {"date": "2026-09-18"}]}
    assert check("Trips on 8 and 18 October.", september) == ["8", "18"]


def test_a_listed_day_that_matches_a_traced_count_is_still_traced():
    assert check("Renewals on 8 and 18 September.", {"renewals": [8, 18]}) == []


def test_a_year_after_a_comma_is_not_a_second_day():
    assert check("The trip was on Sept 8, 2026.", {}) == []
