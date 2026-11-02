"""Movements: one real-world money movement, written as one balanced pgledger batch.

The kinds the agent and API can record are expense, income, transfer, split, loan, refund,
settlement and adjustment. A correction never edits a pgledger row: it appends a reversal and, for
an amount or account fix, a corrected movement in the same write group.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import psycopg

from fin_agent.ledger.audit import WriteGroup, group_for_row, write_group
from fin_agent.ledger.categories import (
    _apply_payee_correction_in_group,
    _upsert_rule_in_group,
    resolve_category,
)
from fin_agent.ledger.common import (
    get_account_row,
    resolve_account,
    system_account,
    today,
)
from fin_agent.ledger.errors import ConflictLedgerError, InvalidLedgerError, NotFoundLedgerError
from fin_agent.ledger.payees import ensure_person_account, get_payee
from fin_agent.ledger.postings import MovementRecord, TransferSpec, create_movement_in_group

CORRECTABLE_SCOPES = ("this", "future", "all")
_LEDGER_CHANGE_KINDS = ("expense", "income", "transfer", "refund", "settlement")


def _movement_row(conn: psycopg.Connection, movement_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM movements WHERE id = %s", (movement_id,)).fetchone()
    if row is None:
        raise NotFoundLedgerError(f"No movement {movement_id}")
    return row


def _transfer_rows(conn: psycopg.Connection, movement_id: str) -> list[dict[str, Any]]:
    return list(
        conn.execute(
            """
            SELECT
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
                tl.transfer_id AS label_id,
                tl.category_id,
                c.name AS category_name,
                tl.payee_id,
                p.display_name AS payee_name,
                tl.expected_date,
                tl.note
            FROM pgledger_transfers t
            JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
            JOIN accounts ta ON ta.ledger_account_id = t.to_account_id
            LEFT JOIN transfer_labels tl ON tl.transfer_id = t.id
            LEFT JOIN categories c ON c.id = tl.category_id
            LEFT JOIN payees p ON p.id = tl.payee_id
            WHERE t.metadata ->> 'movement_id' = %s
            ORDER BY t.id
            """,
            (movement_id,),
        ).fetchall()
    )


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


def _reversal_state(conn: psycopg.Connection, movement_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Whether each movement is effectively reversed, following undo-of-undo chains.

    A movement is reversed when its reversal is active. Reversing that reversal makes the original
    active again, so the answer is the parity of the longest chain of reversal children.
    """
    if not movement_ids:
        return {}
    rows = list(
        conn.execute(
            """
            WITH RECURSIVE descendants AS (
                SELECT m.id AS root, m.id AS node, 0 AS depth
                FROM movements m
                WHERE m.id = ANY(%s::text[])
                UNION ALL
                SELECT d.root, child.id, d.depth + 1
                FROM descendants d
                JOIN movements child ON child.reverses_id = d.node
            )
            SELECT d.root, d.node, d.depth,
                   (SELECT count(*) FROM movements x WHERE x.reverses_id = d.node) AS child_count
            FROM descendants d
            """,
            (movement_ids,),
        ).fetchall()
    )
    by_root: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_root.setdefault(str(row["root"]), []).append(row)

    result: dict[str, dict[str, Any]] = {}
    for root in movement_ids:
        nodes = by_root.get(root, [])
        leaves = [row for row in nodes if int(row["child_count"]) == 0]
        depth = max((int(row["depth"]) for row in leaves), default=0)
        direct = next((str(row["node"]) for row in nodes if int(row["depth"]) == 1), None)
        result[root] = {
            "reversed": depth % 2 == 1,
            "reversed_by": direct if depth % 2 == 1 else None,
            "depth": depth,
        }
    return result


def movement_payload(
    conn: psycopg.Connection,
    movement: dict[str, Any],
    *,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    movement_id = str(movement["id"])
    transfers = _transfer_rows(conn, movement_id)
    captures = list(
        conn.execute(
            """
            SELECT kind, ref, upi_ref, confidence
            FROM captures
            WHERE movement_id = %s
            ORDER BY id
            """,
            (movement_id,),
        ).fetchall()
    )
    state = state or _reversal_state(conn, [movement_id])[movement_id]
    category = next(
        (row["category_name"] for row in transfers if row["category_name"] is not None), None
    )
    payee = next((row["payee_name"] for row in transfers if row["payee_name"] is not None), None)
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
        "undo_id": group_for_row(conn, "movements", movement_id),
    }


