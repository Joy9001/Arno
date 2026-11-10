"""The write tools, as plain Python over the ledger.

Each returns a short model payload plus a card's worth of detail for the UI. Approval is a
PydanticAI concern and lives in the tool wrappers; the functions here only write, through the
same audited `write_group` the REST API uses.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from arno import scheduled_questions as questions_mod
from arno.db.conn import Connection, quote_ident
from arno.engine.money import money
from arno.engine.projection import CardAccount, card_due_date, statement_close
from arno.india.formatting import format_paise
from arno.ledger import accounts as accounts_mod
from arno.ledger import categories as categories_mod
from arno.ledger import goals as goals_mod
from arno.ledger import movements as movements_mod
from arno.ledger import payees as payees_mod
from arno.ledger import schedule_payments
from arno.ledger import schedules as schedules_mod
from arno.ledger import settings as settings_mod
from arno.ledger.audit import undo_group, undo_latest, write_group
from arno.ledger.common import today as ledger_today
from arno.ledger.errors import InvalidLedgerError
from arno.tools.mark import mark


class ToolInputError(ValueError):
    """A message the model can act on, delivered as ModelRetry by the wrapper."""


# `amount_paise` columns are BIGINT. Reject earlier, with words the model can retry on.
MAX_PAISE = 9_223_372_036_854_775_807


def _decimal_paise(value: Any, field: str) -> int:
    """Rupees to whole paise, exact: sub-paise input rounds half up, like engine/money.py."""
    if value is None:
        raise ToolInputError(f"{field} is required")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ToolInputError(f"{field} must be a number of rupees") from exc
    if not amount.is_finite():
        raise ToolInputError(f"{field} must be a finite number of rupees")
    paise = int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if abs(paise) > MAX_PAISE:
        raise ToolInputError(f"{field} is too large")
    return paise


def _rupees(value: Any, field: str = "amount") -> int:
    """A positive rupee amount as paise."""
    paise = _decimal_paise(value, field)
    if paise <= 0:
        raise ToolInputError(f"{field} must be positive")
    return paise


def _balance_rupees(value: Any, field: str = "balance_rupees") -> int:
    """A signed rupee balance as paise: an overdrawn bank account is negative."""
    return _decimal_paise(value, field)


def _stated_balance_paise(value: Any, kind: str, field: str = "balance_rupees") -> int:
    """A stated balance as the ledger stores it.

    A card's or loan's ledger balance is a liability, so the number the user says is what they owe
    and the ledger holds it negative. A negative number from the user therefore means the account
    is in credit (an overpayment), never "negative debt". A loan is never in credit, so that sign
    is refused rather than silently reversed.
    """
    paise = _balance_rupees(value, field)
    if kind not in {"card", "loan"}:
        return paise
    if paise < 0 and kind == "loan":
        raise ToolInputError(
            "A loan's balance is what you still owe, so it cannot be a credit. State the amount "
            "owed as a positive number, or 0 if it is paid off."
        )
    return -paise


def _percent_to_bps(value: Any) -> int:
    """An annual percent as integer basis points, rounded half up like every other amount."""
    return int((Decimal(str(value)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ToolInputError(f"{value!r} is not an ISO date") from exc


async def resolve_account_id(conn: Connection, name: str | None) -> str:
    if not name:
        raise ToolInputError("which account?")
    rows = await conn.fetch(
        "SELECT id, name FROM accounts WHERE active AND lower(name) = lower($1) "
        "ORDER BY name LIMIT 2",
        name,
    )
    row = rows[0] if rows else None
    if len(rows) > 1:
        raise ToolInputError(f"{name!r} names more than one account; pass the account's ID instead")
    if row is None:
        row = await conn.fetchrow("SELECT id, name FROM accounts WHERE id = $1 AND active", name)
    if row is None:
        archived = await conn.fetchrow(
            "SELECT name FROM accounts WHERE lower(name) = lower($1) OR id = $2 LIMIT 1",
            name,
            name,
        )
        if archived is not None:
            raise ToolInputError(
                f"{archived['name']} is archived — reactivate it or pick another account. "
                f"Spendable accounts: {await _spendable_names(conn)}"
            )
        raise ToolInputError(f"No account named {name!r}. Accounts: {await _spendable_names(conn)}")
    return str(row["id"])


async def _spendable_names(conn: Connection) -> str:
    """The active accounts money can actually move through, for a refusal to name."""
    rows = await conn.fetch(
        "SELECT name FROM accounts WHERE active AND kind IN ('bank','cash','wallet','card') "
        "ORDER BY name"
    )
    return ", ".join(str(row["name"]) for row in rows)


async def _resolve_category_id(conn: Connection, name: str | None) -> str | None:
    if not name:
        return None
    row = await conn.fetchrow(
        "SELECT id FROM categories WHERE active AND lower(name) = lower($1) LIMIT 1", name
    )
    if row is None:
        rows = await conn.fetch("SELECT name FROM categories WHERE active ORDER BY name")
        names = ", ".join(row["name"] for row in rows)
        raise ToolInputError(f"No category named {name!r}. Categories: {names}")
    return str(row["id"])


async def resolve_payee(
    conn: Connection,
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
        row = await conn.fetchrow(
            "SELECT * FROM payees WHERE active AND lower(upi_id) = lower($1) "
            "ORDER BY display_name LIMIT 1",
            upi_id,
        )
    else:
        row = await conn.fetchrow(
            """
            SELECT * FROM payees
            WHERE active AND lower(display_name) = lower($1)
            ORDER BY display_name LIMIT 1
            """,
            name,
        )
    if row is not None:
        return row
    return await payees_mod.create_payee(
        conn,
        display_name=name,
        kind=kind,
        upi_id=upi_id,
        match_hash=match_hash,
        outside_text=outside_text,
        actor="agent",
    )


async def _is_category_name(conn: Connection, name: str) -> bool:
    """A category name is not a merchant: "Groceries" came from a description, not a shop."""
    row = await conn.fetchrow(
        "SELECT 1 FROM categories WHERE active AND lower(name) = lower($1) LIMIT 1", name
    )
    return row is not None


async def _is_placeholder_payee(conn: Connection, name: str) -> bool:
    """A name that names nobody: a category name, or a generic word like "Unknown shop"."""
    return payees_mod.is_placeholder_name(name) or await _is_category_name(conn, name)


async def _usual_account(conn: Connection, payee_id: str) -> str | None:
    row = await conn.fetchrow(
        """
        SELECT fa.id, count(*) AS n
        FROM pgledger_transfers t
        JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
        JOIN transfer_labels tl ON tl.transfer_id = t.id
        JOIN movements m ON m.id = t.metadata ->> 'movement_id'
        WHERE tl.payee_id = $1 AND fa.active AND fa.kind IN ('bank', 'cash', 'wallet', 'card')
        GROUP BY fa.id
        ORDER BY n DESC, fa.name
        LIMIT 1
        """,
        payee_id,
    )
    return str(row["id"]) if row else None


async def _default_account(conn: Connection, payee_id: str | None) -> str:
    if payee_id:
        usual = await _usual_account(conn, payee_id)
        if usual:
            return usual
    rows = await conn.fetch("SELECT id, name FROM accounts WHERE active AND liquid ORDER BY name")
    if len(rows) == 1:
        return str(rows[0]["id"])
    if not rows:
        raise ToolInputError("which account? No liquid account exists yet.")
    everyday = await _everyday_account(conn)
    if everyday:
        return everyday
    raise ToolInputError(
        "which account? Liquid accounts: " + ", ".join(row["name"] for row in rows)
    )


async def _everyday_account(conn: Connection) -> str | None:
    """The liquid account the user moves money from most, for a payee with no history."""
    row = await conn.fetchrow(
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
    )
    return str(row["id"]) if row else None


async def default_account_for_payee(conn: Connection, payee_id: str | None) -> str:
    """The account a capture should use: usual for this payee, else the everyday liquid one."""
    return await _default_account(conn, payee_id)


async def _possible_duplicate(
    conn: Connection,
    *,
    account_id: str,
    amount_paise: int,
    payee_id: str | None,
    exclude_key: str | None,
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT m.id, m.date, m.description
        FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
        JOIN transfer_labels tl ON tl.transfer_id = t.id
        JOIN captures c ON c.movement_id = m.id AND c.kind = 'chat'
        WHERE m.kind = 'expense'
          AND ($1::text IS NULL OR m.idempotency_key IS NULL OR m.idempotency_key <> $1)
          AND fa.id = $2
          AND t.amount = $3
          AND tl.payee_id IS NOT DISTINCT FROM $4
          AND m.created_at > now() - interval '10 minutes'
        ORDER BY m.created_at DESC LIMIT 1
        """,
        exclude_key,
        account_id,
        amount_paise,
        payee_id,
    )
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


