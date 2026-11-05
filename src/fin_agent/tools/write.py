"""The write tools, as plain Python over the ledger.

Each returns a short model payload plus a card's worth of detail for the UI. Approval is a
PydanticAI concern and lives in the tool wrappers; the functions here only write, through the
same audited `write_group` the REST API uses.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from fin_agent import scheduled_questions as questions_mod
from fin_agent.engine.money import money
from fin_agent.india.formatting import format_paise
from fin_agent.ledger import accounts as accounts_mod
from fin_agent.ledger import categories as categories_mod
from fin_agent.ledger import goals as goals_mod
from fin_agent.ledger import movements as movements_mod
from fin_agent.ledger import payees as payees_mod
from fin_agent.ledger import schedules as schedules_mod
from fin_agent.ledger import settings as settings_mod
from fin_agent.ledger.audit import undo_group, undo_latest, write_group
from fin_agent.ledger.common import ledger_balance
from fin_agent.ledger.common import today as ledger_today
from fin_agent.ledger.errors import InvalidLedgerError


class ToolInputError(ValueError):
    """A message the model can act on, delivered as ModelRetry by the wrapper."""


def _rupees(value: Any, field: str = "amount") -> int:
    if value is None:
        raise ToolInputError(f"{field} is required")
    paise = int(Decimal(str(value)) * 100)
    if paise <= 0:
        raise ToolInputError(f"{field} must be positive")
    return paise


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ToolInputError(f"{value!r} is not an ISO date") from exc


def resolve_account_id(conn: psycopg.Connection, name: str | None) -> str:
    if not name:
        raise ToolInputError("which account?")
    row = conn.execute(
        "SELECT id, name FROM accounts WHERE active AND lower(name) = lower(%s) LIMIT 1", (name,)
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT id, name FROM accounts WHERE id = %s AND active", (name,)
        ).fetchone()
    if row is None:
        rows = conn.execute(
            "SELECT name FROM accounts WHERE active AND kind NOT IN "
            "('spending','income','equity','person','goal') ORDER BY name"
        ).fetchall()
        names = ", ".join(row["name"] for row in rows)
        raise ToolInputError(f"No account named {name!r}. Accounts: {names}")
    return str(row["id"])


def _resolve_category_id(conn: psycopg.Connection, name: str | None) -> str | None:
    if not name:
        return None
    row = conn.execute(
        "SELECT id FROM categories WHERE active AND lower(name) = lower(%s) LIMIT 1", (name,)
    ).fetchone()
    if row is None:
        rows = conn.execute("SELECT name FROM categories WHERE active ORDER BY name").fetchall()
        names = ", ".join(row["name"] for row in rows)
        raise ToolInputError(f"No category named {name!r}. Categories: {names}")
    return str(row["id"])


def resolve_payee(
    conn: psycopg.Connection,
    name: str,
    *,
    kind: str = "merchant",
    upi_id: str | None = None,
    match_hash: str | None = None,
    outside_text: bool = False,
) -> dict[str, Any]:
    if not name or not name.strip():
        raise ToolInputError("a payee is required")
    name = name.strip()
    if upi_id:
        row = conn.execute(
            "SELECT * FROM payees WHERE active AND upi_id = %s ORDER BY display_name LIMIT 1",
            (upi_id,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT * FROM payees
            WHERE active AND lower(display_name) = lower(%s)
            ORDER BY display_name LIMIT 1
            """,
            (name,),
        ).fetchone()
    if row is not None:
        return row
    return payees_mod.create_payee(
        conn,
        display_name=name,
        kind=kind,
        upi_id=upi_id,
        match_hash=match_hash,
        outside_text=outside_text,
        actor="agent",
    )


