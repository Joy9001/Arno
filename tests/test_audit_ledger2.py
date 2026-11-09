"""Ledger release-audit regressions, wave 2.

Split and loan corrections (amount changes), the unknown-interest loan note, stated balances
dated in the future, person transfers covering a schedule occurrence, credential-free exports,
and case-insensitive UPI matching. Every amount is exact paise, and every correction is followed
by the whole-ledger invariants check.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import timedelta
from decimal import Decimal

import pytest

from fin_agent.ledger.accounts import create_account, set_statement_balance
from fin_agent.ledger.audit import undo_group
from fin_agent.ledger.common import today
from fin_agent.ledger.errors import InvalidLedgerError
from fin_agent.ledger.export import export_csv_zip, export_json, table_names
from fin_agent.ledger.movements import correct_movement, record_split, record_transfer
from fin_agent.ledger.payees import create_payee, ensure_person_account
from fin_agent.ledger.schedule_payments import link_payment
from fin_agent.ledger.schedules import create_schedule
from fin_agent.tools import write as write_tools
from tests.invariants import check_ledger

SECRET_TABLES = {
    "users",
    "sessions",
    "webauthn_credentials",
    "webauthn_challenges",
    "setup_tokens",
    "vapid_keys",
    "push_subscriptions",
}


def _clean(violations) -> None:
    assert violations == [], "\n".join(
        f"{violation.code}: {violation.detail} ids={violation.ids}" for violation in violations
    )


async def _bank(conn, name: str = "Wave bank", opening: int = 10_000_000) -> dict:
    return await create_account(conn, kind="bank", name=name, opening_balance_paise=opening)


async def _spending(conn) -> int:
    return int(
        (await conn.fetchrow("SELECT coalesce(sum(amount_paise), 0) AS n FROM v_spending"))["n"]
    )


async def _owed(conn) -> dict[str, int]:
    rows = await conn.fetch("SELECT payee_id, balance_paise FROM v_owed")
    return {str(row["payee_id"]): int(row["balance_paise"]) for row in rows}


async def _person(conn, name: str) -> dict:
    return await create_payee(conn, display_name=name, kind="person")


async def _labels(conn, movement_id: str) -> list[dict]:
    return await conn.fetch(
        """
        SELECT fa.kind AS from_kind, ta.kind AS to_kind,
               t.amount::bigint AS amount_paise, p.display_name AS payee_name
        FROM transfer_labels tl
        JOIN pgledger_transfers t ON t.id = tl.transfer_id
        JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
        JOIN accounts ta ON ta.ledger_account_id = t.to_account_id
        LEFT JOIN payees p ON p.id = tl.payee_id
        WHERE tl.movement_id = $1
        ORDER BY t.id
        """,
        movement_id,
    )


async def _three_way_split(conn, *, total: int, my_share: int) -> tuple[dict, dict, dict, dict]:
    """A split paid by one bank: mine plus Ravi and Amit, each with an explicit share."""
    bank = await _bank(conn)
    vendor = await create_payee(conn, display_name="Swiggy", kind="merchant")
    ravi = await _person(conn, "Ravi")
    amit = await _person(conn, "Amit")
    remaining = total - my_share
    ravi_share = remaining // 2
    split = await record_split(
        conn,
        account_id=bank["id"],
        my_share_paise=my_share,
        people=[
            {"payee_id": ravi["id"], "amount_paise": ravi_share},
            {"payee_id": amit["id"], "amount_paise": remaining - ravi_share},
        ],
        total_paise=total,
        payee_id=vendor["id"],
    )
    return bank, ravi, amit, split


# 1. A payee change never renames a person share ------------------------------------------------


async def test_a_payee_change_on_a_split_keeps_each_persons_share_label(ledger):
    _, ravi, amit, split = await _three_way_split(ledger, total=120_000, my_share=40_000)
    zomato = await create_payee(ledger, display_name="Zomato", kind="merchant")

    result = await correct_movement(ledger, split["id"], changes={"payee_id": zomato["id"]})

    labels = await _labels(ledger, result["movement_id"])
    spending = [row for row in labels if row["to_kind"] == "spending"]
    person_labels = sorted(row["payee_name"] for row in labels if row["to_kind"] == "person")
    assert [row["payee_name"] for row in spending] == ["Zomato"]
    assert person_labels == ["Amit", "Ravi"]
    # What each person owes is untouched by the vendor's name.
    assert await _owed(ledger) == {ravi["id"]: 40_000, amit["id"]: 40_000}
    _clean(await check_ledger(ledger))


# 2. Correcting a split's amount ---------------------------------------------------------------


async def test_correcting_an_equal_split_amount_resplits_with_the_remainder_to_me(ledger):
    # ₹100 three ways: 3334 + 3333 + 3333, the odd paise on my share.
    bank, ravi, amit, split = await _three_way_split(ledger, total=10_000, my_share=3_334)
    assert await _spending(ledger) == 3_334

    result = await correct_movement(ledger, split["id"], changes={"amount_paise": 10_100})

    # ₹101 three ways is 3366 each with 2 paise of remainder; both go to my share.
    amounts = sorted(
        int(row["amount_paise"]) for row in await _labels(ledger, result["movement_id"])
    )
    assert amounts == [3_366, 3_366, 3_368]
    assert await _spending(ledger) == 3_368
    assert await _owed(ledger) == {ravi["id"]: 3_366, amit["id"]: 3_366}
    _clean(await check_ledger(ledger))


async def test_correcting_a_split_amount_with_explicit_shares(ledger):
    bank, ravi, amit, split = await _three_way_split(ledger, total=10_000, my_share=3_334)

    await correct_movement(
        ledger,
        split["id"],
        changes={
            "amount_paise": 6_000,
            "my_share_paise": 2_000,
            "shares": [
                {"payee_id": ravi["id"], "amount_paise": 1_000},
                {"payee_id": amit["id"], "amount_paise": 3_000},
            ],
        },
    )

    assert await _spending(ledger) == 2_000
    assert await _owed(ledger) == {ravi["id"]: 1_000, amit["id"]: 3_000}
    _clean(await check_ledger(ledger))


async def test_correcting_a_split_amount_through_the_tool_takes_rupees_and_names(ledger):
    bank, ravi, amit, split = await _three_way_split(ledger, total=10_000, my_share=3_334)

    result = await write_tools.correct_movement(
        ledger,
        movement_id=split["id"],
        changes={
            "amount_rupees": Decimal("120"),
            "my_share_rupees": 40,
            "shares": [
                {"name": "Ravi", "amount_rupees": 50},
                {"name": "Amit", "amount_rupees": 30},
            ],
        },
    )

    assert result["status"] == "corrected"
    assert await _spending(ledger) == 4_000
    assert await _owed(ledger) == {ravi["id"]: 5_000, amit["id"]: 3_000}
    _clean(await check_ledger(ledger))


async def test_correcting_an_uneven_split_amount_asks_for_the_shares(ledger):
    bank = await _bank(ledger)
    ravi = await _person(ledger, "Ravi")
    amit = await _person(ledger, "Amit")
    split = await record_split(
        ledger,
        account_id=bank["id"],
        my_share_paise=2_000,
        people=[
            {"payee_id": ravi["id"], "amount_paise": 5_000},
            {"payee_id": amit["id"], "amount_paise": 3_000},
        ],
        total_paise=10_000,
    )

    with pytest.raises(InvalidLedgerError, match="share"):
        await correct_movement(ledger, split["id"], changes={"amount_paise": 12_000})
    with pytest.raises(InvalidLedgerError, match="add up"):
        await correct_movement(
            ledger,
            split["id"],
            changes={
                "amount_paise": 12_000,
                "my_share_paise": 3_000,
                "shares": [
                    {"payee_id": ravi["id"], "amount_paise": 4_000},
                    {"payee_id": amit["id"], "amount_paise": 4_000},
                ],
            },
        )
    # Nothing was written by either refused correction.
    assert await _spending(ledger) == 2_000
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'reversal'") == 0
    _clean(await check_ledger(ledger))


async def test_a_split_correction_can_move_every_share_to_another_account(ledger):
    bank, ravi, amit, split = await _three_way_split(ledger, total=10_000, my_share=3_334)
    other = await _bank(ledger, "Other bank")

    await correct_movement(ledger, split["id"], changes={"account_id": other["id"]})

    assert await _balance(ledger, bank["id"]) == 10_000_000
    assert await _balance(ledger, other["id"]) == 10_000_000 - 10_000
    assert await _owed(ledger) == {ravi["id"]: 3_333, amit["id"]: 3_333}
    _clean(await check_ledger(ledger))


async def test_undoing_a_split_correction_restores_the_original(ledger):
    bank, ravi, amit, split = await _three_way_split(ledger, total=10_000, my_share=3_334)
    result = await correct_movement(ledger, split["id"], changes={"amount_paise": 15_000})
    assert await _spending(ledger) == 5_000

    await undo_group(ledger, result["undo_id"])

    assert await _spending(ledger) == 3_334
    assert await _owed(ledger) == {ravi["id"]: 3_333, amit["id"]: 3_333}
    assert await _balance(ledger, bank["id"]) == 9_990_000
    _clean(await check_ledger(ledger))


# 3. Correcting a loan payment ----------------------------------------------------------------


async def _loan(conn, *, rate_bps: int | None, outstanding: int = 500_000) -> dict:
    return await create_account(
        conn,
        kind="loan",
        name="Car loan" if rate_bps is not None else "Friend loan",
        opening_balance_paise=-outstanding,
        rate_bps=rate_bps,
    )


async def _balance(conn, account_id: str) -> int:
    return int(
        await conn.fetchval(
            "SELECT coalesce(balance, 0)::bigint FROM pgledger_accounts "
            "WHERE id = (SELECT ledger_account_id FROM accounts WHERE id = $1)",
            account_id,
        )
    )


async def test_correcting_a_loan_payment_recomputes_interest_and_undo_restores(ledger):
    bank = await _bank(ledger)
    loan = await _loan(ledger, rate_bps=1_200)  # ₹5,000 outstanding at 12% a year: ₹50 a month
    payment = await write_tools.record_movement(
        ledger,
        {"kind": "loan", "amount_rupees": 1_000, "loan": loan["name"], "account": bank["name"]},
        message_id=None,
        conversation_id=None,
    )
    assert await _spending(ledger) == 5_000
    assert await _balance(ledger, loan["id"]) == -405_000

    result = await correct_movement(
        ledger, payment["movement_id"], changes={"amount_paise": 200_000}
    )

    assert await _spending(ledger) == 5_000
    assert await _balance(ledger, loan["id"]) == -305_000
    _clean(await check_ledger(ledger))

    await undo_group(ledger, result["undo_id"])
    assert await _balance(ledger, loan["id"]) == -405_000
    assert await _spending(ledger) == 5_000
    _clean(await check_ledger(ledger))


async def test_a_loan_payment_without_a_rate_says_the_interest_is_unknown(ledger):
    bank = await _bank(ledger)
    loan = await _loan(ledger, rate_bps=None, outstanding=100_000)

    result = await write_tools.record_movement(
        ledger,
        {"kind": "loan", "amount_rupees": 500, "loan": loan["name"], "account": bank["name"]},
        message_id=None,
        conversation_id=None,
    )

    # It is still recorded, with no extra friction: the whole EMI as principal.
    assert result["status"] == "recorded"
    assert await _balance(ledger, loan["id"]) == -50_000
    assert await _spending(ledger) == 0
    note = result["note"]
    assert "interest" in note.lower() and "principal" in note.lower()
    assert "unknown" in note.lower()
    assert note in result["card"]["summary"]
    _clean(await check_ledger(ledger))


# 4. A stated balance cannot be dated in the future ---------------------------------------------


async def test_a_stated_balance_cannot_be_dated_in_the_future(ledger):
    bank = await _bank(ledger, "Future bank")
    tomorrow = today() + timedelta(days=1)

    with pytest.raises(InvalidLedgerError, match="future"):
        await write_tools.update_setup_item(
            ledger,
            entity="balance",
            action="update",
            data={
                "account": bank["name"],
                "balance_rupees": 5_000,
                "as_of": tomorrow.isoformat(),
            },
            message_id=None,
        )
    with pytest.raises(InvalidLedgerError, match="future"):
        await write_tools.update_setup_item(
            ledger,
            entity="account",
            action="add",
            data={
                "name": "Dated bank",
                "opening_balance_rupees": 5_000,
                "as_of": tomorrow.isoformat(),
            },
            message_id=None,
        )
    with pytest.raises(InvalidLedgerError, match="future"):
        await set_statement_balance(ledger, bank["id"], balance_paise=5_000, as_of=tomorrow)

    # A refusal writes nothing at all.
    assert await ledger.fetchval("SELECT count(*) FROM accounts WHERE name = 'Dated bank'") == 0
    assert await _balance(ledger, bank["id"]) == 10_000_000
    assert await ledger.fetchval("SELECT count(*) FROM movements") == 1

    # Today is the limit, and it is accepted.
    await set_statement_balance(ledger, bank["id"], balance_paise=5_000, as_of=today())
    assert await _balance(ledger, bank["id"]) == 5_000


# 5. A transfer to a person pays a family-transfer obligation -----------------------------------


async def _family_schedule(conn, bank, *, amount: int = 200_000):
    return await create_schedule(
        conn,
        kind="obligation",
        subtype="family_transfer",
        name="Money to Mom",
        amount_paise=amount,
        account_id=bank["id"],
        rule="FREQ=DAILY;COUNT=90",
        starts=today() - timedelta(days=30),
    )


async def test_a_transfer_to_a_person_pays_a_family_transfer_obligation(ledger):
    bank = await _bank(ledger, "Family bank")
    mom = await _person(ledger, "Mom")
    schedule = await _family_schedule(ledger, bank)
    person_account = await ensure_person_account(ledger, mom["id"])
    due = today() + timedelta(days=3)
    movement = await record_transfer(
        ledger,
        from_account_id=bank["id"],
        to_account_id=person_account["id"],
        amount_paise=200_000,
        description="Sent Mom her monthly money",
        payee_id=mom["id"],
    )

    linked = await link_payment(
        ledger, schedule_id=schedule["id"], due_date=due, movement_id=movement["id"]
    )

    assert linked["status"] == "linked"
    assert linked["amount_paise"] == 200_000


async def test_an_own_account_transfer_does_not_pay_a_family_transfer_obligation(ledger):
    bank = await _bank(ledger, "Own bank")
    other = await _bank(ledger, "Own savings")
    schedule = await _family_schedule(ledger, bank)
    due = today() + timedelta(days=3)
    movement = await record_transfer(
        ledger,
        from_account_id=bank["id"],
        to_account_id=other["id"],
        amount_paise=200_000,
        description="Moved to savings",
    )

    with pytest.raises(InvalidLedgerError, match="does not pay"):
        await link_payment(
            ledger, schedule_id=schedule["id"], due_date=due, movement_id=movement["id"]
        )


# 6. The data export never carries credential material ------------------------------------------


async def _seed_secrets(conn) -> None:
    await conn.execute("INSERT INTO users (webauthn_user_handle) VALUES (decode('00', 'hex'))")
    await conn.execute(
        "INSERT INTO sessions (user_id, token_hash, expires_at) "
        "SELECT id, decode('11', 'hex'), now() + interval '1 day' FROM users"
    )
    await conn.execute(
        "INSERT INTO vapid_keys (public_key, private_key, subject) "
        "VALUES ('public', 'PRIVATE-SECRET-KEY', 'mailto:demo@example.com')"
    )
    await conn.execute(
        "INSERT INTO setup_tokens (token_hash, expires_at) "
        "VALUES (decode('22', 'hex'), now() + interval '1 day')"
    )
    await conn.execute(
        "INSERT INTO push_subscriptions (endpoint, p256dh, auth) "
        "VALUES ('https://push.example/SUBSCRIPTION-SECRET', 'p256', 'AUTH-SECRET')"
    )


async def test_no_credential_table_is_in_any_export(ledger):
    await _seed_secrets(ledger)

    names = set(await table_names(ledger))
    assert names & SECRET_TABLES == set()
    assert "audit_log" in names and "movements" in names

    payload = await export_json(ledger)
    assert set(payload["tables"]) & SECRET_TABLES == set()
    text = json.dumps(payload)
    assert "PRIVATE-SECRET-KEY" not in text
    assert "AUTH-SECRET" not in text
    assert "SUBSCRIPTION-SECRET" not in text

    blob = await export_csv_zip(ledger)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        namelist = archive.namelist()
        contents = b"\n".join(archive.read(name) for name in namelist)
    assert {f"{table}.csv" for table in SECRET_TABLES} & set(namelist) == set()
    assert b"audit_log.csv" in [name.encode() for name in namelist]
    assert b"PRIVATE-SECRET-KEY" not in contents
    assert b"AUTH-SECRET" not in contents
    assert b"SUBSCRIPTION-SECRET" not in contents


async def test_the_json_export_labels_its_money_unit(ledger):
    payload = await export_json(ledger)
    assert payload["money_unit"] == "paise"


# 7. UPI handles match without case -------------------------------------------------------------


async def test_a_upi_id_matches_without_case(ledger):
    ravi = await create_payee(ledger, display_name="Ravi", kind="person", upi_id="ravi@okaxis")

    found = await write_tools.resolve_payee(ledger, "Ravi", upi_id="RAVI@OkAxis")

    assert found["id"] == ravi["id"]
    assert await ledger.fetchval("SELECT count(*) FROM payees") == 1
