"""Regression tests for the type-cleanup pass in ledger/, db/, engine/, jobs/ (agent tyA).

Each test pins a `None` that Pyrefly proved could reach a subscript: a row the code assumed was
there but a later change can remove.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from fin_agent.ledger.accounts import create_account
from fin_agent.ledger.common import today
from fin_agent.ledger.errors import ConflictLedgerError
from fin_agent.ledger.movements import record_expense
from fin_agent.ledger.schedule_payments import (
    link_payment,
    unlink_payment,
    validate_restored_payment,
)
from fin_agent.ledger.schedules import create_schedule


async def test_restoring_a_payment_whose_occurrence_is_gone_conflicts(ledger):
    """Undoing an unlink must say what is wrong, not crash on a missing occurrence row.

    An unlinked payment leaves an audit row with no `schedule_payments` row behind it, so undoing
    the occurrence's creation is no longer blocked by the "a later match shares these terms" guard.
    The occurrence can then be gone when the payment is restored.
    """
    bank = await create_account(
        ledger, kind="bank", name="Restore bank", opening_balance_paise=1_000_000
    )
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        name="Restore plan",
        amount_paise=100_000,
        account_id=bank["id"],
        rule="FREQ=DAILY;COUNT=90",
        starts=today() - timedelta(days=30),
    )
    due = today() + timedelta(days=10)
    movement = await record_expense(
        ledger, account_id=bank["id"], amount_paise=100_000, description="A recorded payment"
    )
    linked = await link_payment(
        ledger, schedule_id=schedule["id"], due_date=due, movement_id=movement["id"]
    )
    assert linked["status"] == "linked"
    await unlink_payment(
        ledger, schedule_id=schedule["id"], due_date=due, movement_id=movement["id"]
    )
    payment = {
        "occurrence_id": "occ_does_not_exist",
        "movement_id": movement["id"],
        "amount_paise": 100_000,
    }
    with pytest.raises(ConflictLedgerError):
        await validate_restored_payment(ledger, payment)
