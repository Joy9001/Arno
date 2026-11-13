"""Movements: one real-world money movement, written as one balanced pgledger batch.

The kinds the agent and API can record are expense, income, transfer, split, loan, refund,
settlement and adjustment. A correction never edits a pgledger row: it appends a reversal and, for
an amount or account fix, a corrected movement in the same write group.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from arno.db.conn import Connection
from arno.ledger.audit import WriteGroup, groups_for_rows, write_group
from arno.ledger.categories import (
    _apply_payee_correction_in_group,
    resolve_category,
    upsert_rule_in_group,
)
from arno.ledger.common import (
    SYSTEM_KINDS,
    get_account_row,
    ledger_balance,
    resolve_account,
    system_account,
    today,
)
from arno.ledger.errors import ConflictLedgerError, InvalidLedgerError, NotFoundLedgerError
from arno.ledger.payees import ensure_person_account, get_payee
from arno.ledger.postings import MovementRecord, TransferSpec, create_movement_in_group

CORRECTABLE_SCOPES = ("this", "future", "all")
_LEDGER_CHANGE_KINDS = ("expense", "income", "transfer", "refund", "settlement", "split", "loan")
_LEDGER_CHANGE_KEYS = ("amount_paise", "account_id", "my_share_paise", "shares")


async def _movement_row(conn: Connection, movement_id: str) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM movements WHERE id = $1", movement_id)
    if row is None:
        raise NotFoundLedgerError(f"No movement {movement_id}")
    return row


async def _transfer_rows_for_movements(
    conn: Connection, movement_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    if not movement_ids:
        return {}
    # Routing `unnest($1::text[])` through a subquery creates an InitPlan so Postgres caches a
    # generic plan instead of re-planning every call (plain `= ANY($1::text[])` stayed at 0
    # generic plans and ran 1.78x slower than `= $1` on single-id calls vs 0.96x with the index).
    rows = await conn.fetch(
        """
            SELECT
                ml.movement_id,
                t.id,
                t.from_account_id AS from_ledger_id,
                t.to_account_id AS to_ledger_id,
                t.amount::bigint AS amount_paise,
                fa.id AS from_account_id,
                fa.name AS from_name,
                fa.kind AS from_kind,
                ta.id AS to_account_id,
                ta.name AS to_name,
                ta.kind AS to_kind,
                ml.category_id,
                c.name AS category_name,
                ml.payee_id,
                p.display_name AS payee_name,
                ml.expected_date,
                ml.note
            FROM movement_legs ml
            JOIN pgledger_transfers t ON t.id = ml.transfer_id
            JOIN v_accounts fa ON fa.ledger_account_id = t.from_account_id
            JOIN v_accounts ta ON ta.ledger_account_id = t.to_account_id
            LEFT JOIN categories c ON c.id = ml.category_id
            LEFT JOIN payees p ON p.id = ml.payee_id
            WHERE ml.movement_id = ANY(ARRAY(SELECT unnest($1::text[])))
            ORDER BY ml.movement_id, t.id
            """,
        movement_ids,
    )
    grouped: dict[str, list[dict[str, Any]]] = {movement_id: [] for movement_id in movement_ids}
    for row in rows:
        movement_id = str(row.pop("movement_id"))
        grouped.setdefault(movement_id, []).append(row)
    return grouped


async def _transfer_rows(conn: Connection, movement_id: str) -> list[dict[str, Any]]:
    return (await _transfer_rows_for_movements(conn, [movement_id]))[movement_id]


async def _capture_rows_for_movements(
    conn: Connection, movement_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    if not movement_ids:
        return {}
    # Same `ARRAY(SELECT unnest(...))` generic-plan idiom as `_transfer_rows_for_movements`.
    rows = await conn.fetch(
        """
        SELECT movement_id, kind, ref, upi_ref, confidence
        FROM captures
        WHERE movement_id = ANY(ARRAY(SELECT unnest($1::text[])))
        ORDER BY movement_id, id
        """,
        movement_ids,
    )
    grouped: dict[str, list[dict[str, Any]]] = {movement_id: [] for movement_id in movement_ids}
    for row in rows:
        movement_id = str(row.pop("movement_id"))
        grouped.setdefault(movement_id, []).append(row)
    return grouped


def _transfer_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "transfer_id": row["id"],
        "from_account_id": row["from_account_id"],
        "from_name": row["from_name"],
        "from_kind": row["from_kind"],
        "to_account_id": row["to_account_id"],
        "to_name": row["to_name"],
        "to_kind": row["to_kind"],
        "amount_paise": int(row["amount_paise"]),
        "category_id": row["category_id"],
        "category_name": row["category_name"],
        "payee_id": row["payee_id"],
        "payee_name": row["payee_name"],
        "expected_date": row["expected_date"].isoformat() if row["expected_date"] else None,
        "note": row["note"],
    }


async def _reversal_state(
    conn: Connection, movement_ids: list[str], *, as_of: date | None = None
) -> dict[str, dict[str, Any]]:
    """Whether each movement is effectively reversed, from `movement_reversal_state`."""
    if not movement_ids:
        return {}
    rows = await conn.fetch(
        """
        WITH state AS MATERIALIZED (
            SELECT movement_id, is_reversed
            FROM movement_reversal_state($2)
        )
        SELECT
            s.movement_id,
            s.is_reversed,
            CASE
                WHEN s.is_reversed THEN (
                    SELECT c.id
                    FROM movements c
                    LEFT JOIN state cs ON cs.movement_id = c.id
                    WHERE c.reverses_id = s.movement_id
                      AND ($2::date IS NULL OR c.date <= $2)
                      AND NOT coalesce(cs.is_reversed, false)
                    ORDER BY c.date DESC, c.id DESC
                    LIMIT 1
                )
                ELSE NULL
            END AS reversed_by
        FROM state s
        WHERE s.movement_id = ANY($1::text[])
        """,
        movement_ids,
        as_of,
    )
    by_id = {
        str(row["movement_id"]): {
            "reversed": bool(row["is_reversed"]),
            "reversed_by": str(row["reversed_by"]) if row["reversed_by"] is not None else None,
        }
        for row in rows
    }
    return {
        movement_id: by_id.get(movement_id, {"reversed": False, "reversed_by": None})
        for movement_id in movement_ids
    }


async def _effectively_reversed_ids(conn: Connection) -> list[str]:
    """Movements hidden from activity because a reversal still cancels them."""
    rows = await conn.fetch(
        "SELECT movement_id FROM movement_reversal_state() WHERE is_reversed ORDER BY movement_id"
    )
    return [str(row["movement_id"]) for row in rows]


def _assemble_movement_payload(
    movement: dict[str, Any],
    *,
    transfers: list[dict[str, Any]],
    captures: list[dict[str, Any]],
    state: dict[str, Any],
    undo_id: str | None,
) -> dict[str, Any]:
    movement_id = str(movement["id"])
    category = next(
        (row["category_name"] for row in transfers if row["category_name"] is not None), None
    )
    # The vendor is the label on the leg that touches Spending (my own share of a split). A
    # person's leg names who owes me, so it must never read as "paid to Asha".
    spending_leg = next(
        (row for row in transfers if "spending" in (row["from_kind"], row["to_kind"])),
        None,
    )
    payee = spending_leg["payee_name"] if spending_leg is not None else None
    if payee is None and movement["kind"] != "split":
        payee = next(
            (row["payee_name"] for row in transfers if row["payee_name"] is not None), None
        )
    return {
        "id": movement_id,
        "date": movement["date"].isoformat(),
        "kind": movement["kind"],
        "description": movement["description"],
        "reverses_id": movement["reverses_id"],
        "reversed_by": state.get("reversed_by"),
        "reversed": bool(state.get("reversed")),
        "amount_paise": sum(int(row["amount_paise"]) for row in transfers),
        "category_name": category,
        "payee_name": payee,
        "transfers": [_transfer_payload(row) for row in transfers],
        "evidence": [row["kind"] for row in captures],
        "undo_id": undo_id,
    }


async def movement_payloads(
    conn: Connection,
    movements: list[dict[str, Any]],
    *,
    states: dict[str, dict[str, Any]] | None = None,
    undo_ids: dict[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    if not movements:
        return []
    movement_ids = [str(row["id"]) for row in movements]
    transfers_by_id = await _transfer_rows_for_movements(conn, movement_ids)
    captures_by_id = await _capture_rows_for_movements(conn, movement_ids)
    resolved_states = states if states is not None else await _reversal_state(conn, movement_ids)
    resolved_undo_ids = (
        undo_ids if undo_ids is not None else await groups_for_rows(conn, "movements", movement_ids)
    )
    return [
        _assemble_movement_payload(
            row,
            transfers=transfers_by_id[str(row["id"])],
            captures=captures_by_id[str(row["id"])],
            state=resolved_states[str(row["id"])],
            undo_id=resolved_undo_ids[str(row["id"])],
        )
        for row in movements
    ]


async def movement_payload(
    conn: Connection,
    movement: dict[str, Any],
    *,
    state: dict[str, Any] | None = None,
    undo_ids: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    movement_id = str(movement["id"])
    states = {movement_id: state} if state is not None else None
    return (await movement_payloads(conn, [movement], states=states, undo_ids=undo_ids))[0]


async def get_movement(conn: Connection, movement_id: str) -> dict[str, Any]:
    return await movement_payload(conn, await _movement_row(conn, movement_id))


async def _finish_movement(
    conn: Connection,
    group: WriteGroup,
    record: MovementRecord,
    *,
    status: str = "recorded",
) -> dict[str, Any]:
    created_now = any(
        entry["table_name"] == "movements"
        and entry["row_id"] == record.id
        and entry["before"] is None
        for entry in group.entries
    )
    payload = await movement_payload(
        conn,
        record.movement,
        state={"reversed": False, "reversed_by": None} if created_now else None,
        undo_ids={record.id: group.group_id} if created_now else None,
    )
    undo_id = group.group_id if created_now else payload["undo_id"]
    return payload | {"status": status, "undo_id": undo_id}


def _movement_date(value: date | None) -> date:
    value = value or today()
    if value > today():
        raise InvalidLedgerError("Use a schedule for future payments")
    return value


async def _resolve_label_category(
    conn: Connection,
    group: WriteGroup,
    *,
    payee_id: str | None,
    category_id: str | None,
    category_guess_id: str | None,
    apply_rules: bool = True,
) -> dict[str, Any]:
    payee = await get_payee(conn, payee_id) if payee_id else None
    # An imported row is categorized after commit, so its write skips the rule lookup.
    if apply_rules:
        resolution = await resolve_category(
            conn, payee_id=payee_id, category_id=category_id, payee_row=payee
        )
    else:
        resolution = (
            await resolve_category(conn, category_id=category_id)
            if category_id
            else {"category_id": None, "category_name": None, "source": "none", "rule_id": None}
        )
    if resolution["category_id"] is None and category_guess_id:
        guessed = await resolve_category(conn, category_id=category_guess_id)
        resolution = {
            **resolution,
            "category_id": guessed["category_id"],
            "source": "guess",
        }
        if payee is not None:
            await upsert_rule_in_group(
                conn,
                group,
                pattern=payee["display_name"],
                category_id=category_guess_id,
                source="llm",
            )
    return resolution


async def record_expense(
    conn: Connection,
    *,
    account_id: str,
    amount_paise: int,
    description: str | None = None,
    payee_id: str | None = None,
    category_id: str | None = None,
    category_guess_id: str | None = None,
    date: date | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
    capture: dict[str, Any] | None = None,
    actor: str = "user",
    message_id: str | None = None,
    apply_rules: bool = True,
) -> dict[str, Any]:
    if amount_paise <= 0:
        raise InvalidLedgerError("An expense must be positive")
    when = _movement_date(date)
    account = await resolve_account(conn, account_id)
    if account["kind"] in {"spending", "income", "equity"}:
        raise InvalidLedgerError("An expense comes from a real account, not a system account")
    spending = await system_account(conn, "spending")
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        resolution = await _resolve_label_category(
            conn,
            group,
            payee_id=payee_id,
            category_id=category_id,
            category_guess_id=category_guess_id,
            apply_rules=apply_rules,
        )
        transfer = TransferSpec(
            from_account_id=account_id,
            to_account_id=spending["id"],
            amount_paise=amount_paise,
            category_id=resolution["category_id"],
            payee_id=payee_id,
            note=note,
        )
        record = await create_movement_in_group(
            conn,
            group,
            date=when,
            description=description or "Expense",
            kind="expense",
            transfers=[transfer],
            idempotency_key=idempotency_key,
            capture=capture,
            accounts={account["id"]: account, spending["id"]: spending},
        )
    finished = await _finish_movement(conn, group, record)
    return finished | {"category_source": resolution["source"]}


async def _validated_label(
    conn: Connection, category_id: str | None, payee_id: str | None
) -> tuple[str | None, str | None]:
    if category_id:
        category_id = (await resolve_category(conn, category_id=category_id))["category_id"]
    if payee_id:
        await get_payee(conn, payee_id)
    return category_id, payee_id


async def record_income(
    conn: Connection,
    *,
    account_id: str,
    amount_paise: int,
    description: str | None = None,
    payee_id: str | None = None,
    date: date | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
    capture: dict[str, Any] | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    if amount_paise <= 0:
        raise InvalidLedgerError("Income must be positive")
    when = _movement_date(date)
    account = await resolve_account(conn, account_id)
    if account["kind"] in SYSTEM_KINDS:
        raise InvalidLedgerError("Income arrives in a real account, not a system account")
    await _validated_label(conn, None, payee_id)
    income = await system_account(conn, "income")
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        transfer = TransferSpec(
            from_account_id=income["id"],
            to_account_id=account_id,
            amount_paise=amount_paise,
            payee_id=payee_id,
            note=note,
        )
        record = await create_movement_in_group(
            conn,
            group,
            date=when,
            description=description or "Income",
            kind="income",
            transfers=[transfer],
            idempotency_key=idempotency_key,
            capture=capture,
            accounts={income["id"]: income, account["id"]: account},
        )
    return await _finish_movement(conn, group, record)


async def record_transfer(
    conn: Connection,
    *,
    from_account_id: str,
    to_account_id: str,
    amount_paise: int,
    description: str | None = None,
    category_id: str | None = None,
    payee_id: str | None = None,
    date: date | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    if amount_paise <= 0:
        raise InvalidLedgerError("A transfer must be positive")
    when = _movement_date(date)
    from_account = await resolve_account(conn, from_account_id)
    to_account = await resolve_account(conn, to_account_id)
    category_id, payee_id = await _validated_label(conn, category_id, payee_id)
    if category_id is None and to_account["kind"] == "goal":
        # Money moved into a goal's fenced account is saving, not a plain transfer; every path
        # that records one must label it the same way.
        goal = await conn.fetchrow(
            "SELECT name FROM v_accounts WHERE id = $1 AND kind = 'goal'", to_account_id
        )
        if goal is not None:
            row = await conn.fetchrow(
                "SELECT id FROM categories WHERE name = 'Savings & Investments' AND active"
            )
            category_id = row["id"] if row else None
            description = description or f"Contribution to {goal['name']}"
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        transfer = TransferSpec(
            from_account_id=from_account_id,
            to_account_id=to_account_id,
            amount_paise=amount_paise,
            category_id=category_id,
            payee_id=payee_id,
            note=note,
        )
        record = await create_movement_in_group(
            conn,
            group,
            date=when,
            description=description or "Transfer",
            kind="transfer",
            transfers=[transfer],
            idempotency_key=idempotency_key,
            accounts={from_account["id"]: from_account, to_account["id"]: to_account},
        )
    return await _finish_movement(conn, group, record)


async def record_split(
    conn: Connection,
    *,
    account_id: str,
    my_share_paise: int,
    people: list[dict[str, Any]],
    total_paise: int | None = None,
    description: str | None = None,
    payee_id: str | None = None,
    category_id: str | None = None,
    category_guess_id: str | None = None,
    date: date | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
    capture: dict[str, Any] | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    if my_share_paise < 0:
        raise InvalidLedgerError("Your share cannot be negative")
    if not people:
        raise InvalidLedgerError("A split needs at least one person")
    calculated = my_share_paise + sum(int(person.get("amount_paise", 0)) for person in people)
    if total_paise is not None and total_paise != calculated:
        raise InvalidLedgerError("The split parts do not add up to the total")
    if calculated <= 0:
        raise InvalidLedgerError("A split must move money")
    when = _movement_date(date)
    account = await resolve_account(conn, account_id)
    spending = await system_account(conn, "spending")
    known_accounts: dict[str, dict[str, Any]] = {
        account["id"]: account,
        spending["id"]: spending,
    }

    async with write_group(conn, actor=actor, message_id=message_id) as group:
        resolution = await _resolve_label_category(
            conn,
            group,
            payee_id=payee_id,
            category_id=category_id,
            category_guess_id=category_guess_id,
        )
        transfers: list[TransferSpec] = []
        if my_share_paise:
            transfers.append(
                TransferSpec(
                    from_account_id=account_id,
                    to_account_id=spending["id"],
                    amount_paise=my_share_paise,
                    category_id=resolution["category_id"],
                    payee_id=payee_id,
                    note=note,
                )
            )
        for person in people:
            person_id = str(person["payee_id"])
            amount = int(person["amount_paise"])
            if amount <= 0:
                raise InvalidLedgerError("A split share must be positive")
            payee = await get_payee(conn, person_id)
            if payee["kind"] != "person":
                raise InvalidLedgerError("A split share goes to a person payee")
            person_account = await ensure_person_account(conn, person_id, group)
            known_accounts[person_account["id"]] = person_account
            expected = person.get("expected_date")
            transfers.append(
                TransferSpec(
                    from_account_id=account_id,
                    to_account_id=person_account["id"],
                    amount_paise=amount,
                    payee_id=person_id,
                    expected_date=expected,
                    note=note,
                )
            )
        record = await create_movement_in_group(
            conn,
            group,
            date=when,
            description=description or "Split expense",
            kind="split",
            transfers=transfers,
            idempotency_key=idempotency_key,
            capture=capture,
            accounts=known_accounts,
        )
    return await _finish_movement(conn, group, record)


async def record_lending(
    conn: Connection,
    *,
    account_id: str,
    payee_id: str,
    amount_paise: int,
    direction: str = "i_lent",
    expected_date: date | None = None,
    description: str | None = None,
    date: date | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    if amount_paise <= 0:
        raise InvalidLedgerError("A lending amount must be positive")
    if direction not in {"i_lent", "i_borrowed"}:
        raise InvalidLedgerError("direction is i_lent or i_borrowed")
    when = _movement_date(date)
    account = await resolve_account(conn, account_id)
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        person_account = await ensure_person_account(conn, payee_id, group)
        if direction == "i_lent":
            transfer = TransferSpec(
                from_account_id=account_id,
                to_account_id=person_account["id"],
                amount_paise=amount_paise,
                payee_id=payee_id,
                expected_date=expected_date,
                note=note,
            )
            text = description or "Lent money"
        else:
            transfer = TransferSpec(
                from_account_id=person_account["id"],
                to_account_id=account_id,
                amount_paise=amount_paise,
                payee_id=payee_id,
                expected_date=expected_date,
                note=note,
            )
            text = description or "Borrowed money"
        record = await create_movement_in_group(
            conn,
            group,
            date=when,
            description=text,
            kind="transfer",
            transfers=[transfer],
            idempotency_key=idempotency_key,
            accounts={account["id"]: account, person_account["id"]: person_account},
        )
    return await _finish_movement(conn, group, record)


async def record_settlement(
    conn: Connection,
    *,
    account_id: str,
    payee_id: str,
    amount_paise: int,
    direction: str = "they_paid",
    expected_date: date | None = None,
    description: str | None = None,
    date: date | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    if amount_paise <= 0:
        raise InvalidLedgerError("A settlement must be positive")
    if direction not in {"they_paid", "i_paid"}:
        raise InvalidLedgerError("direction is they_paid or i_paid")
    when = _movement_date(date)
    account = await resolve_account(conn, account_id)
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        person_account = await ensure_person_account(conn, payee_id, group)
        if direction == "they_paid":
            transfer = TransferSpec(
                from_account_id=person_account["id"],
                to_account_id=account_id,
                amount_paise=amount_paise,
                payee_id=payee_id,
                expected_date=expected_date,
                note=note,
            )
            text = description or "Settlement received"
        else:
            transfer = TransferSpec(
                from_account_id=account_id,
                to_account_id=person_account["id"],
                amount_paise=amount_paise,
                payee_id=payee_id,
                expected_date=expected_date,
                note=note,
            )
            text = description or "Settlement paid"
        record = await create_movement_in_group(
            conn,
            group,
            date=when,
            description=text,
            kind="settlement",
            transfers=[transfer],
            idempotency_key=idempotency_key,
            accounts={account["id"]: account, person_account["id"]: person_account},
        )
    return await _finish_movement(conn, group, record)


async def record_refund(
    conn: Connection,
    *,
    account_id: str,
    amount_paise: int,
    description: str | None = None,
    payee_id: str | None = None,
    category_id: str | None = None,
    date: date | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    if amount_paise <= 0:
        raise InvalidLedgerError("A refund must be positive")
    when = _movement_date(date)
    account = await resolve_account(conn, account_id)
    category_id, payee_id = await _validated_label(conn, category_id, payee_id)
    spending = await system_account(conn, "spending")
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        transfer = TransferSpec(
            from_account_id=spending["id"],
            to_account_id=account_id,
            amount_paise=amount_paise,
            category_id=category_id,
            payee_id=payee_id,
            note=note,
        )
        record = await create_movement_in_group(
            conn,
            group,
            date=when,
            description=description or "Refund",
            kind="refund",
            transfers=[transfer],
            idempotency_key=idempotency_key,
            accounts={spending["id"]: spending, account["id"]: account},
        )
    return await _finish_movement(conn, group, record)


async def record_loan_payment(
    conn: Connection,
    *,
    account_id: str,
    loan_account_id: str,
    amount_paise: int,
    principal_paise: int | None = None,
    interest_paise: int | None = None,
    interest_category_id: str | None = None,
    description: str | None = None,
    date: date | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    if amount_paise <= 0:
        raise InvalidLedgerError("A loan payment must be positive")
    if principal_paise is not None and interest_paise is not None:
        if principal_paise + interest_paise != amount_paise:
            raise InvalidLedgerError("Principal and interest must add up to the payment")
    elif principal_paise is not None:
        interest_paise = amount_paise - principal_paise
    elif interest_paise is not None:
        principal_paise = amount_paise - interest_paise
    else:
        principal_paise = amount_paise
        interest_paise = 0
    if principal_paise < 0 or interest_paise < 0:
        raise InvalidLedgerError("Principal and interest cannot be negative")
    if principal_paise == 0 and interest_paise == 0:
        raise InvalidLedgerError("A loan payment needs principal or interest")

    when = _movement_date(date)
    account = await resolve_account(conn, account_id)
    loan = await resolve_account(conn, loan_account_id)
    if loan["kind"] != "loan":
        raise InvalidLedgerError("The destination account is not a loan")
    spending = await system_account(conn, "spending")
    if interest_paise and interest_category_id is None:
        row = await conn.fetchrow(
            "SELECT id FROM categories WHERE name = 'Loan Interest' AND active"
        )
        interest_category_id = row["id"] if row else None
    interest_category_id, _ = await _validated_label(conn, interest_category_id, None)

    transfers: list[TransferSpec] = []
    if principal_paise:
        transfers.append(
            TransferSpec(
                from_account_id=account_id,
                to_account_id=loan_account_id,
                amount_paise=principal_paise,
                note=note,
            )
        )
    if interest_paise:
        transfers.append(
            TransferSpec(
                from_account_id=account_id,
                to_account_id=spending["id"],
                amount_paise=interest_paise,
                category_id=interest_category_id,
                note=note,
            )
        )
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        record = await create_movement_in_group(
            conn,
            group,
            date=when,
            description=description or "Loan EMI",
            kind="loan",
            transfers=transfers,
            idempotency_key=idempotency_key,
            accounts={account["id"]: account, loan["id"]: loan, spending["id"]: spending},
        )
    return await _finish_movement(conn, group, record)


async def loan_payment_parts(
    conn: Connection, loan: dict[str, Any], amount_paise: int, *, on: date
) -> tuple[int, int]:
    """Principal and interest of one loan payment, the single rule every path shares.

    A loan is a liability, so its ledger balance is negative: the outstanding principal is the
    negated balance. The monthly rate is the annual rate over twelve, and it is booked once per
    calendar month per loan: the first payment in a month carries the month's interest, and a
    later payment in the same month is a prepayment, all principal. A loan with no rate has
    unknown interest, so the payment is all principal; the caller says so instead of implying
    zero interest.
    """
    outstanding = max(-await ledger_balance(conn, loan["ledger_account_id"]), 0)
    if not loan["rate_bps"]:
        return amount_paise, 0
    if await _interest_booked(conn, loan, on):
        return amount_paise, 0
    monthly = Decimal(loan["rate_bps"]) / Decimal(10000) / Decimal(12)
    interest = int((Decimal(outstanding) * monthly).quantize(Decimal("1")))
    interest = min(interest, amount_paise - 1) if amount_paise > 1 else 0
    return amount_paise - interest, interest


async def _interest_booked(conn: Connection, loan: dict[str, Any], on: date) -> bool:
    """Whether a live loan payment this calendar month already booked the month's interest.

    A payment that booked interest has a leg into Spending (the interest is consumption); a
    prepayment does not. A reversed payment booked nothing.
    """
    start = on.replace(day=1)
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return bool(
        await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM movements m
                JOIN v_movement_state ms ON ms.movement_id = m.id
                WHERE m.kind = 'loan' AND m.date >= $2 AND m.date < $3
                  AND ms.is_effective
                  AND EXISTS (
                      SELECT 1 FROM movement_legs ml
                      JOIN pgledger_transfers tp ON tp.id = ml.transfer_id
                      WHERE ml.movement_id = m.id AND tp.to_account_id = $1
                  )
                  AND EXISTS (
                      SELECT 1 FROM movement_legs ml
                      JOIN pgledger_transfers ti ON ti.id = ml.transfer_id
                      JOIN accounts ta ON ta.ledger_account_id = ti.to_account_id
                      WHERE ml.movement_id = m.id AND ta.kind = 'spending'
                  )
            )
            """,
            loan["ledger_account_id"],
            start,
            end,
        )
    )


