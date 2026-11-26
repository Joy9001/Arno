"""Runtime regressions the type-cleanup passes uncovered across ledger, agent, and API."""

from __future__ import annotations

import base64
from datetime import timedelta

import pytest

from arno.ledger.accounts import create_account
from arno.ledger.common import today
from arno.ledger.errors import ConflictLedgerError
from arno.ledger.movements import record_expense
from arno.ledger.schedule_payments import link_payment, unlink_payment, validate_restored_payment
from arno.ledger.schedules import create_schedule
from arno.notifications import ensure_vapid_keys


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


async def test_vapid_keys_are_a_matching_pair(ledger):
    """Generating the key ourselves still stores a public key that matches the private one."""
    from cryptography.hazmat.primitives import serialization

    row = await ensure_vapid_keys(ledger)
    private_key = serialization.load_pem_private_key(row["private_key"].encode(), password=None)
    point = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    assert base64.urlsafe_b64encode(point).decode("ascii").rstrip("=") == row["public_key"]
    assert len(point) == 65 and point[0] == 4