def get_movement(conn: psycopg.Connection, movement_id: str) -> dict[str, Any]:
    return movement_payload(conn, _movement_row(conn, movement_id))


def _finish_movement(
    conn: psycopg.Connection,
    group: WriteGroup,
    record: MovementRecord,
    *,
    status: str = "recorded",
) -> dict[str, Any]:
    payload = movement_payload(conn, record.movement)
    undo_id = group.group_id if group.entries else payload["undo_id"]
    return payload | {"status": status, "undo_id": undo_id}


def _movement_date(value: date | None) -> date:
    value = value or today()
    if value > today():
        raise InvalidLedgerError("Use a schedule for future payments")
    return value


def _resolve_label_category(
    conn: psycopg.Connection,
    group: WriteGroup,
    *,
    payee_id: str | None,
    category_id: str | None,
    category_guess_id: str | None,
) -> dict[str, Any]:
    if payee_id:
        get_payee(conn, payee_id)
    resolution = resolve_category(conn, payee_id=payee_id, category_id=category_id)
    if resolution["category_id"] is None and category_guess_id:
        guessed = resolve_category(conn, category_id=category_guess_id)
        resolution = {
            **resolution,
            "category_id": guessed["category_id"],
            "source": "guess",
        }
        if payee_id:
            payee = get_payee(conn, payee_id)
            _upsert_rule_in_group(
                conn,
                group,
                merchant_pattern=payee["display_name"],
                category_id=category_guess_id,
                source="llm",
            )
    return resolution


def record_expense(
    conn: psycopg.Connection,
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
) -> dict[str, Any]:
    if amount_paise <= 0:
        raise InvalidLedgerError("An expense must be positive")
    when = _movement_date(date)
    account = resolve_account(conn, account_id)
    if account["kind"] in {"spending", "income", "equity"}:
        raise InvalidLedgerError("An expense comes from a real account, not a system account")
    spending = system_account(conn, "spending")
    with write_group(conn, actor=actor, message_id=message_id) as group:
        resolution = _resolve_label_category(
            conn,
            group,
            payee_id=payee_id,
            category_id=category_id,
            category_guess_id=category_guess_id,
        )
        transfer = TransferSpec(
            from_account_id=account_id,
            to_account_id=spending["id"],
            amount_paise=amount_paise,
            category_id=resolution["category_id"],
            payee_id=payee_id,
            note=note,
        )
        record = create_movement_in_group(
            conn,
            group,
            date=when,
            description=description or "Expense",
            kind="expense",
            transfers=[transfer],
            idempotency_key=idempotency_key,
            capture=capture,
        )
    return _finish_movement(conn, group, record) | {"category_source": resolution["source"]}


def _validated_label(
    conn: psycopg.Connection, category_id: str | None, payee_id: str | None
) -> tuple[str | None, str | None]:
    if category_id:
        category_id = resolve_category(conn, category_id=category_id)["category_id"]
    if payee_id:
        get_payee(conn, payee_id)
    return category_id, payee_id


