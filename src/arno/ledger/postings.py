"""One balanced movement: a row in `movements` and one pgledger call for its transfers.

This is the only place that writes pgledger. Every transfer has a matching row in `movement_legs`
linking it to its movement, so transfers, labels, captures, and the audit log can always be joined
back to one movement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from arno.db.conn import Connection
from arno.ledger.audit import WriteGroup, india_noon, new_id
from arno.ledger.common import require_active_account
from arno.ledger.errors import InvalidLedgerError

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


async def _existing_by_key(conn: Connection, key: str) -> dict[str, Any] | None:
    return await conn.fetchrow("SELECT * FROM movements WHERE idempotency_key = $1", key)


async def _existing_record(conn: Connection, existing: dict[str, Any]) -> MovementRecord:
    rows = await conn.fetch(
        """
        SELECT ml.transfer_id AS id FROM movement_legs ml
        WHERE ml.movement_id = $1 ORDER BY ml.transfer_id
        """,
        existing["id"],
    )
    return MovementRecord(movement=existing, transfer_ids=tuple(row["id"] for row in rows))


async def create_movement_in_group(
    conn: Connection,
    group: WriteGroup,
    *,
    date: date,
    description: str,
    kind: str,
    transfers: list[TransferSpec],
    reverses_id: str | None = None,
    idempotency_key: str | None = None,
    capture: dict[str, Any] | None = None,
    accounts: dict[str, dict[str, Any]] | None = None,
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
        existing = await _existing_by_key(conn, idempotency_key)
        if existing is not None:
            return await _existing_record(conn, existing)

    known_accounts: dict[str, dict[str, Any]] = dict(accounts) if accounts else {}
    resolved: list[tuple[TransferSpec, dict[str, Any], dict[str, Any]]] = []
    for transfer in transfers:
        if transfer.amount_paise <= 0:
            raise InvalidLedgerError("Transfer amounts must be positive")
        if transfer.from_account_id == transfer.to_account_id:
            raise InvalidLedgerError("A transfer needs two different accounts")
        from_account = known_accounts.get(transfer.from_account_id)
        if from_account is None or not from_account["active"]:
            from_account = await require_active_account(conn, transfer.from_account_id)
            known_accounts[transfer.from_account_id] = from_account
        to_account = known_accounts.get(transfer.to_account_id)
        if to_account is None or not to_account["active"]:
            to_account = await require_active_account(conn, transfer.to_account_id)
            known_accounts[transfer.to_account_id] = to_account
        if from_account["ledger_account_id"] == to_account["ledger_account_id"]:
            raise InvalidLedgerError("A transfer needs two different ledger accounts")
        resolved.append((transfer, from_account, to_account))

    movement_id = await new_id(conn, "mov")
    movement = await conn.fetchrow(
        """
        INSERT INTO movements (id, date, description, kind, reverses_id, idempotency_key)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING *
        """,
        movement_id,
        date,
        description.strip(),
        kind,
        reverses_id,
        idempotency_key,
    )
    if movement is None:
        # A concurrent call with the same key committed first: return its movement, not a second.
        if idempotency_key is None:  # pragma: no cover - only a keyed insert can conflict
            raise InvalidLedgerError("An insert without an idempotency key returned no movement")
        existing = await _existing_by_key(conn, idempotency_key)
        if existing is None:  # pragma: no cover - the unique index guarantees the row exists
            raise InvalidLedgerError("An idempotency key conflict left no movement")
        return await _existing_record(conn, existing)
    group.record_created("movements", movement)

    row_placeholders: list[str] = []
    params: list[Any] = []
    for transfer, from_account, to_account in resolved:
        start = len(params) + 1
        row_placeholders.append(
            f"ROW(${start}::text, ${start + 1}::text, ${start + 2}::numeric)::transfer_request"
        )
        params.extend(
            [
                from_account["ledger_account_id"],
                to_account["ledger_account_id"],
                transfer.amount_paise,
            ]
        )
    event_index = len(params) + 1
    params.extend(
        [
            india_noon(date),
            None,
            idempotency_key,
        ]
    )
    created = await conn.fetch(
        f"""
        SELECT id, from_account_id, to_account_id, amount::bigint AS amount
        FROM pgledger_create_transfers(
            ARRAY[{", ".join(row_placeholders)}], ${event_index}, ${event_index + 1}::jsonb,
            ${event_index + 2}
        )
        """,
        *params,
    )

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
        leg = await conn.fetchrow_required(
            """
            INSERT INTO movement_legs
                (transfer_id, movement_id, category_id, payee_id, expected_date, note)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            row["id"],
            movement_id,
            transfer.category_id,
            transfer.payee_id,
            transfer.expected_date,
            transfer.note,
        )
        if transfer.has_label():
            # An unlabelled leg is not a change the user made, so undo does not count it.
            group.record_created("movement_legs", leg)

    if capture is not None:
        capture_row = await conn.fetchrow_required(
            """
            INSERT INTO captures (movement_id, kind, ref, upi_ref, confidence)
            VALUES ($1, $2, $3, $4, $5) RETURNING *
            """,
            movement_id,
            capture.get("kind", "chat"),
            capture.get("ref"),
            capture.get("upi_ref"),
            capture.get("confidence"),
        )
        group.record_created("captures", capture_row)

    return MovementRecord(movement=movement, transfer_ids=tuple(ordered_ids))
