"""Financial context, projections, and read tools agree on ledger data."""

from __future__ import annotations

from datetime import date

from arno.engine.projection import CardAccount, statement_close


def _context_account(context: dict, account_id: str) -> dict:
    return next(item for item in context["accounts"] if item["id"] == account_id)


def _balance_age(inputs, account_id: str) -> dict:
    return next(row for row in inputs.data_quality["balance_ages"] if row["id"] == account_id)


def test_statement_close_clamps_in_february_and_rolls_after_close():
    card = CardAccount("card", "Credit Card", 31, 10, 0, 100_000)

    assert statement_close(card, date(2026, 2, 28)) == date(2026, 2, 28)
    assert statement_close(card, date(2026, 3, 1)) == date(2026, 3, 31)


# --- The age of a balance, in the context and in the projection ---------------------------------


# --- Available credit, in the payload and in the engine -----------------------------------------


# --- Why an amount is null ----------------------------------------------------------------------


# --- The rupee helper ----------------------------------------------------------------------------