async def record_goal_contribution(
    conn: Connection,
    *,
    account_id: str,
    goal_account_id: str,
    amount_paise: int,
    description: str | None = None,
    date: date | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    goal = await get_account_row(conn, goal_account_id)
    if goal["kind"] != "goal":
        raise InvalidLedgerError("A goal contribution goes into a goal account")
    # `record_transfer` labels a move into a goal account as saving by default.
    return await record_transfer(
        conn,
        from_account_id=account_id,
        to_account_id=goal_account_id,
        amount_paise=amount_paise,
        description=description,
        date=date,
        note=note,
        idempotency_key=idempotency_key,
        actor=actor,
        message_id=message_id,
    )


async def reverse_movement(
    group: WriteGroup,
    movement_id: str,
    *,
    note: str | None = None,
    date: date | None = None,
) -> MovementRecord:
    """Append the opposite transfer batch in one movement."""
    conn = group.conn
    movement = await conn.fetchrow(
        "SELECT * FROM movements WHERE id = $1 FOR UPDATE",
        movement_id,
    )
    if movement is None:
        raise NotFoundLedgerError(f"No movement {movement_id}")
    # Undo-of-undo reverses a reversal, and that target is active. A movement that a live
    # reversal already cancels has no money to reverse: reversing it again would mint money.
    if (await _reversal_state(conn, [movement_id]))[movement_id]["reversed"]:
        raise ConflictLedgerError(
            "That entry was already corrected or removed; undo that change first."
        )
    transfers = await _transfer_rows(conn, movement_id)
    if not transfers:
        raise InvalidLedgerError(f"Movement {movement_id} has no transfers")
    opposite: list[TransferSpec] = []
    for row in transfers:
        opposite.append(
            TransferSpec(
                from_account_id=row["to_account_id"],
                to_account_id=row["from_account_id"],
                amount_paise=int(row["amount_paise"]),
                category_id=row["category_id"],
                payee_id=row["payee_id"],
                expected_date=row["expected_date"],
                note=note if note is not None else row["note"],
            )
        )
    return await create_movement_in_group(
        conn,
        group,
        date=date or movement["date"],
        description=f"Reversal of {movement['description']}",
        kind="reversal",
        transfers=opposite,
        reverses_id=movement_id,
    )


async def _update_labels(
    conn: Connection,
    group: WriteGroup,
    movement_id: str,
    values: dict[str, Any],
) -> int:
    updates = {
        key: value
        for key, value in values.items()
        if key in {"category_id", "payee_id", "expected_date", "note"}
    }
    if not updates:
        return 0
    legs = await conn.fetch(
        "SELECT * FROM movement_legs WHERE movement_id = $1 ORDER BY transfer_id",
        movement_id,
    )
    if "payee_id" in updates and legs:
        # A split labels each person's share with that person; the vendor is the label on my own
        # share. Renaming the vendor must not rename who owes what.
        person_transfers = {
            row["transfer_id"]
            for row in await conn.fetch(
                """
                SELECT ml.transfer_id
                FROM movement_legs ml
                JOIN pgledger_transfers t ON t.id = ml.transfer_id
                JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
                JOIN accounts ta ON ta.ledger_account_id = t.to_account_id
                WHERE ml.movement_id = $1 AND 'person' IN (fa.kind, ta.kind)
                """,
                movement_id,
            )
        }
        vendor_legs = [leg for leg in legs if leg["transfer_id"] not in person_transfers]
        if not vendor_legs:
            raise InvalidLedgerError(
                "That movement's payee is a person, so a new payee would change who owes what; "
                "delete it and record it again instead"
            )
        legs = vendor_legs
    if not legs:
        raise InvalidLedgerError("That movement has no transfer to label")

    changed = 0
    for leg in legs:
        columns = list(updates)
        assignments = ", ".join(
            f"{column} = ${index}" for index, column in enumerate(columns, start=1)
        )
        after = await conn.fetchrow(
            f"""
            UPDATE movement_legs
            SET {assignments}, updated_at = now()
            WHERE transfer_id = ${len(columns) + 1} RETURNING *
            """,
            *[updates[column] for column in columns],
            leg["transfer_id"],
        )
        if after is None:  # pragma: no cover - the leg was read in this transaction
            raise InvalidLedgerError("The leg disappeared mid-correction")
        group.record_updated("movement_legs", leg["transfer_id"], leg, after)
        changed += 1
    return changed


def _corrected_split_shares(
    *,
    total_paise: int,
    people: list[dict[str, Any]],
    original_mine: int,
    my_share_paise: int | None,
    shares: list[dict[str, Any]] | None,
) -> tuple[int, dict[str, int]]:
    """The shares of a corrected split: explicit ones, or an equal re-split of an equal split.

    The equal rule is deterministic: every person pays the floor of the division and my share
    takes the remainder, so ₹100 three ways is 3334 + 3333 + 3333 and no paise is lost or
    invented. A split that was not equal cannot be re-split without naming each person's share.
    """
    original = {str(row["payee_id"]): int(row["amount_paise"]) for row in people}
    if shares is not None:
        named: dict[str, int] = {}
        for share in shares:
            payee_id = str(share["payee_id"])
            if payee_id in named:
                raise InvalidLedgerError("A corrected split names each person's share once")
            named[payee_id] = int(share["amount_paise"])
        if set(named) != set(original):
            raise InvalidLedgerError(
                "A corrected split names each person's share, and only the people already on it; "
                "delete it and record it again to change who was in it"
            )
        mine = total_paise - sum(named.values())
        if my_share_paise is not None and my_share_paise != mine:
            raise InvalidLedgerError("The split parts do not add up to the total")
        if mine < 0:
            raise InvalidLedgerError("The split parts do not add up to the total")
        return mine, named
    if my_share_paise is not None:
        raise InvalidLedgerError(
            "A new total for a split needs each person's share too, because theirs may change"
        )
    base, remainder = divmod(original_mine + sum(original.values()), len(original) + 1)
    if original_mine != base + remainder or any(value != base for value in original.values()):
        raise InvalidLedgerError(
            "That split was not equal, so a new total needs each person's share"
        )
    base, remainder = divmod(total_paise, len(original) + 1)
    if base <= 0:
        raise InvalidLedgerError("That total is too small to split between these people")
    return base + remainder, {payee_id: base for payee_id in original}


async def _loan_interest_category(
    conn: Connection, interest_row: dict[str, Any] | None
) -> str | None:
    """The category a loan's interest lands in, as `record_loan_payment` picks it."""
    if interest_row is not None and interest_row["category_id"]:
        return str(interest_row["category_id"])
    row = await conn.fetchrow("SELECT id FROM categories WHERE name = 'Loan Interest' AND active")
    return row["id"] if row else None


async def _corrected_transfer_specs(
    conn: Connection,
    movement: dict[str, Any],
    transfers: list[dict[str, Any]],
    *,
    amount_paise: int | None,
    account_id: str | None,
    labels: dict[str, Any],
    date: date,
    my_share_paise: int | None = None,
    shares: list[dict[str, Any]] | None = None,
) -> list[TransferSpec]:
    kind = movement["kind"]
    if kind not in _LEDGER_CHANGE_KINDS:
        raise InvalidLedgerError(f"Amount and account corrections are not supported for {kind}")

    def make(from_id: str, to_id: str, amount: int) -> TransferSpec:
        return TransferSpec(
            from_account_id=from_id,
            to_account_id=to_id,
            amount_paise=amount,
            category_id=labels.get("category_id"),
            payee_id=labels.get("payee_id"),
            expected_date=labels.get("expected_date"),
            note=labels.get("note"),
        )

    if kind == "expense":
        row = next(row for row in transfers if row["to_kind"] == "spending")
        return [
            make(
                account_id or row["from_account_id"],
                row["to_account_id"],
                amount_paise or int(row["amount_paise"]),
            )
        ]
    if kind == "refund":
        row = next(row for row in transfers if row["from_kind"] == "spending")
        return [
            make(
                row["from_account_id"],
                account_id or row["to_account_id"],
                amount_paise or int(row["amount_paise"]),
            )
        ]
    if kind == "income":
        row = next(row for row in transfers if row["from_kind"] == "income")
        return [
            make(
                row["from_account_id"],
                account_id or row["to_account_id"],
                amount_paise or int(row["amount_paise"]),
            )
        ]
    if kind == "transfer":
        row = transfers[0]
        if account_id is not None:
            raise InvalidLedgerError("A transfer correction names an amount, not one account")
        return [
            make(
                row["from_account_id"],
                row["to_account_id"],
                amount_paise or int(row["amount_paise"]),
            )
        ]
    if kind == "settlement":
        row = transfers[0]
        if row["from_kind"] == "person":
            return [
                make(
                    row["from_account_id"],
                    account_id or row["to_account_id"],
                    amount_paise or int(row["amount_paise"]),
                )
            ]
        return [
            make(
                account_id or row["from_account_id"],
                row["to_account_id"],
                amount_paise or int(row["amount_paise"]),
            )
        ]
    if kind == "split":
        people = [row for row in transfers if row["to_kind"] == "person"]
        if not people:
            raise InvalidLedgerError("That split has no person's share to correct")
        mine_row = next((row for row in transfers if row["to_kind"] == "spending"), None)
        original_mine = int(mine_row["amount_paise"]) if mine_row else 0
        old_total = original_mine + sum(int(row["amount_paise"]) for row in people)
        total = amount_paise if amount_paise is not None else old_total
        mine, person_shares = _corrected_split_shares(
            total_paise=total,
            people=people,
            original_mine=original_mine,
            my_share_paise=my_share_paise,
            shares=shares,
        )
        specs: list[TransferSpec] = []
        if mine:
            from_id = account_id or (
                mine_row["from_account_id"] if mine_row else people[0]["from_account_id"]
            )
            to_id = mine_row["to_account_id"] if mine_row else None
            if to_id is None:
                spending = await system_account(conn, "spending")
                to_id = spending["id"]
            specs.append(make(from_id, to_id, mine))
        for row in people:
            amount = person_shares[str(row["payee_id"])]
            if amount <= 0:
                raise InvalidLedgerError("A split share must be positive")
            specs.append(
                TransferSpec(
                    from_account_id=account_id or row["from_account_id"],
                    to_account_id=row["to_account_id"],
                    amount_paise=amount,
                    payee_id=str(row["payee_id"]),
                    expected_date=row["expected_date"],
                    note=row["note"],
                )
            )
        return specs
    if kind == "loan":
        principal_row = next((row for row in transfers if row["to_kind"] == "loan"), None)
        if principal_row is None:
            raise InvalidLedgerError("That loan payment has no principal transfer")
        interest_row = next((row for row in transfers if row["to_kind"] == "spending"), None)
        total = (
            amount_paise
            if amount_paise is not None
            else sum(int(row["amount_paise"]) for row in transfers)
        )
        loan = await resolve_account(conn, principal_row["to_account_id"])
        principal, interest = await loan_payment_parts(conn, loan, total, on=date)
        paid_from = account_id or principal_row["from_account_id"]
        specs = [
            TransferSpec(
                from_account_id=paid_from,
                to_account_id=principal_row["to_account_id"],
                amount_paise=principal,
                note=labels.get("note"),
            )
        ]
        if interest:
            # The interest is consumption, so it takes the category the original carried (or the
            # default loan-interest one) rather than leaving it uncategorized.
            to_id = (
                interest_row["to_account_id"]
                if interest_row
                else (await system_account(conn, "spending"))["id"]
            )
            category_id = labels.get("category_id") or await _loan_interest_category(
                conn, interest_row
            )
            specs.append(
                TransferSpec(
                    from_account_id=paid_from,
                    to_account_id=to_id,
                    amount_paise=interest,
                    category_id=category_id,
                    note=labels.get("note"),
                )
            )
        return specs
    raise InvalidLedgerError(f"Amount and account corrections are not supported for {kind}")


async def correct_movement(
    conn: Connection,
    movement_id: str,
    *,
    changes: dict[str, Any],
    scope: str = "this",
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    if scope not in CORRECTABLE_SCOPES:
        raise InvalidLedgerError("scope is this, future, or all")
    if changes.get("date") is not None:
        changes = {**changes, "date": _movement_date(changes["date"])}
    if changes.get("amount_paise") is not None and changes["amount_paise"] <= 0:
        raise InvalidLedgerError("A corrected amount must be positive")
    if changes.get("my_share_paise") is not None and changes["my_share_paise"] < 0:
        raise InvalidLedgerError("Your share cannot be negative")
    movement = await _movement_row(conn, movement_id)
    if movement["kind"] == "reversal":
        raise InvalidLedgerError("A reversal is not edited")
    state = (await _reversal_state(conn, [movement_id]))[movement_id]
    if state["reversed"]:
        raise ConflictLedgerError("That movement has already been reversed")

    transfers = await _transfer_rows(conn, movement_id)
    existing_labels = next(
        (
            {
                "category_id": row["category_id"],
                "payee_id": row["payee_id"],
                "expected_date": row["expected_date"],
                "note": row["note"],
            }
            for row in transfers
            if any(
                row[key] is not None for key in ("category_id", "payee_id", "expected_date", "note")
            )
        ),
        {"category_id": None, "payee_id": None, "expected_date": None, "note": None},
    )
    merged_labels = {
        "category_id": changes.get("category_id", existing_labels["category_id"]),
        "payee_id": changes.get("payee_id", existing_labels["payee_id"]),
        "expected_date": changes.get("expected_date", existing_labels["expected_date"]),
        "note": changes.get("note", existing_labels["note"]),
    }
    category_id = merged_labels["category_id"]
    payee_id = merged_labels["payee_id"]
    if category_id:
        category_id = (await resolve_category(conn, category_id=category_id))["category_id"]
    if payee_id:
        await get_payee(conn, payee_id)
    if scope != "this" and (category_id is None or payee_id is None):
        raise InvalidLedgerError("A future or all correction needs both a payee and a category")

    label_only = not any(
        changes.get(key) is not None for key in _LEDGER_CHANGE_KEYS
    ) and not changes.get("delete")
    rows_changed = 0
    label_changes = {
        key: merged_labels[key]
        for key in ("category_id", "payee_id", "expected_date", "note")
        if key in changes
    }

    async with write_group(conn, actor=actor, message_id=message_id) as group:
        if label_only:
            if changes.get("date") is not None:
                before = movement
                after = await conn.fetchrow_required(
                    "UPDATE movements SET date = $1 WHERE id = $2 RETURNING *",
                    changes["date"],
                    movement_id,
                )
                group.record_updated("movements", movement_id, before, after)
                rows_changed = 1
            if label_changes:
                rows_changed += await _update_labels(conn, group, movement_id, label_changes)
            result_movement_id = movement_id
            reversal_id = None
        else:
            reversal = await reverse_movement(group, movement_id)
            reversal_id = reversal.id
            if changes.get("delete"):
                result_movement_id = reversal.id
            else:
                corrected = await _corrected_transfer_specs(
                    conn,
                    movement,
                    transfers,
                    amount_paise=changes.get("amount_paise"),
                    account_id=changes.get("account_id"),
                    labels=merged_labels,
                    date=changes.get("date") or movement["date"],
                    my_share_paise=changes.get("my_share_paise"),
                    shares=changes.get("shares"),
                )
                record = await create_movement_in_group(
                    conn,
                    group,
                    date=changes.get("date") or movement["date"],
                    description=movement["description"],
                    kind=movement["kind"],
                    transfers=corrected,
                )
                result_movement_id = record.id
            rows_changed = 1

        if scope != "this":
            correction = await _apply_payee_correction_in_group(
                conn,
                group,
                payee_id=payee_id,
                category_id=category_id,
                scope=scope,
            )
            rows_changed += int(correction["rows_changed"])
        else:
            correction = {"rule": None, "rows_changed": 0}

    payload = await movement_payload(conn, await _movement_row(conn, result_movement_id))
    return {
        "status": "corrected" if not changes.get("delete") else "deleted",
        "movement_id": result_movement_id,
        "reversal_id": reversal_id,
        "rows_changed": rows_changed,
        "rule": correction["rule"],
        "movement": payload,
        "undo_id": group.group_id,
    }


async def ledger_span(conn: Connection) -> dict[str, Any]:
    """How much the ledger holds: movements, spending rows, and the first date.

    The first-run screens read it: an empty ledger has nothing to notice or recap, and a recap
    period before the first row has nothing to tell.
    """
    # ponytail: counts include movements a later reversal cancelled; the screens only ask
    # "anything yet?" and "since when?", where that is close enough.
    # An aggregate with no GROUP BY always yields its one row.
    row = await conn.fetchrow_required(
        """
        SELECT count(*) FILTER (WHERE NOT ms.is_reversal) AS movements,
               count(*) FILTER (WHERE m.kind = 'expense') AS spending,
               min(m.date) FILTER (WHERE NOT ms.is_reversal) AS first_date
        FROM movements m
        JOIN v_movement_state ms ON ms.movement_id = m.id
        """
    )
    return {
        "movements": int(row["movements"]),
        "spending": int(row["spending"]),
        "first_date": row["first_date"].isoformat() if row["first_date"] else None,
    }


async def list_movements(
    conn: Connection,
    *,
    start: date | None = None,
    end: date | None = None,
    account_id: str | None = None,
    category_id: str | None = None,
    payee_id: str | None = None,
    kind: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    include_reversed: bool = False,
) -> dict[str, Any]:
    if limit < 1 or limit > 200:
        raise InvalidLedgerError("limit must be between 1 and 200")
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        params.append(start)
        clauses.append(f"m.date >= ${len(params)}")
    if end is not None:
        params.append(end)
        clauses.append(f"m.date <= ${len(params)}")
    if kind is not None:
        params.append(kind)
        clauses.append(f"m.kind = ${len(params)}")
    if account_id is not None:
        params.extend([account_id, account_id])
        clauses.append(
            f"""
            EXISTS (
                SELECT 1 FROM movement_legs ml
                JOIN pgledger_transfers tx ON tx.id = ml.transfer_id
                JOIN accounts fa ON fa.ledger_account_id = tx.from_account_id
                JOIN accounts ta ON ta.ledger_account_id = tx.to_account_id
                WHERE ml.movement_id = m.id
                  AND (fa.id = ${len(params) - 1} OR ta.id = ${len(params)})
            )
            """
        )
    if category_id is not None or payee_id is not None:
        label_clauses = ["ml.movement_id = m.id"]
        if category_id is not None:
            params.append(category_id)
            label_clauses.append(f"ml.category_id = ${len(params)}")
        if payee_id is not None:
            params.append(payee_id)
            label_clauses.append(f"ml.payee_id = ${len(params)}")
        clauses.append(
            f"EXISTS (SELECT 1 FROM movement_legs ml WHERE {' AND '.join(label_clauses)})"
        )
    if not include_reversed:
        # In SQL, not after LIMIT, so a page is never short and total_count agrees.
        hidden = [
            str(row["movement_id"])
            for row in await conn.fetch(
                "SELECT movement_id FROM v_movement_state WHERE NOT is_effective"
            )
        ]
        if hidden:
            params.append(hidden)
            clauses.append(f"NOT (m.id = ANY(${len(params)}))")

    cursor_key: tuple[date, str] | None = None
    if cursor:
        try:
            cursor_date, cursor_id = cursor.split("|", 1)
            cursor_key = (date.fromisoformat(cursor_date), cursor_id)
        except (ValueError, TypeError) as exc:
            raise InvalidLedgerError("Invalid cursor") from exc

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total_row = await conn.fetchrow(
        f"SELECT count(*) AS n FROM movements m {where}",  # noqa: S608
        *params,
    )
    total = int(total_row["n"]) if total_row is not None else 0

    page_where = where
    page_params = list(params)
    if cursor_key is not None:
        page_params.extend(cursor_key)
        cursor_clause = f"(m.date, m.id) < (${len(page_params) - 1}, ${len(page_params)})"
        page_where = f"WHERE {' AND '.join([*clauses, cursor_clause])}"
    page_params.append(limit + 1)
    rows = await conn.fetch(
        f"""
        SELECT m.* FROM movements m {page_where}
        ORDER BY m.date DESC, m.id DESC
        LIMIT ${len(page_params)}
        """,  # noqa: S608
        *page_params,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    states = await _reversal_state(conn, [str(row["id"]) for row in rows])
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = f"{last['date'].isoformat()}|{last['id']}"
    items = await movement_payloads(conn, rows, states=states)
    return {
        "items": items,
        "total_count": total,
        "next_cursor": next_cursor,
        "as_of": today().isoformat(),
    }