def record_income(
    conn: psycopg.Connection,
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
    resolve_account(conn, account_id)
    _validated_label(conn, None, payee_id)
    income = system_account(conn, "income")
    with write_group(conn, actor=actor, message_id=message_id) as group:
        transfer = TransferSpec(
            from_account_id=income["id"],
            to_account_id=account_id,
            amount_paise=amount_paise,
            payee_id=payee_id,
            note=note,
        )
        record = create_movement_in_group(
            conn,
            group,
            date=when,
            description=description or "Income",
            kind="income",
            transfers=[transfer],
            idempotency_key=idempotency_key,
            capture=capture,
        )
    return _finish_movement(conn, group, record)


def record_transfer(
    conn: psycopg.Connection,
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
    resolve_account(conn, from_account_id)
    resolve_account(conn, to_account_id)
    category_id, payee_id = _validated_label(conn, category_id, payee_id)
    with write_group(conn, actor=actor, message_id=message_id) as group:
        transfer = TransferSpec(
            from_account_id=from_account_id,
            to_account_id=to_account_id,
            amount_paise=amount_paise,
            category_id=category_id,
            payee_id=payee_id,
            note=note,
        )
        record = create_movement_in_group(
            conn,
            group,
            date=when,
            description=description or "Transfer",
            kind="transfer",
            transfers=[transfer],
            idempotency_key=idempotency_key,
        )
    return _finish_movement(conn, group, record)


def record_split(
    conn: psycopg.Connection,
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
    resolve_account(conn, account_id)
    spending = system_account(conn, "spending")

    with write_group(conn, actor=actor, message_id=message_id) as group:
        resolution = _resolve_label_category(
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
            payee = get_payee(conn, person_id)
            if payee["kind"] != "person":
                raise InvalidLedgerError("A split share goes to a person payee")
            person_account = ensure_person_account(conn, person_id, group)
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
        record = create_movement_in_group(
            conn,
            group,
            date=when,
            description=description or "Split expense",
            kind="split",
            transfers=transfers,
            idempotency_key=idempotency_key,
            capture=capture,
        )
    return _finish_movement(conn, group, record)


def record_lending(
    conn: psycopg.Connection,
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
    resolve_account(conn, account_id)
    with write_group(conn, actor=actor, message_id=message_id) as group:
        person_account = ensure_person_account(conn, payee_id, group)
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
        record = create_movement_in_group(
            conn,
            group,
            date=when,
            description=text,
            kind="transfer",
            transfers=[transfer],
            idempotency_key=idempotency_key,
        )
    return _finish_movement(conn, group, record)


def record_settlement(
    conn: psycopg.Connection,
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
    resolve_account(conn, account_id)
    with write_group(conn, actor=actor, message_id=message_id) as group:
        person_account = ensure_person_account(conn, payee_id, group)
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
        record = create_movement_in_group(
            conn,
            group,
            date=when,
            description=text,
            kind="settlement",
            transfers=[transfer],
            idempotency_key=idempotency_key,
        )
    return _finish_movement(conn, group, record)


def record_refund(
    conn: psycopg.Connection,
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
    resolve_account(conn, account_id)
    category_id, payee_id = _validated_label(conn, category_id, payee_id)
    spending = system_account(conn, "spending")
    with write_group(conn, actor=actor, message_id=message_id) as group:
        transfer = TransferSpec(
            from_account_id=spending["id"],
            to_account_id=account_id,
            amount_paise=amount_paise,
            category_id=category_id,
            payee_id=payee_id,
            note=note,
        )
        record = create_movement_in_group(
            conn,
            group,
            date=when,
            description=description or "Refund",
            kind="refund",
            transfers=[transfer],
            idempotency_key=idempotency_key,
        )
    return _finish_movement(conn, group, record)


def record_loan_payment(
    conn: psycopg.Connection,
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
    resolve_account(conn, account_id)
    loan = resolve_account(conn, loan_account_id)
    if loan["kind"] != "loan":
        raise InvalidLedgerError("The destination account is not a loan")
    spending = system_account(conn, "spending")
    if interest_paise and interest_category_id is None:
        row = conn.execute(
            "SELECT id FROM categories WHERE name = 'Loan Interest' AND active"
        ).fetchone()
        interest_category_id = row["id"] if row else None
    interest_category_id, _ = _validated_label(conn, interest_category_id, None)

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
    with write_group(conn, actor=actor, message_id=message_id) as group:
        record = create_movement_in_group(
            conn,
            group,
            date=when,
            description=description or "Loan EMI",
            kind="loan",
            transfers=transfers,
            idempotency_key=idempotency_key,
        )
    return _finish_movement(conn, group, record)


def record_goal_contribution(
    conn: psycopg.Connection,
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
    goal = get_account_row(conn, goal_account_id)
    if goal["kind"] != "goal":
        raise InvalidLedgerError("A goal contribution goes into a goal account")
    row = conn.execute(
        "SELECT id FROM categories WHERE name = 'Savings & Investments' AND active"
    ).fetchone()
    return record_transfer(
        conn,
        from_account_id=account_id,
        to_account_id=goal_account_id,
        amount_paise=amount_paise,
        description=description or f"Contribution to {goal['name']}",
        category_id=row["id"] if row else None,
        date=date,
        note=note,
        idempotency_key=idempotency_key,
        actor=actor,
        message_id=message_id,
    )


def reverse_movement(
    group: WriteGroup,
    movement_id: str,
    *,
    note: str | None = None,
    date: date | None = None,
) -> MovementRecord:
    """Append the opposite transfer batch in one movement."""
    conn = group.conn
    movement = _movement_row(conn, movement_id)
    if movement["kind"] == "reversal":
        # Reversing a reversal is how undo-of-undo works; it is allowed and nets back.
        pass
    transfers = _transfer_rows(conn, movement_id)
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
    return create_movement_in_group(
        conn,
        group,
        date=date or movement["date"],
        description=f"Reversal of {movement['description']}",
        kind="reversal",
        transfers=opposite,
        reverses_id=movement_id,
    )


def _update_labels(
    conn: psycopg.Connection,
    group: WriteGroup,
    movement_id: str,
    values: dict[str, Any],
) -> int:
    updates = {key: value for key, value in values.items() if value is not None}
    if not updates:
        return 0
    labels = list(
        conn.execute(
            "SELECT * FROM transfer_labels WHERE movement_id = %s ORDER BY transfer_id",
            (movement_id,),
        ).fetchall()
    )
    if not labels:
        transfer = conn.execute(
            """
            SELECT id FROM pgledger_transfers
            WHERE metadata ->> 'movement_id' = %s ORDER BY id LIMIT 1
            """,
            (movement_id,),
        ).fetchone()
        if transfer is None:
            raise InvalidLedgerError("That movement has no transfer to label")
        row = conn.execute(
            """
            INSERT INTO transfer_labels
                (transfer_id, movement_id, category_id, payee_id, expected_date, note)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING *
            """,
            (
                transfer["id"],
                movement_id,
                updates.get("category_id"),
                updates.get("payee_id"),
                updates.get("expected_date"),
                updates.get("note"),
            ),
        ).fetchone()
        group.record_created("transfer_labels", row)
        return 1

    changed = 0
    for label in labels:
        columns = list(updates)
        after = conn.execute(
            f"""
            UPDATE transfer_labels
            SET {", ".join(f"{column} = %s" for column in columns)}, updated_at = now()
            WHERE transfer_id = %s RETURNING *
            """,
            (*[updates[column] for column in columns], label["transfer_id"]),
        ).fetchone()
        group.record_updated("transfer_labels", label["transfer_id"], label, after)
        changed += 1
    return changed


def _corrected_transfer_specs(
    conn: psycopg.Connection,
    movement: dict[str, Any],
    transfers: list[dict[str, Any]],
    *,
    amount_paise: int | None,
    account_id: str | None,
    labels: dict[str, Any],
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
    raise InvalidLedgerError(f"Amount and account corrections are not supported for {kind}")


def correct_movement(
    conn: psycopg.Connection,
    movement_id: str,
    *,
    changes: dict[str, Any],
    scope: str = "this",
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    if scope not in CORRECTABLE_SCOPES:
        raise InvalidLedgerError("scope is this, future, or all")
    movement = _movement_row(conn, movement_id)
    if movement["kind"] == "reversal":
        raise InvalidLedgerError("A reversal is not edited")
    state = _reversal_state(conn, [movement_id])[movement_id]
    if state["reversed"]:
        raise ConflictLedgerError("That movement has already been reversed")

    transfers = _transfer_rows(conn, movement_id)
    existing_labels = next(
        (
            {
                "category_id": row["category_id"],
                "payee_id": row["payee_id"],
                "expected_date": row["expected_date"],
                "note": row["note"],
            }
            for row in transfers
            if row["label_id"] is not None
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
        category_id = resolve_category(conn, category_id=category_id)["category_id"]
    if payee_id:
        get_payee(conn, payee_id)
    if scope != "this" and (category_id is None or payee_id is None):
        raise InvalidLedgerError("A future or all correction needs both a payee and a category")

    label_only = not any(
        changes.get(key) is not None for key in ("amount_paise", "account_id")
    ) and not changes.get("delete")
    rows_changed = 0
    label_changes = {
        key: merged_labels[key]
        for key in ("category_id", "payee_id", "expected_date", "note")
        if changes.get(key) is not None
    }

    with write_group(conn, actor=actor, message_id=message_id) as group:
        if label_only:
            if changes.get("date") is not None:
                before = movement
                after = conn.execute(
                    "UPDATE movements SET date = %s WHERE id = %s RETURNING *",
                    (changes["date"], movement_id),
                ).fetchone()
                group.record_updated("movements", movement_id, before, after)
            rows_changed = (
                _update_labels(conn, group, movement_id, label_changes) if label_changes else 0
            )
            result_movement_id = movement_id
            reversal_id = None
        else:
            reversal = reverse_movement(group, movement_id)
            reversal_id = reversal.id
            if changes.get("delete"):
                result_movement_id = reversal.id
            else:
                corrected = _corrected_transfer_specs(
                    conn,
                    movement,
                    transfers,
                    amount_paise=changes.get("amount_paise"),
                    account_id=changes.get("account_id"),
                    labels=merged_labels,
                )
                record = create_movement_in_group(
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
            correction = _apply_payee_correction_in_group(
                conn,
                group,
                payee_id=payee_id,
                category_id=category_id,
                scope=scope,
            )
            rows_changed += int(correction["rows_changed"])
        else:
            correction = {"rule": None, "rows_changed": 0}

    payload = movement_payload(conn, _movement_row(conn, result_movement_id))
    return {
        "status": "corrected" if not changes.get("delete") else "deleted",
        "movement_id": result_movement_id,
        "reversal_id": reversal_id,
        "rows_changed": rows_changed,
        "rule": correction["rule"],
        "movement": payload,
        "undo_id": group.group_id,
    }


def list_movements(
    conn: psycopg.Connection,
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
        clauses.append("m.date >= %s")
        params.append(start)
    if end is not None:
        clauses.append("m.date <= %s")
        params.append(end)
    if kind is not None:
        clauses.append("m.kind = %s")
        params.append(kind)
    if account_id is not None:
        clauses.append(
            """
            EXISTS (
                SELECT 1 FROM pgledger_transfers tx
                JOIN accounts fa ON fa.ledger_account_id = tx.from_account_id
                JOIN accounts ta ON ta.ledger_account_id = tx.to_account_id
                WHERE tx.metadata ->> 'movement_id' = m.id
                  AND (fa.id = %s OR ta.id = %s)
            )
            """
        )
        params.extend([account_id, account_id])
    if category_id is not None or payee_id is not None:
        label_clauses = ["tl.movement_id = m.id"]
        if category_id is not None:
            label_clauses.append("tl.category_id = %s")
            params.append(category_id)
        if payee_id is not None:
            label_clauses.append("tl.payee_id = %s")
            params.append(payee_id)
        clauses.append(
            f"EXISTS (SELECT 1 FROM transfer_labels tl WHERE {' AND '.join(label_clauses)})"
        )
    if cursor:
        try:
            cursor_date, cursor_id = cursor.split("|", 1)
            clauses.append("(m.date, m.id) < (%s, %s)")
            params.extend([date.fromisoformat(cursor_date), cursor_id])
        except (ValueError, TypeError) as exc:
            raise InvalidLedgerError("Invalid cursor") from exc

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = int(
        conn.execute(
            f"SELECT count(*) AS n FROM movements m {where}",  # noqa: S608
            params,
        ).fetchone()["n"]
    )

    rows = list(
        conn.execute(
            f"""
            SELECT m.* FROM movements m {where}
            ORDER BY m.date DESC, m.id DESC
            LIMIT %s
            """,  # noqa: S608
            (*params, limit * 3 + 1),
        ).fetchall()
    )
    if not include_reversed:
        rows = [row for row in rows if row["kind"] != "reversal"]
    states = _reversal_state(conn, [str(row["id"]) for row in rows])
    if not include_reversed:
        rows = [row for row in rows if not states[str(row["id"])]["reversed"]]
    rows = rows[:limit]
    next_cursor = None
    if rows:
        last = rows[-1]
        next_cursor = f"{last['date'].isoformat()}|{last['id']}"
    return {
        "items": [movement_payload(conn, row, state=states[str(row["id"])]) for row in rows],
        "total_count": total,
        "next_cursor": next_cursor,
        "as_of": today().isoformat(),
    }
