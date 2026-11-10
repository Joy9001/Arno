"""Assign recorded ledger money to recurring plan occurrences, without moving money.

Expected occurrences are expanded from the schedule. The first assignment freezes its terms.
Payment status is always derived from live movement evidence, including reversal parity.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fin_agent.db.conn import Connection
from fin_agent.india.recurrence import expand_rule
from fin_agent.india.workdays import calendar_for
from fin_agent.ledger.audit import WriteGroup, write_group
from fin_agent.ledger.common import today
from fin_agent.ledger.errors import ConflictLedgerError, InvalidLedgerError, NotFoundLedgerError
from fin_agent.ledger.movements import _reversal_state, _transfer_rows


async def _schedule(conn: Connection, schedule_id: str) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM schedules WHERE id = $1 FOR UPDATE", schedule_id)
    if row is None:
        raise NotFoundLedgerError(f"No schedule {schedule_id}")
    return row


async def _occurrence(
    conn: Connection, group: WriteGroup, schedule: dict[str, Any], due_date: date
) -> dict[str, Any]:
    existing = await conn.fetchrow(
        "SELECT * FROM schedule_occurrences WHERE schedule_id = $1 AND due_date = $2",
        schedule["id"],
        due_date,
    )
    if existing:
        return existing
    dates = expand_rule(
        schedule["rule"],
        schedule["starts"],
        start=due_date,
        end=due_date,
        ends=schedule["ends"],
        holiday_adjust=schedule["holiday_adjust"],
        calendar=calendar_for(due_date - timedelta(days=7), due_date + timedelta(days=7)),
    )
    if due_date not in dates:
        raise InvalidLedgerError("That date is not an occurrence of this schedule")
    row = await conn.fetchrow(
        """INSERT INTO schedule_occurrences
           (schedule_id, due_date, name, kind, amount_paise, account_id, category_id,
            subtype, outside_text)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING *""",
        schedule["id"],
        due_date,
        schedule["name"],
        schedule["kind"],
        schedule["amount_paise"],
        schedule["account_id"],
        schedule["category_id"],
        schedule["subtype"],
        schedule["outside_text"],
    )
    group.record_created("schedule_occurrences", row)
    return row


async def _capacity(conn: Connection, occurrence: dict[str, Any], movement: dict[str, Any]) -> int:
    kind = occurrence["kind"]
    incoming = kind.startswith("income")
    if incoming and movement["kind"] != "income":
        raise InvalidLedgerError("An income occurrence needs a recorded income movement")
    if kind == "goal_contribution" and movement["kind"] != "transfer":
        raise InvalidLedgerError("A goal contribution needs a recorded transfer to its goal")
    if (
        not incoming
        and kind != "goal_contribution"
        and movement["kind"]
        not in {
            "expense",
            "loan",
            "split",
            "settlement",
            "transfer",
        }
    ):
        raise InvalidLedgerError("This occurrence needs a recorded outgoing payment")
    goal_id = None
    if kind == "goal_contribution":
        goals = await conn.fetch(
            "SELECT account_id FROM goals WHERE contribution_schedule_id = $1",
            occurrence["schedule_id"],
        )
        if len(goals) != 1:
            raise InvalidLedgerError("This contribution schedule needs exactly one linked goal")
        goal_id = goals[0]["account_id"]
    amount = 0
    for transfer in await _transfer_rows(conn, movement["id"]):
        if (
            movement["kind"] == "transfer"
            and not goal_id
            and transfer["to_kind"] not in {"card", "person"}
        ):
            # A bill paid to a card, or money sent to a person (a family transfer), is payment;
            # moving money between your own accounts is not.
            continue
        side = "to" if incoming else "from"
        if transfer[f"{side}_kind"] not in {"bank", "cash", "wallet", "card"}:
            continue
        if occurrence["account_id"] and transfer[f"{side}_account_id"] != occurrence["account_id"]:
            continue
        if goal_id and transfer["to_account_id"] != goal_id:
            continue
        amount += int(transfer["amount_paise"])
    if not amount:
        raise InvalidLedgerError("The movement does not pay through this schedule’s account")
    return amount


async def link_payment(
    conn: Connection,
    *,
    schedule_id: str,
    due_date: date,
    movement_id: str,
    amount_paise: int | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    if amount_paise is not None and amount_paise <= 0:
        raise InvalidLedgerError("A payment allocation must be positive")
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        schedule = await _schedule(conn, schedule_id)
        movement = await conn.fetchrow(
            "SELECT * FROM movements WHERE id = $1 FOR UPDATE",
            movement_id,
        )
        if movement is None:
            raise NotFoundLedgerError(f"No movement {movement_id}")
        if (
            movement["kind"] == "reversal"
            or (await _reversal_state(conn, [movement_id]))[movement_id]["reversed"]
        ):
            raise InvalidLedgerError("A reversed movement cannot cover a schedule occurrence")
        if due_date > movement["date"] + timedelta(days=31):
            raise InvalidLedgerError(await _far_match_message(conn, schedule, movement, due_date))
        occurrence = await _occurrence(conn, group, schedule, due_date)
        previous = await conn.fetchrow(
            "SELECT * FROM schedule_payments WHERE occurrence_id = $1 AND movement_id = $2",
            occurrence["id"],
            movement_id,
        )
        if previous:
            if amount_paise is not None and amount_paise != previous["amount_paise"]:
                raise ConflictLedgerError(
                    "Unlink the existing allocation before changing its amount"
                )
            return {"status": "already_linked", "payment_id": previous["id"], "undo_id": None}
        capacity = await _capacity(conn, occurrence, movement)
        allocated = await conn.fetchval(
            "SELECT coalesce(sum(amount_paise), 0) FROM schedule_payments WHERE movement_id = $1",
            movement_id,
        )
        available = capacity - int(allocated)
        amount = amount_paise if amount_paise is not None else available
        if amount <= 0 or amount > available:
            raise InvalidLedgerError(
                "The allocation exceeds the movement’s unassigned payment amount"
            )
        row = await conn.fetchrow(
            """INSERT INTO schedule_payments (occurrence_id, movement_id, amount_paise)
               VALUES ($1, $2, $3) RETURNING *""",
            occurrence["id"],
            movement_id,
            amount,
        )
        group.record_created("schedule_payments", row)
    return {
        "status": "linked",
        "payment_id": row["id"],
        "amount_paise": amount,
        "undo_id": group.group_id,
    }


async def _far_match_message(
    conn: Connection,
    schedule: dict[str, Any],
    movement: dict[str, Any],
    due_date: date,
) -> str:
    """Why a payment cannot cover an occurrence more than a month ahead, and which one can."""
    from fin_agent.ledger.schedules import expand_schedule

    events = await expand_schedule(
        conn, schedule, movement["date"], movement["date"] + timedelta(days=366)
    )
    nearest = next(
        (
            event["date"]
            for event in events
            if event["remaining_paise"] is None or event["remaining_paise"] > 0
        ),
        None,
    )
    message = (
        f"That payment is dated {movement['date']}, more than 31 days before the occurrence due "
        f"{due_date}. A payment covers the occurrence it pays, not one over a month later."
    )
    if nearest:
        return f"{message} Nearest open occurrence: {nearest}."
    return f"{message} It has no open occurrence within the next year."


async def validate_restored_payment(conn: Connection, payment: dict[str, Any]) -> None:
    """Undo must not reassign money that a later match already used."""
    occurrence = await conn.fetchrow(
        "SELECT * FROM schedule_occurrences WHERE id = $1", payment["occurrence_id"]
    )
    movement = await conn.fetchrow(
        "SELECT * FROM movements WHERE id = $1 FOR UPDATE", payment["movement_id"]
    )
    capacity = await _capacity(conn, occurrence, movement)
    assigned = await conn.fetchval(
        "SELECT coalesce(sum(amount_paise), 0) FROM schedule_payments WHERE movement_id = $1",
        payment["movement_id"],
    )
    if int(assigned) + int(payment["amount_paise"]) > capacity:
        raise ConflictLedgerError("Unlink the later payment match before undoing this change")


async def lock_payment_undo(conn: Connection, entries: list[dict[str, Any]]) -> None:
    """Take the same schedule-then-movement locks as normal matching, including deleted rows."""
    schedules: set[str] = set()
    occurrences: set[str] = set()
    movements: set[str] = set()
    for entry in entries:
        row = entry["before"] or entry["after"]
        if entry["table_name"] == "schedule_occurrences" and row:
            schedules.add(row["schedule_id"])
        if entry["table_name"] == "schedule_payments" and row:
            occurrences.add(row["occurrence_id"])
            movements.add(row["movement_id"])
    rows = await conn.fetch(
        "SELECT schedule_id FROM schedule_occurrences WHERE id = ANY($1::text[])",
        list(occurrences),
    )
    schedules.update(row["schedule_id"] for row in rows)
    await conn.fetch(
        "SELECT id FROM schedules WHERE id = ANY($1::text[]) ORDER BY id FOR UPDATE",
        sorted(schedules),
    )
    await conn.fetch(
        "SELECT id FROM movements WHERE id = ANY($1::text[]) ORDER BY id FOR UPDATE",
        sorted(movements),
    )


async def unlink_payment(
    conn: Connection,
    *,
    schedule_id: str,
    due_date: date,
    movement_id: str,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        await _schedule(conn, schedule_id)
        await conn.fetchrow("SELECT id FROM movements WHERE id = $1 FOR UPDATE", movement_id)
        row = await conn.fetchrow(
            """SELECT p.* FROM schedule_payments p
               JOIN schedule_occurrences o ON o.id = p.occurrence_id
               WHERE o.schedule_id = $1 AND o.due_date = $2 AND p.movement_id = $3""",
            schedule_id,
            due_date,
            movement_id,
        )
        if row is None:
            return {"status": "not_linked", "undo_id": None}
        await conn.execute("DELETE FROM schedule_payments WHERE id = $1", row["id"])
        group.record_deleted("schedule_payments", row)
    return {"status": "unlinked", "payment_id": row["id"], "undo_id": group.group_id}


async def payment_evidence(
    conn: Connection,
    schedule_id: str,
    start: date,
    end: date,
    *,
    as_of: date | None = None,
) -> dict[date, dict[str, Any]]:
    as_of = as_of or today()
    rows = await conn.fetch(
        """SELECT o.*, p.id AS payment_id, p.amount_paise AS allocated_paise,
                  m.id AS movement_id, m.date AS movement_date, m.description
           FROM schedule_occurrences o
           LEFT JOIN schedule_payments p ON p.occurrence_id = o.id
           LEFT JOIN movements m ON m.id = p.movement_id AND m.date <= $4
           WHERE o.schedule_id = $1 AND o.due_date BETWEEN $2 AND $3
           ORDER BY o.due_date, m.date, p.id""",
        schedule_id,
        start,
        end,
        as_of,
    )
    states = await _reversal_state(
        conn,
        list({row["movement_id"] for row in rows if row["movement_id"]}),
        as_of=as_of,
    )
    result: dict[date, dict[str, Any]] = {}
    for row in rows:
        day = row["due_date"]
        item = result.setdefault(day, {**dict(row), "paid_paise": 0, "payments": []})
        if not row["movement_id"]:
            continue
        active = not states[row["movement_id"]]["reversed"]
        amount = int(row["allocated_paise"])
        if active:
            item["paid_paise"] += amount
        item["payments"].append(
            {
                "id": row["payment_id"],
                "movement_id": row["movement_id"],
                "date": row["movement_date"].isoformat(),
                "description": row["description"],
                "amount_paise": amount,
                "active": active,
            }
        )
    return result


def payment_status(expected: int | None, paid: int, due_date: date, as_of: date) -> str:
    if paid:
        if expected is None:
            return "recorded"
        return "paid" if paid >= expected else "partial"
    return "unmatched" if due_date < as_of else "scheduled"
