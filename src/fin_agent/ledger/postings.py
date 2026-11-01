"""One balanced movement: a row in `movements` and one pgledger call for its transfers.

This is the only place that writes pgledger. Every transfer carries `{movement_id}` in metadata,
so transfers, labels, captures, and the audit log can always be joined back to one movement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from fin_agent.ledger.audit import WriteGroup, india_noon, new_id
from fin_agent.ledger.common import require_active_account
from fin_agent.ledger.errors import InvalidLedgerError

MOVEMENT_KINDS = (
    "expense",
    "income",
    "transfer",
    "split",
    "loan",
    "refund",
    "settlement",
    "adjustment",
    "reversal",
)


@dataclass(frozen=True, slots=True)
class TransferSpec:
    from_account_id: str
    to_account_id: str
    amount_paise: int
    category_id: str | None = None
    payee_id: str | None = None
    expected_date: date | None = None
    note: str | None = None

    def has_label(self) -> bool:
        return any(
            value is not None
            for value in (self.category_id, self.payee_id, self.expected_date, self.note)
        )


@dataclass(frozen=True, slots=True)
class MovementRecord:
    movement: dict[str, Any]
    transfer_ids: tuple[str, ...]

    @property
    def id(self) -> str:
        return str(self.movement["id"])


def _existing_by_key(conn: psycopg.Connection, key: str) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM movements WHERE idempotency_key = %s", (key,)).fetchone()


def create_movement_in_group(
    conn: psycopg.Connection,
    group: WriteGroup,
    *,
    date: date,
    description: str,
    kind: str,
    transfers: list[TransferSpec],
    reverses_id: str | None = None,
    idempotency_key: str | None = None,
    capture: dict[str, Any] | None = None,
) -> MovementRecord:
    if kind not in MOVEMENT_KINDS:
        raise InvalidLedgerError(f"Unknown movement kind {kind}")
    if not transfers:
        raise InvalidLedgerError("A movement needs at least one transfer")
    if not description.strip():
        raise InvalidLedgerError("A movement needs a description")
    if reverses_id and kind != "reversal":
        # A correction is a reversal plus a corrected movement, never a silently edited row.
        raise InvalidLedgerError("Only a reversal movement may point at the movement it reverses")

    if idempotency_key:
        existing = _existing_by_key(conn, idempotency_key)
        if existing is not None:
            ids = tuple(
                row["id"]
                for row in conn.execute(
                    """
                    SELECT t.id FROM pgledger_transfers t
                    WHERE t.metadata ->> 'movement_id' = %s ORDER BY t.id
                    """,
                    (existing["id"],),
                ).fetchall()
            )
            return MovementRecord(movement=existing, transfer_ids=ids)

    resolved: list[tuple[TransferSpec, dict[str, Any], dict[str, Any]]] = []
    for transfer in transfers:
        if transfer.amount_paise <= 0:
            raise InvalidLedgerError("Transfer amounts must be positive")
        if transfer.from_account_id == transfer.to_account_id:
            raise InvalidLedgerError("A transfer needs two different accounts")
        from_account = require_active_account(conn, transfer.from_account_id)
        to_account = require_active_account(conn, transfer.to_account_id)
        if from_account["ledger_account_id"] == to_account["ledger_account_id"]:
            raise InvalidLedgerError("A transfer needs two different ledger accounts")
        resolved.append((transfer, from_account, to_account))

    movement_id = new_id(conn, "mov")
    movement = conn.execute(
        """
        INSERT INTO movements (id, date, description, kind, reverses_id, idempotency_key)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (movement_id, date, description.strip(), kind, reverses_id, idempotency_key),
    ).fetchone()
    group.record_created("movements", movement)

    placeholders = ", ".join(
        ["ROW(%s::text, %s::text, %s::numeric)::transfer_request"] * len(resolved)
    )
    params: list[Any] = []
    for transfer, from_account, to_account in resolved:
        params.extend(
            [
                from_account["ledger_account_id"],
                to_account["ledger_account_id"],
                transfer.amount_paise,
            ]
        )
    params.extend(
        [
            india_noon(date),
            Jsonb({"movement_id": movement_id}),
            idempotency_key,
        ]
    )
    created = conn.execute(
        f"""
        SELECT id, from_account_id, to_account_id, amount::bigint AS amount
        FROM pgledger_create_transfers(ARRAY[{placeholders}], %s, %s::jsonb, %s)
        """,
        params,
    ).fetchall()

    unused = list(created)
    ordered_ids: list[str] = []
    for transfer, from_account, to_account in resolved:
        match_index = next(
            (
                index
                for index, row in enumerate(unused)
                if row["from_account_id"] == from_account["ledger_account_id"]
                and row["to_account_id"] == to_account["ledger_account_id"]
                and int(row["amount"]) == transfer.amount_paise
            ),
            None,
        )
        if match_index is None:  # pragma: no cover - pgledger returned something unexpected
            raise InvalidLedgerError("pgledger returned a transfer that was not requested")
        row = unused.pop(match_index)
        ordered_ids.append(str(row["id"]))
        if transfer.has_label():
            label = conn.execute(
                """
                INSERT INTO transfer_labels
                    (transfer_id, movement_id, category_id, payee_id, expected_date, note)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    row["id"],
                    movement_id,
                    transfer.category_id,
                    transfer.payee_id,
                    transfer.expected_date,
                    transfer.note,
                ),
            ).fetchone()
            group.record_created("transfer_labels", label)

    if capture is not None:
        capture_row = conn.execute(
            """
            INSERT INTO captures (movement_id, kind, ref, upi_ref, confidence)
            VALUES (%s, %s, %s, %s, %s) RETURNING *
            """,
            (
                movement_id,
                capture.get("kind", "chat"),
                capture.get("ref"),
                capture.get("upi_ref"),
                capture.get("confidence"),
            ),
        ).fetchone()
        group.record_created("captures", capture_row)

    return MovementRecord(movement=movement, transfer_ids=tuple(ordered_ids))
