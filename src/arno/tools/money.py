"""Plain-Python money-maths tools: resolving ledger accounts and calling engine/money."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

from arno.db.conn import Connection
from arno.engine.money import card_payoff as card_payoff_fn
from arno.engine.money import money
from arno.ledger.common import has_known_balance, ledger_balance
from arno.tools.mark import mark


class ToolInputError(ValueError):
    """A message the model can act on, delivered as ModelRetry by the wrapper."""


def _finite_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ToolInputError(f"{field} must be a number")
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ToolInputError(f"{field} must be a number") from exc
    if not dec.is_finite():
        raise ToolInputError(f"{field} must be a finite number")
    return dec


def percent_to_bps(value: Any) -> int:
    """An annual percent as integer basis points, rounded half up like every other amount."""
    dec = _finite_decimal(value, "rate")
    return int((dec * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def monthly_percent_to_annual_bps(value: Any) -> int:
    """An issuer's monthly percent as annual basis points: 3.5% a month is 4,200 a year."""
    dec = _finite_decimal(value, "monthly_rate_percent")
    return percent_to_bps(dec * 12)


def apr_percent(fit: Mapping[str, Any] | None) -> float | None:
    """Quotable annual APR percent rounded to two decimals from a RateFit interval."""
    if not isinstance(fit, Mapping):
        return None
    low = fit.get("low_bps_x100")
    high = fit.get("high_bps_x100")
    if low is None or high is None:
        return None
    if low == 0 and high == 0:
        return 0.0
    midpoint = (Decimal(int(low)) + Decimal(int(high))) / Decimal(2) / Decimal(10_000)
    return float(midpoint.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


async def card_payoff(
    conn: Connection | None,
    *,
    card: str | None = None,
    balance_paise: int | None = None,
    monthly_rate_percent: float | None = None,
    payment_paise: int | None = None,
    minimum_percent: float | None = None,
    minimum_floor_paise: int | None = None,
    start_date: date | None = None,
) -> dict[str, Any]:
    """Payoff schedule and carrying cost for a revolving credit card balance."""
    card_row: Any = None
    if card is not None:
        card_name = card.strip()
        if not card_name:
            raise ToolInputError("Card name cannot be empty")
        if conn is None:
            raise ValueError("a database connection is required to look up a card")
        rows = await conn.fetch(
            """
            SELECT v.id, v.kind, v.name, v.ledger_account_id, v.statement_balance_paise,
                   v.statement_balance_as_of, v.finance_rate_bps, v.min_due_bps,
                   v.min_due_floor_paise, coalesce(p.outside_text, false) AS outside_text
            FROM v_accounts v
            LEFT JOIN payees p ON v.kind = 'person' AND p.person_account_id = v.id
            WHERE v.active AND lower(v.name) = lower($1)
            ORDER BY v.name
            """,
            card_name,
        )
        cards = [row for row in rows if row["kind"] == "card"]
        others = [row for row in rows if row["kind"] != "card"]
        if len(cards) > 1:
            raise ToolInputError(f"More than one card matches {card_name!r}")
        if not cards:
            if others:
                other = others[0]
                other_name = mark(other["name"]) if other["outside_text"] else other["name"]
                raise ToolInputError(f"{other_name!r} is a {other['kind']} account, not a card")
            raise ToolInputError(f"No card named {card_name!r}")
        card_row = cards[0]

    if balance_paise is not None:
        if isinstance(balance_paise, bool) or not isinstance(balance_paise, int):
            raise ToolInputError("balance_paise must be an integer")
        if balance_paise <= 0:
            raise ToolInputError("balance_paise must be positive")
        effective_balance = balance_paise
        balance_source = "as you stated"
    elif card_row is not None:
        if conn is None:
            raise ValueError("a database connection is required to look up a card balance")
        if not has_known_balance(card_row):
            raise ToolInputError(
                f"{card_row['name']}'s balance is unknown; pass balance_paise or record a "
                "statement balance first"
            )
        bal = await ledger_balance(conn, card_row["ledger_account_id"])
        effective_balance = -int(bal)
        if effective_balance <= 0:
            raise ToolInputError(f"{card_row['name']} has no balance owed")
        balance_source = "stored on the card"
    else:
        raise ToolInputError("Pass a card name or balance_paise")

    quoted_monthly_rate: float
    if monthly_rate_percent is not None:
        monthly_rate_dec = _finite_decimal(monthly_rate_percent, "monthly_rate_percent")
        if monthly_rate_dec < 0:
            raise ToolInputError("monthly_rate_percent cannot be negative")
        annual_rate_bps = monthly_percent_to_annual_bps(monthly_rate_dec)
        quoted_monthly_rate = float(monthly_rate_dec)
        rate_source = "as you stated"
    elif card_row is not None and card_row["finance_rate_bps"] is not None:
        annual_rate_bps = int(card_row["finance_rate_bps"])
        quoted_monthly_rate = float(Decimal(annual_rate_bps) / Decimal(1200))
        rate_source = "stored on the card"
    else:
        raise ToolInputError(
            "No finance charge is known for this card. Ask the user for the card's finance "
            "charge in percent a month from their statement; you can also record it with "
            "update_setup."
        )

    minimum_bps: int | None = None
    if minimum_percent is not None:
        minimum_bps = percent_to_bps(_finite_decimal(minimum_percent, "minimum_percent"))
    elif card_row is not None and card_row["min_due_bps"] is not None:
        minimum_bps = int(card_row["min_due_bps"])

    effective_floor: int | None = None
    if minimum_floor_paise is not None:
        if isinstance(minimum_floor_paise, bool) or not isinstance(minimum_floor_paise, int):
            raise ToolInputError("minimum_floor_paise must be an integer")
        effective_floor = minimum_floor_paise
    elif card_row is not None and card_row["min_due_floor_paise"] is not None:
        effective_floor = int(card_row["min_due_floor_paise"])

    if payment_paise is not None:
        if isinstance(payment_paise, bool) or not isinstance(payment_paise, int):
            raise ToolInputError("payment_paise must be an integer")
        payment: Literal["minimum"] | int = payment_paise
    else:
        payment = "minimum"
    engine_result = card_payoff_fn(
        effective_balance,
        annual_rate_bps=annual_rate_bps,
        payment=payment,
        minimum_bps=minimum_bps,
        minimum_floor_paise=effective_floor,
        start_date=start_date,
    )
    return {
        **engine_result,
        "balance": money(effective_balance),
        "balance_source": balance_source,
        "monthly_rate_percent": quoted_monthly_rate,
        "annual_rate_bps": annual_rate_bps,
        "rate_source": rate_source,
        "payment_mode": "fixed" if payment_paise is not None else "minimum",
        "fixed_payment": money(payment_paise) if payment_paise is not None else None,
    }


__all__ = [
    "ToolInputError",
    "apr_percent",
    "card_payoff",
    "monthly_percent_to_annual_bps",
    "percent_to_bps",
]