def _required(data: dict[str, Any], key: str, label: str) -> Any:
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ToolInputError(f"{label} is required")
    return value


async def _audited_undo_id(conn: Connection, undo_id: str | None) -> str | None:
    """Only an audit group with a real change gets an undo button."""
    if not undo_id:
        return None
    row = await conn.fetchrow("SELECT 1 FROM audit_log WHERE group_id = $1 LIMIT 1", undo_id)
    return undo_id if row else None


def _card_bill(card: dict[str, Any], spent_on: date) -> dict[str, str] | None:
    """The statement a card purchase lands on, from the engine's own card cycle."""
    if card["statement_day"] is None or card["due_day"] is None:
        return None
    engine_card = CardAccount(
        id=str(card["id"]),
        name=str(card["name"]),
        statement_day=int(card["statement_day"]),
        due_day=int(card["due_day"]),
        outstanding_paise=0,
        credit_limit_paise=int(card["credit_limit_paise"] or 0),
    )
    return {
        "statement_close": statement_close(engine_card, spent_on).isoformat(),
        "due_date": card_due_date(engine_card, spent_on).isoformat(),
    }


def _movement_summary(result: dict[str, Any], amount_paise: int) -> str:
    transfers = result.get("transfers") or []
    if not transfers:
        return str(result.get("description") or "Recorded")
    first = transfers[0]
    text = money(amount_paise)["text"] if money(amount_paise) else ""
    if result.get("kind") == "expense":
        who = first.get("payee_name")
        # An unnamed shop says nothing: "to Food & Dining" would name a category as a merchant.
        line = (
            f"{text} to {who} from {first.get('from_name')}"
            if who
            else f"{text} from {first.get('from_name')}"
        )
        if first.get("category_name"):
            line += f", {first['category_name']}"
        return line
    if result.get("kind") == "income":
        who = first.get("payee_name") or first.get("from_name")
        return f"{text} into {first.get('to_name')} from {who}"
    return str(result.get("description") or "Recorded")


