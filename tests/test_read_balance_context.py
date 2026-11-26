"""Read balance context: the age, credit, and coverage numbers the read path reports.

Each test pins one figure the context snapshot or an account payload states, so that the age a
balance claims, the credit a card can still use, and the setup gaps cannot drift from the ledger
rows behind them.
"""

from __future__ import annotations

from datetime import date

import pytest

from arno.tools import periods


def _context_account(context: dict, account_id: str) -> dict:
    return next(item for item in context["accounts"] if item["id"] == account_id)


def test_a_single_iso_date_is_a_one_day_period():
    today = date(2026, 11, 9)
    assert periods.resolve_period("2026-11-09", today) == (
        date(2026, 11, 9),
        date(2026, 11, 9),
    )
    assert periods.resolve_period("2026-11-01", today) == (
        date(2026, 11, 1),
        date(2026, 11, 1),
    )
    with pytest.raises(ValueError, match="Unknown period") as error:
        periods.resolve_period("9 Nov", today)
    assert '"YYYY-MM-DD", or' in str(error.value)
    with pytest.raises(ValueError, match="has not started"):
        periods.resolve_period("2026-11-10", today)