def _usual_account(conn: psycopg.Connection, payee_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT fa.id, count(*) AS n
        FROM pgledger_transfers t
        JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
        JOIN transfer_labels tl ON tl.transfer_id = t.id
        JOIN movements m ON m.id = t.metadata ->> 'movement_id'
        WHERE tl.payee_id = %s AND fa.kind IN ('bank', 'cash', 'wallet', 'card')
        GROUP BY fa.id
        ORDER BY n DESC, fa.name
        LIMIT 1
        """,
        (payee_id,),
    ).fetchone()
    return str(row["id"]) if row else None


def _default_account(conn: psycopg.Connection, payee_id: str | None) -> str:
    if payee_id:
        usual = _usual_account(conn, payee_id)
        if usual:
            return usual
    rows = conn.execute(
        "SELECT id, name FROM accounts WHERE active AND liquid ORDER BY name"
    ).fetchall()
    if len(rows) == 1:
        return str(rows[0]["id"])
    if not rows:
        raise ToolInputError("which account? No liquid account exists yet.")
    everyday = _everyday_account(conn)
    if everyday:
        return everyday
    raise ToolInputError(
        "which account? Liquid accounts: " + ", ".join(row["name"] for row in rows)
    )


def _everyday_account(conn: psycopg.Connection) -> str | None:
    """The liquid account the user moves money from most, for a payee with no history."""
    row = conn.execute(
        """
        SELECT fa.id, count(*) AS n
        FROM pgledger_transfers t
        JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
        JOIN movements m ON m.id = t.metadata ->> 'movement_id'
        WHERE fa.active AND fa.liquid AND m.kind IN ('expense', 'refund')
        GROUP BY fa.id
        ORDER BY n DESC, fa.name
        LIMIT 1
        """
    ).fetchone()
    return str(row["id"]) if row else None


def default_account_for_payee(conn: psycopg.Connection, payee_id: str | None) -> str:
    """The account a capture should use: usual for this payee, else the everyday liquid one."""
    return _default_account(conn, payee_id)


def _possible_duplicate(
    conn: psycopg.Connection,
    *,
    account_id: str,
    amount_paise: int,
    payee_id: str | None,
    exclude_key: str | None,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT m.id, m.date, m.description
        FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
        JOIN transfer_labels tl ON tl.transfer_id = t.id
        JOIN captures c ON c.movement_id = m.id AND c.kind = 'chat'
        WHERE m.kind = 'expense'
          AND (m.idempotency_key IS NULL OR m.idempotency_key <> %s)
          AND fa.id = %s
          AND t.amount = %s
          AND tl.payee_id IS NOT DISTINCT FROM %s
          AND m.created_at > now() - interval '10 minutes'
        ORDER BY m.created_at DESC LIMIT 1
        """,
        (exclude_key, account_id, amount_paise, payee_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "status": "possible_duplicate",
        "existing": {
            "movement_id": row["id"],
            "date": row["date"].isoformat(),
            "summary": row["description"],
        },
    }


def _card(kind: str, **values: Any) -> dict[str, Any]:
    return {"card": kind, **values}


def _audited_undo_id(conn: psycopg.Connection, undo_id: str | None) -> str | None:
    """Only an audit group with a real change gets an undo button."""
    if not undo_id:
        return None
    row = conn.execute("SELECT 1 FROM audit_log WHERE group_id = %s LIMIT 1", (undo_id,)).fetchone()
    return undo_id if row else None


def _movement_summary(result: dict[str, Any], amount_paise: int) -> str:
    transfers = result.get("transfers") or []
    if not transfers:
        return str(result.get("description") or "Recorded")
    first = transfers[0]
    text = money(amount_paise)["text"] if money(amount_paise) else ""
    if result.get("kind") == "expense":
        who = first.get("payee_name") or first.get("to_name")
        line = f"{text} to {who} from {first.get('from_name')}"
        if first.get("category_name"):
            line += f", {first['category_name']}"
        return line
    if result.get("kind") == "income":
        who = first.get("payee_name") or first.get("from_name")
        return f"{text} into {first.get('to_name')} from {who}"
    return str(result.get("description") or "Recorded")


def record_movement(
    conn: psycopg.Connection,
    movement: dict[str, Any],
    *,
    message_id: str | None,
    conversation_id: str | None,
    flagged: bool = False,
    user_texts: tuple[str, ...] = (),
    not_duplicate: bool = False,
    capture_kind: str = "chat",
    capture_ref: str | None = None,
) -> dict[str, Any]:
    kind = str(movement.get("kind") or "expense")
    when = _date(movement.get("date"))
    note = movement.get("note")
    description = movement.get("description")
    payee_name = movement.get("payee") or movement.get("person")
    idempotency_key = f"{message_id}:{movement.get('tool_call_id')}" if message_id else None
    evidence = (
        {"kind": capture_kind, "ref": capture_ref or message_id}
        if (capture_ref or message_id)
        else None
    )

    def named_outside(name: str | None) -> bool:
        if not name:
            return False
        return flagged and not any(name.lower() in text.lower() for text in user_texts)

    payee = None
    if payee_name:
        payee = resolve_payee(
            conn,
            str(payee_name),
            kind="person" if kind in {"split", "settlement"} else "merchant",
            upi_id=movement.get("upi_id"),
            outside_text=named_outside(str(payee_name)),
        )
    payee_id = payee["id"] if payee else None

    if kind == "expense":
        amount = _rupees(movement.get("amount_rupees"))
        account_id = (
            resolve_account_id(conn, movement.get("account"))
            if movement.get("account")
            else _default_account(conn, payee_id)
        )
        if not not_duplicate:
            duplicate = _possible_duplicate(
                conn,
                account_id=account_id,
                amount_paise=amount,
                payee_id=payee_id,
                exclude_key=idempotency_key,
            )
            if duplicate:
                return duplicate
        result = movements_mod.record_expense(
            conn,
            account_id=account_id,
            amount_paise=amount,
            description=description,
            payee_id=payee_id,
            category_id=_resolve_category_id(conn, movement.get("category")),
            category_guess_id=_resolve_category_id(conn, movement.get("category_guess")),
            date=when,
            note=note,
            idempotency_key=idempotency_key,
            capture=evidence,
            actor="agent",
            message_id=message_id,
        )
        return {
            "status": "recorded",
            "movement_id": result["id"],
            "undo_id": result["undo_id"],
            "date": result["date"],
            "summary": _movement_summary(result, amount),
            "transfers": result["transfers"],
            "account_source": "stated" if movement.get("account") else "usual",
            "category_source": result.get("category_source", "none"),
            "card": _card(
                "movement",
                title="Logged",
                summary=_movement_summary(result, amount),
                amount=money(amount),
                undo_id=result["undo_id"],
                movement=result,
            ),
        }

    if kind == "income":
        amount = _rupees(movement.get("amount_rupees"))
        account_id = (
            resolve_account_id(conn, movement.get("account"))
            if movement.get("account")
            else _default_account(conn, payee_id)
        )
        result = movements_mod.record_income(
            conn,
            account_id=account_id,
            amount_paise=amount,
            description=description,
            payee_id=payee_id,
            date=when,
            note=note,
            idempotency_key=idempotency_key,
            capture=evidence,
            actor="agent",
            message_id=message_id,
        )
        return {
            "status": "recorded",
            "movement_id": result["id"],
            "undo_id": result["undo_id"],
            "date": result["date"],
            "summary": result["description"],
            "transfers": result["transfers"],
            "card": _card(
                "movement",
                title="Logged income",
                summary=result["description"],
                amount=money(amount),
                undo_id=result["undo_id"],
            ),
        }

    if kind == "transfer":
        amount = _rupees(movement.get("amount_rupees"))
        result = movements_mod.record_transfer(
            conn,
            from_account_id=resolve_account_id(conn, movement.get("from_account")),
            to_account_id=resolve_account_id(conn, movement.get("to_account")),
            amount_paise=amount,
            description=description,
            date=when,
            note=note,
            idempotency_key=idempotency_key,
            actor="agent",
            message_id=message_id,
        )
        return {
            "status": "recorded",
            "movement_id": result["id"],
            "undo_id": result["undo_id"],
            "date": result["date"],
            "summary": result["description"],
            "transfers": result["transfers"],
            "card": _card(
                "movement",
                title="Transferred",
                summary=result["description"],
                amount=money(amount),
                undo_id=result["undo_id"],
            ),
        }

    if kind == "split":
        total = _rupees(movement.get("total_rupees"), "total_rupees")
        my_share = _rupees(movement.get("my_share_rupees"), "my_share_rupees")
        people = []
        for person in movement.get("people") or []:
            if isinstance(person, dict):
                name = person.get("name")
                amount = _rupees(person.get("amount_rupees"), "a person's share")
                expected = _date(person.get("expected_date"))
            else:
                name, amount, expected = str(person), 0, None
            if not name or not amount:
                raise ToolInputError("a split names each person and their share")
            other = resolve_payee(
                conn,
                str(name),
                kind="person",
                outside_text=named_outside(str(name)),
            )
            people.append(
                {"payee_id": other["id"], "amount_paise": amount, "expected_date": expected}
            )
        if not people:
            raise ToolInputError("a split needs at least one person")
        account_id = (
            resolve_account_id(conn, movement.get("account"))
            if movement.get("account")
            else _default_account(conn, payee_id)
        )
        result = movements_mod.record_split(
            conn,
            account_id=account_id,
            my_share_paise=my_share,
            people=people,
            total_paise=total,
            description=description,
            payee_id=payee_id,
            category_id=_resolve_category_id(conn, movement.get("category")),
            category_guess_id=_resolve_category_id(conn, movement.get("category_guess")),
            date=when,
            note=note,
            idempotency_key=idempotency_key,
            capture=evidence,
            actor="agent",
            message_id=message_id,
        )
        return {
            "status": "recorded",
            "movement_id": result["id"],
            "undo_id": result["undo_id"],
            "date": result["date"],
            "summary": result["description"],
            "transfers": result["transfers"],
            "card": _card(
                "movement",
                title="Split logged",
                summary=result["description"],
                amount=money(total),
                undo_id=result["undo_id"],
            ),
        }

    if kind == "loan":
        amount = _rupees(movement.get("amount_rupees"))
        loan_id = resolve_account_id(conn, movement.get("loan"))
        loan = conn.execute("SELECT * FROM accounts WHERE id = %s", (loan_id,)).fetchone()
        if loan is None or loan["kind"] != "loan":
            raise ToolInputError("a loan payment names a loan account")
        outstanding = max(ledger_balance(conn, loan["ledger_account_id"]), 0)
        principal = amount
        interest = 0
        if loan["rate_bps"]:
            monthly = Decimal(loan["rate_bps"]) / Decimal(10000) / Decimal(12)
            interest = int((Decimal(outstanding) * monthly).quantize(Decimal("1")))
            interest = min(interest, amount - 1) if amount > 1 else 0
            principal = amount - interest
        account_id = (
            resolve_account_id(conn, movement.get("account"))
            if movement.get("account")
            else _default_account(conn, payee_id)
        )
        result = movements_mod.record_loan_payment(
            conn,
            account_id=account_id,
            loan_account_id=loan_id,
            amount_paise=amount,
            principal_paise=principal,
            interest_paise=interest,
            description=description,
            date=when,
            note=note,
            idempotency_key=idempotency_key,
            actor="agent",
            message_id=message_id,
        )
        return {
            "status": "recorded",
            "movement_id": result["id"],
            "undo_id": result["undo_id"],
            "date": result["date"],
            "summary": result["description"],
            "transfers": result["transfers"],
            "card": _card(
                "movement",
                title="Loan payment logged",
                summary=result["description"],
                amount=money(amount),
                undo_id=result["undo_id"],
            ),
        }

    if kind == "refund":
        amount = _rupees(movement.get("amount_rupees"))
        account_id = (
            resolve_account_id(conn, movement.get("account"))
            if movement.get("account")
            else _default_account(conn, payee_id)
        )
        result = movements_mod.record_refund(
            conn,
            account_id=account_id,
            amount_paise=amount,
            description=description,
            payee_id=payee_id,
            category_id=_resolve_category_id(conn, movement.get("category")),
            date=when,
            note=note,
            idempotency_key=idempotency_key,
            actor="agent",
            message_id=message_id,
        )
        return {
            "status": "recorded",
            "movement_id": result["id"],
            "undo_id": result["undo_id"],
            "date": result["date"],
            "summary": result["description"],
            "transfers": result["transfers"],
            "card": _card(
                "movement",
                title="Refund logged",
                summary=result["description"],
                amount=money(amount),
                undo_id=result["undo_id"],
            ),
        }

    if kind == "settlement":
        amount = _rupees(movement.get("amount_rupees"))
        if payee is None:
            raise ToolInputError("a settlement names a person")
        account_id = (
            resolve_account_id(conn, movement.get("account"))
            if movement.get("account")
            else _default_account(conn, payee_id)
        )
        result = movements_mod.record_settlement(
            conn,
            account_id=account_id,
            payee_id=payee_id,
            amount_paise=amount,
            direction=movement.get("direction") or "they_paid",
            expected_date=_date(movement.get("expected_date")),
            description=description,
            date=when,
            note=note,
            idempotency_key=idempotency_key,
            actor="agent",
            message_id=message_id,
        )
        return {
            "status": "recorded",
            "movement_id": result["id"],
            "undo_id": result["undo_id"],
            "date": result["date"],
            "summary": result["description"],
            "transfers": result["transfers"],
            "card": _card(
                "movement",
                title="Settlement logged",
                summary=result["description"],
                amount=money(amount),
                undo_id=result["undo_id"],
            ),
        }

    raise ToolInputError(f"unknown movement kind {kind!r}")


def correct_movement(
    conn: psycopg.Connection,
    *,
    movement_id: str,
    changes: dict[str, Any],
    scope: str = "this",
    message_id: str | None = None,
) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for key, value in changes.items():
        if value is None:
            continue
        if key == "category":
            mapped["category_id"] = _resolve_category_id(conn, value)
        elif key == "account":
            mapped["account_id"] = resolve_account_id(conn, value)
        elif key == "amount_rupees":
            mapped["amount_paise"] = _rupees(value)
        elif key == "expected_date":
            mapped["expected_date"] = _date(value)
        elif key == "date":
            mapped["date"] = _date(value)
        elif key == "payee":
            mapped["payee_id"] = resolve_payee(conn, str(value))["id"]
        else:
            mapped[key] = value
    result = movements_mod.correct_movement(
        conn,
        movement_id,
        changes=mapped,
        scope=scope,
        actor="agent",
        message_id=message_id,
    )
    summary = result["movement"]["description"]
    rule_text = (
        str(result["movement"].get("payee_name"))
        + " → "
        + str(result["movement"].get("category_name"))
    )
    undo_id = _audited_undo_id(conn, result["undo_id"])
    return {
        "status": result["status"],
        "undo_id": result["undo_id"],
        "summary": summary,
        "rule": rule_text if result.get("rule") else None,
        "rows_changed": result["rows_changed"],
        "movement": result["movement"],
        "card": _card(
            "correction",
            title="Corrected" if result["status"] == "corrected" else "Deleted",
            summary=summary,
            rows_changed=result["rows_changed"],
            rule=result.get("rule"),
            undo_id=undo_id,
        ),
    }


def _find_one(
    conn: psycopg.Connection, table: str, name: str | None, key: str | None
) -> dict[str, Any]:
    if key:
        row = conn.execute(f"SELECT * FROM {table} WHERE id = %s", (key,)).fetchone()  # noqa: S608
        if row:
            return row
    if not name:
        raise ToolInputError(f"{table[:-1]} needs a name or id")
    row = conn.execute(
        f"SELECT * FROM {table} WHERE lower(name) = lower(%s) ORDER BY created_at LIMIT 1",  # noqa: S608
        (name,),
    ).fetchone()
    if row is None:
        raise ToolInputError(f"No {table[:-1]} named {name!r}")
    return row


def update_setup_item(
    conn: psycopg.Connection,
    *,
    entity: str,
    action: str,
    data: dict[str, Any],
    message_id: str | None,
) -> dict[str, Any]:
    """One discriminated setup write. The caller has already gated a lasting change."""
    if action not in {"add", "update", "archive"}:
        raise ToolInputError("action is add, update, or archive")

    if entity == "account":
        if action == "add":
            result = accounts_mod.create_account(
                conn,
                kind=data.get("kind") or "bank",
                name=data["name"],
                institution=data.get("institution"),
                last4=data.get("last4"),
                opening_balance_paise=_rupees(data["opening_balance_rupees"])
                if data.get("opening_balance_rupees") is not None
                else None,
                opening_balance_as_of=_date(data.get("as_of")),
                liquid=data.get("liquid"),
                credit_limit_paise=_rupees(data["credit_limit_rupees"])
                if data.get("credit_limit_rupees") is not None
                else None,
                statement_day=data.get("statement_day"),
                due_day=data.get("due_day"),
                rate_bps=int(Decimal(str(data["rate_percent"])) * 100)
                if data.get("rate_percent") is not None
                else None,
                tenure_months=data.get("tenure_months"),
                actor="agent",
                message_id=message_id,
            )
            summary = f"Added account {data['name']}"
        else:
            row = _find_one(conn, "accounts", data.get("name"), data.get("id"))
            if action == "archive":
                result = accounts_mod.archive_account(
                    conn, row["id"], actor="agent", message_id=message_id
                )
                summary = f"Archived account {row['name']}"
            else:
                result = accounts_mod.update_account(
                    conn,
                    row["id"],
                    name=data.get("new_name"),
                    institution=data.get("institution"),
                    last4=data.get("last4"),
                    liquid=data.get("liquid"),
                    credit_limit_paise=_rupees(data["credit_limit_rupees"])
                    if data.get("credit_limit_rupees") is not None
                    else None,
                    statement_day=data.get("statement_day"),
                    due_day=data.get("due_day"),
                    rate_bps=int(Decimal(str(data["rate_percent"])) * 100)
                    if data.get("rate_percent") is not None
                    else None,
                    tenure_months=data.get("tenure_months"),
                    active=data.get("active"),
                    actor="agent",
                    message_id=message_id,
                )
                summary = f"Updated account {row['name']}"
        return {
            "status": "saved",
            "undo_id": result["undo_id"],
            "summary": summary,
            "result": result,
            "card": _card(
                "setup", title="Setup saved", summary=summary, undo_id=result.get("undo_id")
            ),
        }

    if entity == "balance":
        account_id = resolve_account_id(conn, data.get("account"))
        result = accounts_mod.set_statement_balance(
            conn,
            account_id,
            balance_paise=_rupees(data.get("balance_rupees"), "balance_rupees"),
            as_of=_date(data.get("as_of")),
            actor="agent",
            message_id=message_id,
        )
        rupees = data["balance_rupees"]
        paise = rupees * 100 if isinstance(rupees, int) else Decimal(str(rupees)) * 100
        summary = f"Set {result['account']['name']} balance to {format_paise(paise)}"
        return {
            "status": "saved",
            "undo_id": result["undo_id"],
            "summary": summary,
            "result": result,
            "card": _card(
                "setup",
                title="Balance saved",
                summary=summary,
                undo_id=result.get("undo_id"),
            ),
        }

    if entity == "schedule":
        if action == "add":
            result = schedules_mod.create_schedule(
                conn,
                kind=data.get("kind") or "obligation",
                name=data["name"],
                rule=data["rule"],
                starts=_date(data.get("starts")) or ledger_today(),
                amount_paise=_rupees(data["amount_rupees"])
                if data.get("amount_rupees") is not None
                else None,
                subtype=data.get("subtype"),
                account_id=resolve_account_id(conn, data["account"])
                if data.get("account")
                else None,
                category_id=_resolve_category_id(conn, data.get("category")),
                confirmed=bool(data.get("confirmed", True)),
                ends=_date(data.get("ends")),
                holiday_adjust=data.get("holiday_adjust") or "none",
                actor="agent",
                message_id=message_id,
            )
            summary = f"Added schedule {data['name']}"
        else:
            row = _find_one(conn, "schedules", data.get("name"), data.get("id"))
            if action == "archive":
                result = schedules_mod.archive_schedule(
                    conn, row["id"], actor="agent", message_id=message_id
                )
                summary = f"Archived schedule {row['name']}"
            else:
                result = schedules_mod.update_schedule(
                    conn,
                    row["id"],
                    name=data.get("new_name"),
                    amount_paise=_rupees(data["amount_rupees"])
                    if data.get("amount_rupees") is not None
                    else None,
                    rule=data.get("rule"),
                    subtype=data.get("subtype"),
                    account_id=resolve_account_id(conn, data["account"])
                    if data.get("account")
                    else None,
                    category_id=_resolve_category_id(conn, data.get("category")),
                    confirmed=data.get("confirmed"),
                    starts=_date(data.get("starts")),
                    ends=_date(data.get("ends")),
                    holiday_adjust=data.get("holiday_adjust"),
                    active=data.get("active"),
                    actor="agent",
                    message_id=message_id,
                )
                summary = f"Updated schedule {row['name']}"
        return {
            "status": "saved",
            "undo_id": result["undo_id"],
            "summary": summary,
            "result": result,
            "card": _card(
                "setup", title="Setup saved", summary=summary, undo_id=result.get("undo_id")
            ),
        }

    if entity == "goal":
        if action == "add":
            result = goals_mod.create_goal(
                conn,
                name=data["name"],
                target_paise=_rupees(data.get("target_rupees"), "target_rupees"),
                deadline=_date(data.get("deadline")),
                priority=int(data.get("priority") or 3),
                actor="agent",
                message_id=message_id,
            )
            summary = f"Added goal {data['name']}"
        else:
            row = _find_one(conn, "goals", data.get("name"), data.get("id"))
            if action == "archive":
                result = goals_mod.archive_goal(
                    conn, row["id"], actor="agent", message_id=message_id
                )
                summary = f"Archived goal {row['name']}"
            else:
                result = goals_mod.update_goal(
                    conn,
                    row["id"],
                    name=data.get("new_name"),
                    target_paise=_rupees(data["target_rupees"])
                    if data.get("target_rupees") is not None
                    else None,
                    deadline=_date(data.get("deadline")),
                    priority=data.get("priority"),
                    status=data.get("status"),
                    actor="agent",
                    message_id=message_id,
                )
                summary = f"Updated goal {row['name']}"
        return {
            "status": "saved",
            "undo_id": result["undo_id"],
            "summary": summary,
            "result": result,
            "card": _card(
                "setup", title="Setup saved", summary=summary, undo_id=result.get("undo_id")
            ),
        }

    if entity == "category":
        if action == "add":
            result = categories_mod.create_category(
                conn,
                name=data["name"],
                reducible=bool(data.get("reducible", False)),
                actor="agent",
                message_id=message_id,
            )
            summary = f"Added category {data['name']}"
        else:
            row = _find_one(conn, "categories", data.get("name"), data.get("id"))
            result = (
                categories_mod.archive_category(
                    conn, row["id"], actor="agent", message_id=message_id
                )
                if action == "archive"
                else categories_mod.update_category(
                    conn,
                    row["id"],
                    name=data.get("new_name"),
                    reducible=data.get("reducible"),
                    actor="agent",
                    message_id=message_id,
                )
            )
            summary = f"{'Archived' if action == 'archive' else 'Updated'} category {row['name']}"
        return {
            "status": "saved",
            "undo_id": result["undo_id"],
            "summary": summary,
            "result": result,
            "card": _card(
                "setup", title="Setup saved", summary=summary, undo_id=result.get("undo_id")
            ),
        }

    if entity == "payee":
        if action == "add":
            result = payees_mod.create_payee(
                conn,
                display_name=data["name"],
                kind=data.get("kind") or "merchant",
                upi_id=data.get("upi_id"),
                default_category_id=_resolve_category_id(conn, data.get("default_category")),
                create_person_account=bool(data.get("create_person_account", False)),
                actor="agent",
                message_id=message_id,
            )
            summary = f"Added payee {data['name']}"
        else:
            row = _find_one(conn, "payees", data.get("name"), data.get("id"))
            result = (
                payees_mod.archive_payee(conn, row["id"], actor="agent", message_id=message_id)
                if action == "archive"
                else payees_mod.update_payee(
                    conn,
                    row["id"],
                    display_name=data.get("new_name"),
                    kind=data.get("kind"),
                    upi_id=data.get("upi_id"),
                    default_category_id=_resolve_category_id(conn, data.get("default_category")),
                    active=data.get("active"),
                    actor="agent",
                    message_id=message_id,
                )
            )
            summary = (
                f"{'Archived' if action == 'archive' else 'Updated'} payee {row['display_name']}"
            )
        return {
            "status": "saved",
            "undo_id": result["undo_id"],
            "summary": summary,
            "result": result,
            "card": _card(
                "setup", title="Setup saved", summary=summary, undo_id=result.get("undo_id")
            ),
        }

    if entity == "settings":
        floor = data.get("floor_rupees")
        result = settings_mod.update_settings(
            conn,
            floor_paise=_rupees(floor) if floor is not None else None,
            base_currency=data.get("base_currency"),
            time_zone=data.get("time_zone"),
            default_horizon_days=data.get("default_horizon_days"),
            notification_time=data.get("notification_time"),
            weekly_briefing_dow=data.get("weekly_briefing_dow"),
            reminder_lead_days=data.get("reminder_lead_days"),
            display_name=data.get("display_name"),
            actor="agent",
            message_id=message_id,
        )
        summary = "Updated settings"
        if floor is not None:
            summary = f"Set the floor to {format_paise(_rupees(floor))}"
        return {
            "status": "saved",
            "undo_id": result["undo_id"],
            "summary": summary,
            "result": result,
            "card": _card(
                "setup", title="Setup saved", summary=summary, undo_id=result.get("undo_id")
            ),
        }

    if entity == "scheduled_question":
        if action == "archive":
            row = conn.execute(
                "SELECT * FROM scheduled_questions "
                "WHERE id = %s OR lower(prompt) = lower(%s) LIMIT 1",
                (data.get("id"), data.get("prompt")),
            ).fetchone()
            if row is None:
                raise ToolInputError("No such scheduled question")
            result = questions_mod.archive_question(
                conn, row["id"], actor="agent", message_id=message_id
            )
            summary = "Archived the scheduled question"
        elif action == "update":
            row = conn.execute(
                "SELECT * FROM scheduled_questions "
                "WHERE id = %s OR lower(prompt) = lower(%s) LIMIT 1",
                (data.get("id"), data.get("prompt")),
            ).fetchone()
            if row is None:
                raise ToolInputError("No such scheduled question")
            result = questions_mod.update_question(
                conn,
                row["id"],
                prompt=data.get("prompt"),
                rule=data.get("rule"),
                spend_limit_usd=data.get("spend_limit_usd"),
                active=data.get("active"),
                actor="agent",
                message_id=message_id,
            )
            summary = "Updated the scheduled question"
        else:
            try:
                result = questions_mod.create_question(
                    conn,
                    prompt=data["prompt"],
                    rule=data["rule"],
                    spend_limit_usd=data.get("spend_limit_usd"),
                    actor="agent",
                    message_id=message_id,
                )
            except InvalidLedgerError as exc:
                raise ToolInputError(exc.message) from exc
            summary = "Added the scheduled question"
        return {
            "status": "saved",
            "undo_id": result["undo_id"],
            "summary": summary,
            "card": _card(
                "setup", title="Setup saved", summary=summary, undo_id=result.get("undo_id")
            ),
        }

    if entity == "baseline":
        category_id = _resolve_category_id(conn, data.get("category"))
        if category_id is None:
            raise ToolInputError("a baseline names a category")
        amount = _rupees(data.get("amount_per_day_rupees"), "amount_per_day_rupees")
        with write_group(conn, actor="agent", message_id=message_id) as group:
            before = conn.execute(
                "SELECT * FROM baselines WHERE category_id = %s", (category_id,)
            ).fetchone()
            after = conn.execute(
                """
                INSERT INTO baselines (category_id, amount_per_day_paise, source, window_days)
                VALUES (%s, %s, 'estimated', 30)
                ON CONFLICT (category_id) DO UPDATE
                    SET amount_per_day_paise = EXCLUDED.amount_per_day_paise,
                        source = 'estimated', updated_at = now()
                RETURNING *
                """,
                (category_id, amount),
            ).fetchone()
            if before is None:
                group.record_created("baselines", after)
            else:
                group.record_updated("baselines", after["id"], before, after)
        summary = f"Estimated {format_paise(amount)} a day for {data.get('category')}"
        return {
            "status": "saved",
            "undo_id": group.group_id,
            "summary": summary,
            "card": _card(
                "setup", title="Baseline estimated", summary=summary, undo_id=group.group_id
            ),
        }

    raise ToolInputError(f"unknown setup entity {entity!r}")


def preference(
    conn: psycopg.Connection,
    *,
    action: str,
    text: str | None,
    preference_id: str | None,
    message_id: str | None,
) -> dict[str, Any]:
    if action == "remember":
        if not text or not text.strip():
            raise ToolInputError("what should I remember?")
        text = text.strip()
        existing = conn.execute(
            """
            SELECT * FROM preferences
            WHERE active AND deleted_at IS NULL AND lower(body) = lower(%s)
            ORDER BY created_at LIMIT 1
            """,
            (text,),
        ).fetchone()
        if existing:
            return {
                "status": "remembered",
                "id": existing["id"],
                "undo_id": None,
                "text": existing["body"],
                "card": _card("preference", title="Remembered", text=existing["body"]),
            }
        with write_group(conn, actor="agent", message_id=message_id) as group:
            row = conn.execute(
                """
                INSERT INTO preferences (body, created_by, source_message_id)
                VALUES (%s, 'agent', %s) RETURNING *
                """,
                (text, message_id),
            ).fetchone()
            group.record_created("preferences", row)
        return {
            "status": "remembered",
            "id": row["id"],
            "undo_id": group.group_id,
            "text": row["body"],
            "card": _card(
                "preference",
                title="Remembered",
                text=row["body"],
                undo_id=group.group_id,
            ),
        }

    if action == "forget":
        row = None
        if preference_id:
            row = conn.execute(
                "SELECT * FROM preferences WHERE id = %s AND deleted_at IS NULL", (preference_id,)
            ).fetchone()
        if row is None and text:
            row = conn.execute(
                """
                SELECT * FROM preferences
                WHERE active AND deleted_at IS NULL AND lower(body) = lower(%s)
                ORDER BY created_at LIMIT 1
                """,
                (text.strip(),),
            ).fetchone()
        if row is None:
            active = conn.execute(
                "SELECT id, body FROM preferences WHERE active AND deleted_at IS NULL"
            ).fetchall()
            listed = "; ".join(f"{r['id']}: {r['body']}" for r in active) or "none"
            raise ToolInputError(f"No preference matched. Active preferences: {listed}")
        with write_group(conn, actor="agent", message_id=message_id) as group:
            after = conn.execute(
                "UPDATE preferences SET active = false, updated_at = now() "
                "WHERE id = %s RETURNING *",
                (row["id"],),
            ).fetchone()
            group.record_updated("preferences", row["id"], row, after)
        return {
            "status": "forgotten",
            "id": row["id"],
            "undo_id": group.group_id,
            "text": row["body"],
            "card": _card(
                "preference",
                title="Forgotten",
                text=row["body"],
                undo_id=group.group_id,
            ),
        }

    raise ToolInputError("action is remember or forget")


def import_document(
    conn: psycopg.Connection,
    *,
    attachment_id: str,
    message_id: str | None = None,
) -> dict[str, Any]:
    """Start an import in review state. The ledger is untouched and no statement text returns."""
    del message_id
    from fin_agent.importers.pipeline import create_import, import_payload

    try:
        row = create_import(conn, attachment_id=attachment_id)
    except ValueError as exc:
        raise ToolInputError(str(exc)) from exc
    payload = import_payload(conn, row["id"])
    return {
        "import_id": payload["import_id"],
        "status": payload["status"],
        "account": payload["account"]["name"] if payload["account"] else None,
        "period": payload["period"],
        "counts": payload["counts"],
        "check": payload["check"],
        "reason": payload["reason"],
        "card": _card(
            "import",
            title="Import started",
            import_id=payload["import_id"],
            filename=(payload["attachment"] or {}).get("filename"),
            status=payload["status"],
            reason=payload["reason"],
            review_url=f"#activity/imports/{payload['import_id']}",
        ),
    }


def undo(
    conn: psycopg.Connection,
    *,
    undo_id: str | None,
    conversation_id: str | None,
    message_id: str | None,
) -> dict[str, Any]:
    if undo_id:
        try:
            result = undo_group(conn, undo_id, actor="agent", message_id=message_id)
        except Exception as exc:  # noqa: BLE001 - wrapper turns this into ModelRetry
            raise ToolInputError(str(exc)) from exc
    else:
        row = None
        if conversation_id:
            row = conn.execute(
                """
                SELECT a.group_id
                FROM audit_log a
                JOIN messages m ON m.id = a.message_id
                WHERE m.conversation_id = %s AND a.undone_at IS NULL
                GROUP BY a.group_id
                ORDER BY max(a.created_at) DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        if row is None:
            result = undo_latest(conn, actor="agent", message_id=message_id)
        else:
            result = undo_group(conn, str(row["group_id"]), actor="agent", message_id=message_id)
    return {
        **result,
        "card": _card("undo", title="Undone", summary=result.get("summary")),
    }


def delete_preference_for_good(conn: psycopg.Connection, preference_id: str) -> dict[str, Any]:
    """Settings-only delete: blank the body and every audit copy, then delete the row."""
    row = conn.execute("SELECT * FROM preferences WHERE id = %s", (preference_id,)).fetchone()
    if row is None:
        raise ToolInputError("No such preference")
    with conn.transaction():
        conn.execute(
            """
            UPDATE audit_log
            SET before = CASE WHEN before ? 'body'
                              THEN jsonb_set(before, '{body}', '\"\"') ELSE before END,
                after = CASE WHEN after ? 'body'
                             THEN jsonb_set(after, '{body}', '\"\"') ELSE after END
            WHERE table_name = 'preferences' AND row_id = %s
            """,
            (preference_id,),
        )
        conn.execute("DELETE FROM preferences WHERE id = %s", (preference_id,))
        conn.execute(
            """
            INSERT INTO audit_log (actor, group_id, table_name, row_id, before, after)
            VALUES ('user', pgledger_generate_id('und'), 'preferences', %s, %s, NULL)
            """,
            (preference_id, Jsonb({"id": preference_id, "body": ""})),
        )
    conn.commit()
    return {"status": "deleted", "id": preference_id}


__all__ = [
    "ToolInputError",
    "correct_movement",
    "default_account_for_payee",
    "delete_preference_for_good",
    "preference",
    "record_movement",
    "resolve_account_id",
    "resolve_payee",
    "undo",
    "update_setup_item",
]