async def record_movement(
    conn: Connection,
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
    tool_call_id = movement.get("tool_call_id")
    # A woken turn has no message id; the tool call id alone still makes its retry idempotent.
    idempotency_key = (
        f"{message_id}:{tool_call_id}"
        if message_id
        else (str(tool_call_id) if tool_call_id else None)
    )
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
    # A generic name names no merchant and no person: no payee is created from it, so unnamed
    # spending never piles up under one invented shop. A settlement then names nobody, and the
    # tool asks who, which is the honest answer.
    if payee_name and not await _is_placeholder_payee(conn, str(payee_name)):
        payee = await resolve_payee(
            conn,
            str(payee_name),
            # A settlement names the person on the other side; a split's payee is the vendor.
            kind="person" if kind == "settlement" else "merchant",
            upi_id=movement.get("upi_id"),
            outside_text=named_outside(str(payee_name)),
        )
    payee_id: str | None = payee["id"] if payee is not None else None

    if kind == "expense":
        amount = _rupees(movement.get("amount_rupees"))
        account_id = (
            await resolve_account_id(conn, movement.get("account"))
            if movement.get("account")
            else await _default_account(conn, payee_id)
        )
        if not not_duplicate:
            duplicate = await _possible_duplicate(
                conn,
                account_id=account_id,
                amount_paise=amount,
                payee_id=payee_id,
                exclude_key=idempotency_key,
            )
            if duplicate:
                return duplicate
        result = await movements_mod.record_expense(
            conn,
            account_id=account_id,
            amount_paise=amount,
            description=description,
            payee_id=payee_id,
            category_id=await _resolve_category_id(conn, movement.get("category")),
            category_guess_id=await _resolve_category_id(conn, movement.get("category_guess")),
            date=when,
            note=note,
            idempotency_key=idempotency_key,
            capture=evidence,
            actor="agent",
            message_id=message_id,
        )
        summary = _movement_summary(result, amount)
        account_row = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", account_id)
        bill = (
            _card_bill(dict(account_row), date.fromisoformat(result["date"]))
            if account_row is not None and account_row["kind"] == "card"
            else None
        )
        if bill is not None:
            # The agent must quote the engine's cycle, not its own idea of the bill date.
            summary = f"{summary} · bill due {bill['due_date']}"
        return {
            "status": "recorded",
            "movement_id": result["id"],
            "undo_id": result["undo_id"],
            "date": result["date"],
            "summary": summary,
            "transfers": result["transfers"],
            "account_source": "stated" if movement.get("account") else "usual",
            "category_source": result.get("category_source", "none"),
            "card_bill": bill,
            "card": _card(
                "movement",
                title="Logged",
                summary=summary,
                amount=money(amount),
                undo_id=result["undo_id"],
                card_bill=bill,
                movement=result,
            ),
        }

    if kind == "income":
        amount = _rupees(movement.get("amount_rupees"))
        account_id = (
            await resolve_account_id(conn, movement.get("account"))
            if movement.get("account")
            else await _default_account(conn, payee_id)
        )
        result = await movements_mod.record_income(
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
        result = await movements_mod.record_transfer(
            conn,
            from_account_id=await resolve_account_id(conn, movement.get("from_account")),
            to_account_id=await resolve_account_id(conn, movement.get("to_account")),
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
        my_share = _balance_rupees(movement.get("my_share_rupees"), "my_share_rupees")
        if my_share < 0:
            raise ToolInputError("my_share_rupees cannot be negative")
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
            # Money owed by "someone" can never be collected or settled: ask who it is.
            if await _is_placeholder_payee(conn, str(name)):
                raise ToolInputError(f"{name!r} isn't a person; ask who owes this share")
            other = await resolve_payee(
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
            await resolve_account_id(conn, movement.get("account"))
            if movement.get("account")
            else await _default_account(conn, payee_id)
        )
        result = await movements_mod.record_split(
            conn,
            account_id=account_id,
            my_share_paise=my_share,
            people=people,
            total_paise=total,
            description=description,
            payee_id=payee_id,
            category_id=await _resolve_category_id(conn, movement.get("category")),
            category_guess_id=await _resolve_category_id(conn, movement.get("category_guess")),
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
        loan_id = await resolve_account_id(conn, movement.get("loan"))
        loan = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", loan_id)
        if loan is None or loan["kind"] != "loan":
            raise ToolInputError("a loan payment names a loan account")
        principal, interest = await movements_mod.loan_payment_parts(
            conn, loan, amount, on=when or ledger_today()
        )
        # A loan with no rate has unknown interest: record it as principal, but say so instead of
        # implying the payment was all principal by fact.
        warning = None
        if not loan["rate_bps"]:
            warning = (
                f"Interest is unknown: {loan['name']} has no rate on file, so all "
                f"{money(amount)['text']} was recorded as principal and the outstanding balance "
                f"may be overstated. Ask for the loan's rate or this payment's interest part."
            )
        account_id = (
            await resolve_account_id(conn, movement.get("account"))
            if movement.get("account")
            else await _default_account(conn, payee_id)
        )
        result = await movements_mod.record_loan_payment(
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
        summary = result["description"]
        payload = {
            "status": "recorded",
            "movement_id": result["id"],
            "undo_id": result["undo_id"],
            "date": result["date"],
            "summary": summary,
            "transfers": result["transfers"],
            "principal_paise": principal,
            "interest_paise": interest,
            "card": _card(
                "movement",
                title="Loan payment logged",
                summary=f"{summary} · {warning}" if warning else summary,
                amount=money(amount),
                undo_id=result["undo_id"],
            ),
        }
        if warning:
            payload["note"] = warning
        return payload

    if kind == "refund":
        amount = _rupees(movement.get("amount_rupees"))
        account_id = (
            await resolve_account_id(conn, movement.get("account"))
            if movement.get("account")
            else await _default_account(conn, payee_id)
        )
        result = await movements_mod.record_refund(
            conn,
            account_id=account_id,
            amount_paise=amount,
            description=description,
            payee_id=payee_id,
            category_id=await _resolve_category_id(conn, movement.get("category")),
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
        if payee_id is None:
            raise ToolInputError("a settlement names a person")
        account_id = (
            await resolve_account_id(conn, movement.get("account"))
            if movement.get("account")
            else await _default_account(conn, payee_id)
        )
        result = await movements_mod.record_settlement(
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


async def correct_movement(
    conn: Connection,
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
            mapped["category_id"] = await _resolve_category_id(conn, value)
        elif key == "account":
            mapped["account_id"] = await resolve_account_id(conn, value)
        elif key == "amount_rupees":
            mapped["amount_paise"] = _rupees(value)
        elif key == "my_share_rupees":
            mapped["my_share_paise"] = _balance_rupees(value, "my_share_rupees")
        elif key == "shares":
            mapped["shares"] = [
                {
                    # A person already on the split; never a new payee from a correction.
                    "payee_id": (
                        await _find_one(
                            conn,
                            "payees",
                            str(_required(share, "name", "a person's name")),
                            None,
                        )
                    )["id"],
                    "amount_paise": _rupees(share.get("amount_rupees"), "a person's share"),
                }
                for share in value
            ]
        elif key == "expected_date":
            mapped["expected_date"] = _date(value)
        elif key == "date":
            mapped["date"] = _date(value)
        elif key == "payee":
            name = str(value)
            if await _is_placeholder_payee(conn, name):
                raise ToolInputError(
                    f"{name!r} is not a payee name; a generic word names nobody. Use the real "
                    "merchant or person, or change something else."
                )
            mapped["payee_id"] = (await resolve_payee(conn, name))["id"]
        else:
            mapped[key] = value
    result = await movements_mod.correct_movement(
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
    undo_id = await _audited_undo_id(conn, result["undo_id"])
    return {
        "status": result["status"],
        "undo_id": undo_id,
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


_NAME_COLUMNS = {"payees": "display_name"}


async def _find_one(
    conn: Connection, table: str, name: str | None, key: str | None
) -> dict[str, Any]:
    if key:
        row = await conn.fetchrow(f"SELECT * FROM {quote_ident(table)} WHERE id = $1", key)
        if row:
            return row
    if not name:
        raise ToolInputError(f"{table[:-1]} needs a name or id")
    column = quote_ident(_NAME_COLUMNS.get(table, "name"))
    rows = await conn.fetch(
        f"SELECT * FROM {quote_ident(table)} WHERE lower({column}) = lower($1) "
        "ORDER BY created_at LIMIT 2",
        name,
    )
    if not rows:
        raise ToolInputError(f"No {table[:-1]} named {name!r}")
    if len(rows) > 1:
        raise ToolInputError(f"More than one {table[:-1]} is named {name!r}; use its ID")
    return rows[0]


async def update_setup_item(
    conn: Connection,
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
            name = _required(data, "name", "an account name")
            kind = data.get("kind") or "bank"
            opening = data.get("opening_balance_rupees")
            # A card or a loan is a liability: the balance the user states is what is owed.
            opening_paise = (
                _stated_balance_paise(opening, kind, "opening_balance_rupees")
                if opening is not None
                else None
            )
            result = await accounts_mod.create_account(
                conn,
                kind=kind,
                name=name,
                institution=data.get("institution"),
                last4=data.get("last4"),
                opening_balance_paise=opening_paise,
                opening_balance_as_of=_date(data.get("as_of")),
                liquid=data.get("liquid"),
                credit_limit_paise=(
                    _rupees(data["credit_limit_rupees"])
                    if data.get("credit_limit_rupees") is not None
                    else None
                ),
                statement_day=data.get("statement_day"),
                due_day=data.get("due_day"),
                rate_bps=(
                    _percent_to_bps(data["rate_percent"])
                    if data.get("rate_percent") is not None
                    else None
                ),
                tenure_months=data.get("tenure_months"),
                actor="agent",
                message_id=message_id,
            )
            summary = f"Added account {name}"
        else:
            if data.get("opening_balance_rupees") is not None:
                raise ToolInputError(
                    "An existing account's balance is restated with entity=balance, "
                    "not an opening balance"
                )
            row = await _find_one(conn, "accounts", data.get("name"), data.get("id"))
            if data.get("kind") is not None and data["kind"] != row["kind"]:
                raise ToolInputError("An account's kind cannot change; add a new account instead")
            if action == "archive":
                result = await accounts_mod.archive_account(
                    conn, row["id"], actor="agent", message_id=message_id
                )
                summary = f"Archived account {row['name']}"
            else:
                result = await accounts_mod.update_account(
                    conn,
                    row["id"],
                    name=data.get("new_name"),
                    institution=data.get("institution"),
                    last4=data.get("last4"),
                    liquid=data.get("liquid"),
                    credit_limit_paise=(
                        _rupees(data["credit_limit_rupees"])
                        if data.get("credit_limit_rupees") is not None
                        else None
                    ),
                    statement_day=data.get("statement_day"),
                    due_day=data.get("due_day"),
                    rate_bps=(
                        _percent_to_bps(data["rate_percent"])
                        if data.get("rate_percent") is not None
                        else None
                    ),
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
        account_id = await resolve_account_id(conn, data.get("account"))
        account = await accounts_mod.get_account(conn, account_id)
        # A card or a loan is a liability: "the balance is 5,000" means 5,000 owed, which the
        # ledger stores as -500000; "minus 500" means 500 in credit, the sign
        # `card_outstanding_paise` and the UI already use.
        paise = _stated_balance_paise(data.get("balance_rupees"), account["kind"])
        owing = account["kind"] in {"card", "loan"}
        result = await accounts_mod.set_statement_balance(
            conn,
            account_id,
            balance_paise=paise,
            as_of=_date(data.get("as_of")),
            actor="agent",
            message_id=message_id,
        )
        shown = money(paise)["text"]
        summary = f"Set {result['account']['name']} balance to {shown}"
        if owing and paise < 0:
            summary = f"Set {result['account']['name']} balance to {money(-paise)['text']} due"
        elif owing and paise > 0:
            summary = f"Set {result['account']['name']} balance to {shown} in credit"
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
            name = _required(data, "name", "a schedule name")
            rule = _required(data, "rule", "a schedule rule")
            result = await schedules_mod.create_schedule(
                conn,
                kind=data.get("kind") or "obligation",
                name=name,
                rule=rule,
                starts=_date(data.get("starts")) or ledger_today(),
                amount_paise=(
                    _rupees(data["amount_rupees"])
                    if data.get("amount_rupees") is not None
                    else None
                ),
                subtype=data.get("subtype"),
                account_id=(
                    await resolve_account_id(conn, data["account"]) if data.get("account") else None
                ),
                category_id=await _resolve_category_id(conn, data.get("category")),
                confirmed=bool(data.get("confirmed", True)),
                ends=_date(data.get("ends")),
                holiday_adjust=data.get("holiday_adjust") or "none",
                actor="agent",
                message_id=message_id,
            )
            summary = f"Added schedule {name}"
        else:
            row = await _find_one(conn, "schedules", data.get("name"), data.get("id"))
            if data.get("kind") is not None and data["kind"] != row["kind"]:
                raise ToolInputError("A schedule's kind cannot change; add a new schedule instead")
            if action == "archive":
                result = await schedules_mod.archive_schedule(
                    conn, row["id"], actor="agent", message_id=message_id
                )
                summary = f"Archived schedule {row['name']}"
            else:
                result = await schedules_mod.update_schedule(
                    conn,
                    row["id"],
                    name=data.get("new_name"),
                    amount_paise=(
                        _rupees(data["amount_rupees"])
                        if data.get("amount_rupees") is not None
                        else None
                    ),
                    rule=data.get("rule"),
                    subtype=data.get("subtype"),
                    account_id=(
                        await resolve_account_id(conn, data["account"])
                        if data.get("account")
                        else None
                    ),
                    category_id=await _resolve_category_id(conn, data.get("category")),
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
            name = _required(data, "name", "a goal name")
            result = await goals_mod.create_goal(
                conn,
                name=name,
                target_paise=_rupees(data.get("target_rupees"), "target_rupees"),
                deadline=_date(data.get("deadline")),
                priority=int(data.get("priority") or 3),
                actor="agent",
                message_id=message_id,
            )
            summary = f"Added goal {name}"
        else:
            row = await _find_one(conn, "goals", data.get("name"), data.get("id"))
            if action == "archive":
                result = await goals_mod.archive_goal(
                    conn, row["id"], actor="agent", message_id=message_id
                )
                summary = f"Archived goal {row['name']}"
            else:
                result = await goals_mod.update_goal(
                    conn,
                    row["id"],
                    name=data.get("new_name"),
                    target_paise=(
                        _rupees(data["target_rupees"])
                        if data.get("target_rupees") is not None
                        else None
                    ),
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
            name = _required(data, "name", "a category name")
            result = await categories_mod.create_category(
                conn,
                name=name,
                reducible=bool(data.get("reducible", False)),
                actor="agent",
                message_id=message_id,
            )
            summary = f"Added category {name}"
        else:
            row = await _find_one(conn, "categories", data.get("name"), data.get("id"))
            result = (
                await categories_mod.archive_category(
                    conn, row["id"], actor="agent", message_id=message_id
                )
                if action == "archive"
                else await categories_mod.update_category(
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
            name = _required(data, "name", "a payee name")
            result = await payees_mod.create_payee(
                conn,
                display_name=name,
                kind=data.get("kind") or "merchant",
                upi_id=data.get("upi_id"),
                default_category_id=await _resolve_category_id(conn, data.get("default_category")),
                create_person_account=bool(data.get("create_person_account", False)),
                actor="agent",
                message_id=message_id,
            )
            summary = f"Added payee {name}"
        else:
            row = await _find_one(conn, "payees", data.get("name"), data.get("id"))
            result = (
                await payees_mod.archive_payee(
                    conn, row["id"], actor="agent", message_id=message_id
                )
                if action == "archive"
                else await payees_mod.update_payee(
                    conn,
                    row["id"],
                    display_name=data.get("new_name"),
                    kind=data.get("kind"),
                    upi_id=data.get("upi_id"),
                    default_category_id=await _resolve_category_id(
                        conn, data.get("default_category")
                    ),
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
        floor_paise = _balance_rupees(floor, "floor_rupees") if floor is not None else None
        if floor_paise is not None and floor_paise < 0:
            raise ToolInputError("The floor cannot be negative")
        result = await settings_mod.update_settings(
            conn,
            floor_paise=floor_paise,
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
        if floor_paise is not None:
            summary = f"Set the floor to {money(floor_paise)['text']}"
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
            row = await conn.fetchrow(
                "SELECT * FROM scheduled_questions "
                "WHERE id = $1 OR lower(prompt) = lower($2) LIMIT 1",
                data.get("id"),
                data.get("prompt"),
            )
            if row is None:
                raise ToolInputError("No such scheduled question")
            result = await questions_mod.archive_question(
                conn, row["id"], actor="agent", message_id=message_id
            )
            summary = "Archived the scheduled question"
        elif action == "update":
            row = await conn.fetchrow(
                "SELECT * FROM scheduled_questions "
                "WHERE id = $1 OR lower(prompt) = lower($2) LIMIT 1",
                data.get("id"),
                data.get("prompt"),
            )
            if row is None:
                raise ToolInputError("No such scheduled question")
            result = await questions_mod.update_question(
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
                result = await questions_mod.create_question(
                    conn,
                    prompt=_required(data, "prompt", "a scheduled question"),
                    rule=_required(data, "rule", "a scheduled question rule"),
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
        category_id = await _resolve_category_id(conn, data.get("category"))
        if category_id is None:
            raise ToolInputError("a baseline names a category")
        amount = _rupees(data.get("amount_per_day_rupees"), "amount_per_day_rupees")
        async with write_group(conn, actor="agent", message_id=message_id) as group:
            before = await conn.fetchrow(
                "SELECT * FROM baselines WHERE category_id = $1", category_id
            )
            after = await conn.fetchrow_required(
                """
                INSERT INTO baselines (category_id, amount_per_day_paise, source, window_days)
                VALUES ($1, $2, 'estimated', 30)
                ON CONFLICT (category_id) DO UPDATE
                    SET amount_per_day_paise = EXCLUDED.amount_per_day_paise,
                        source = 'estimated', updated_at = now()
                RETURNING *
                """,
                category_id,
                amount,
            )
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


async def schedule_payment(
    conn: Connection,
    *,
    schedule: str,
    due_date: date,
    movement_id: str,
    action: str = "link",
    amount_rupees: Any = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    """Link existing money to one expected occurrence; never log or infer a payment here."""
    row = await conn.fetchrow("SELECT * FROM schedules WHERE id = $1", schedule)
    if row is None:
        matches = await conn.fetch(
            "SELECT * FROM schedules WHERE lower(name) = lower($1) LIMIT 2", schedule
        )
        if len(matches) != 1:
            raise ToolInputError("Use a unique schedule ID; that name is missing or ambiguous")
        row = matches[0]
    arguments = {
        "schedule_id": row["id"],
        "due_date": due_date,
        "movement_id": movement_id,
        "actor": "agent",
        "message_id": message_id,
    }
    if action == "link":
        amount = _rupees(amount_rupees, "amount_rupees") if amount_rupees is not None else None
        result = await schedule_payments.link_payment(conn, **arguments, amount_paise=amount)
    elif action == "unlink":
        result = await schedule_payments.unlink_payment(conn, **arguments)
    else:
        raise ToolInputError("action must be link or unlink")
    occurrence = await schedules_mod.get_occurrence(conn, row["id"], due_date)
    outside = occurrence.pop("outside_text", False)
    if outside:
        occurrence["name"] = mark(occurrence["name"])
    for payment in occurrence["payments"]:
        if payment["description"]:
            payment["description"] = mark(payment["description"])
            outside = True
    summary = (
        f"{result['status'].replace('_', ' ').capitalize()}: {occurrence['name']} · {due_date}"
    )
    return result | {
        "outside_text": outside,
        "occurrence": occurrence,
        "card": _card(
            "setup", title="Schedule payment", summary=summary, undo_id=result.get("undo_id")
        ),
    }


async def preference(
    conn: Connection,
    *,
    action: str,
    text: str | None,
    preference_id: str | None,
    message_id: str | None,
    kind: str | None = None,
) -> dict[str, Any]:
    # None means "not given": remember defaults to preference and keeps an existing memory's kind;
    # classify must name the kind it sets.
    if kind is not None and kind not in {"preference", "fact", "rule"}:
        raise ToolInputError("kind must be preference, fact or rule")
    if action == "remember":
        if not text or not text.strip():
            raise ToolInputError("what should I remember?")
        text = text.strip()
        existing = await conn.fetchrow(
            """
            SELECT * FROM preferences
            WHERE active AND deleted_at IS NULL AND lower(body) = lower($1)
            ORDER BY created_at LIMIT 1
            """,
            text,
        )
        if existing:
            if kind is not None and existing["kind"] != kind:
                async with write_group(conn, actor="agent", message_id=message_id) as group:
                    after = await conn.fetchrow_required(
                        "UPDATE preferences SET kind = $1, updated_at = now() "
                        "WHERE id = $2 RETURNING *",
                        kind,
                        existing["id"],
                    )
                    group.record_updated("preferences", existing["id"], existing, after)
                return {
                    "status": "remembered",
                    "id": existing["id"],
                    "undo_id": group.group_id,
                    "text": existing["body"],
                    "kind": after["kind"],
                    "card": _card(
                        "preference",
                        title="Reclassified",
                        text=existing["body"],
                        undo_id=group.group_id,
                    ),
                }
            return {
                "status": "remembered",
                "id": existing["id"],
                "undo_id": None,
                "text": existing["body"],
                "kind": existing["kind"],
                "card": _card("preference", title="Remembered", text=existing["body"]),
            }
        async with write_group(conn, actor="agent", message_id=message_id) as group:
            row = await conn.fetchrow_required(
                """
                INSERT INTO preferences (body, created_by, source_message_id, kind)
                VALUES ($1, 'agent', $2, $3) RETURNING *
                """,
                text,
                message_id,
                kind or "preference",
            )
            group.record_created("preferences", row)
        return {
            "status": "remembered",
            "id": row["id"],
            "undo_id": group.group_id,
            "text": row["body"],
            "kind": row["kind"],
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
            row = await conn.fetchrow(
                "SELECT * FROM preferences WHERE id = $1 AND deleted_at IS NULL",
                preference_id,
            )
        if row is None and text:
            row = await conn.fetchrow(
                """
                SELECT * FROM preferences
                WHERE active AND deleted_at IS NULL AND lower(body) = lower($1)
                ORDER BY created_at LIMIT 1
                """,
                text.strip(),
            )
        if row is None:
            active = await conn.fetch(
                "SELECT id, body FROM preferences WHERE active AND deleted_at IS NULL"
            )
            listed = "; ".join(f"{r['id']}: {r['body']}" for r in active) or "none"
            raise ToolInputError(f"No preference matched. Active preferences: {listed}")
        async with write_group(conn, actor="agent", message_id=message_id) as group:
            after = await conn.fetchrow_required(
                "UPDATE preferences SET active = false, updated_at = now() "
                "WHERE id = $1 RETURNING *",
                row["id"],
            )
            group.record_updated("preferences", row["id"], row, after)
        return {
            "status": "forgotten",
            "id": row["id"],
            "undo_id": group.group_id,
            "text": row["body"],
            "kind": row["kind"],
            "card": _card(
                "preference",
                title="Forgotten",
                text=row["body"],
                undo_id=group.group_id,
            ),
        }

    if action == "classify":
        if not preference_id or kind is None:
            raise ToolInputError("classify needs the memory id and its new kind")
        row = await conn.fetchrow(
            "SELECT * FROM preferences WHERE id = $1 AND deleted_at IS NULL", preference_id
        )
        if row is None:
            raise ToolInputError("No memory matched that ID")
        async with write_group(conn, actor="agent", message_id=message_id) as group:
            after = await conn.fetchrow_required(
                "UPDATE preferences SET kind = $1, updated_at = now() WHERE id = $2 RETURNING *",
                kind,
                preference_id,
            )
            group.record_updated("preferences", preference_id, row, after)
        return {
            "status": "classified",
            "id": preference_id,
            "kind": after["kind"],
            "text": row["body"],
            "undo_id": group.group_id,
            "card": _card(
                "preference",
                title="Reclassified",
                text=row["body"],
                undo_id=group.group_id,
            ),
        }

    raise ToolInputError("action is remember, forget or classify")


async def undo(
    conn: Connection,
    *,
    undo_id: str | None,
    conversation_id: str | None,
    message_id: str | None,
) -> dict[str, Any]:
    if undo_id:
        try:
            result = await undo_group(conn, undo_id, actor="agent", message_id=message_id)
        except Exception as exc:  # noqa: BLE001 - wrapper turns this into ModelRetry
            raise ToolInputError(str(exc)) from exc
    else:
        row = None
        if conversation_id:
            row = await conn.fetchrow(
                """
                SELECT a.group_id
                FROM audit_log a
                JOIN messages m ON m.id = a.message_id
                WHERE m.conversation_id = $1 AND a.undone_at IS NULL
                GROUP BY a.group_id
                ORDER BY max(a.created_at) DESC
                LIMIT 1
                """,
                conversation_id,
            )
        if row is None:
            result = await undo_latest(conn, actor="agent", message_id=message_id)
        else:
            result = await undo_group(
                conn, str(row["group_id"]), actor="agent", message_id=message_id
            )
    return {
        **result,
        "card": _card(
            "undo",
            title="Undone",
            summary=result.get("summary"),
            undo_undo_id=result.get("undo_undo_id"),
        ),
    }


async def delete_preference_for_good(conn: Connection, preference_id: str) -> dict[str, Any]:
    """Settings-only delete: blank the body and every audit copy, then delete the row."""
    row = await conn.fetchrow("SELECT * FROM preferences WHERE id = $1", preference_id)
    if row is None:
        raise ToolInputError("No such preference")
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE audit_log
            SET before = CASE WHEN before ? 'body'
                              THEN jsonb_set(before, '{body}', '\"\"') ELSE before END,
                after = CASE WHEN after ? 'body'
                             THEN jsonb_set(after, '{body}', '\"\"') ELSE after END
            WHERE table_name = 'preferences' AND row_id = $1
            """,
            preference_id,
        )
        await conn.execute("DELETE FROM preferences WHERE id = $1", preference_id)
        await conn.execute(
            """
            INSERT INTO audit_log (actor, group_id, table_name, row_id, before, after)
            VALUES ('user', pgledger_generate_id('und'), 'preferences', $1, $2, NULL)
            """,
            preference_id,
            {"id": preference_id, "body": ""},
        )
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
