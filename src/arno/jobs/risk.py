"""Risk warnings: rerun the projection nightly and warn when the floor chance is low.

The check warns when the chance of staying above the floor (above zero when no floor is set)
falls below the engine's own threshold. The notification key carries the ISO week, so the same
risk warns once a week, not every night, and a restart cannot send it twice.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from arno.db.conn import Connection, connect
from arno.engine.money import money
from arno.engine.projection import BASE_HORIZON_DAYS, UNUSABLE_SETUP_GAPS
from arno.ledger.common import today as ledger_today
from arno.notifications import create_notification
from arno.tools import projection as projection_tools


async def risk_warning(
    conn: Connection, *, horizon_days: int = BASE_HORIZON_DAYS, as_of: date | None = None
) -> dict[str, Any] | None:
    """Check the projection and create at most one warning for the current week."""
    projection = await projection_tools.project(
        conn,
        horizon_days=horizon_days,
        horizon_limit_days=max(BASE_HORIZON_DAYS, horizon_days),
    )
    data_quality = projection["data_quality"]
    ages = data_quality.get("balance_ages")
    if ages is not None and not any(
        row.get("known") and row.get("kind") in {"bank", "cash", "wallet"} for row in ages
    ):
        # No verified liquid balance: the curve is a provisional net change, not a low balance.
        return None
    # A projection with no income or liquid account measures its own missing setup, not danger,
    # so onboarding must not open with a warning.
    if any(gap in UNUSABLE_SETUP_GAPS for gap in data_quality.get("setup_gaps") or []):
        return None

    probability = float(projection["floor_probability"])
    threshold = float(projection["floor_threshold"])
    if probability >= threshold:
        return None
    floor = int(projection["floor_paise"])
    # No floor set is not "nothing to check": the same threshold applies to going below zero.
    level = "your floor" if floor > 0 else "zero"
    body = (
        f"The chance of staying above {level} over the next horizon is "
        f"{round(probability * 100)}%, below the {round(threshold * 100)}% line. "
        + (
            f"Floor {money(floor)['text']}. "
            if floor
            else "No floor is set, so the check is against zero. "
        )
        + "This is a projection, not a prediction; the data-quality notes are in the app."
    )
    week = (as_of or ledger_today()).strftime("%G-W%V")
    return await create_notification(
        conn,
        kind="risk_warning",
        title="Your projection is riskier",
        body=body,
        data={
            "floor_probability": probability,
            "floor_threshold": threshold,
            "floor_paise": floor,
            "first_floor_crossing": projection["first_floor_crossing"],
            "source_ids": projection["source_ids"],
            "data_quality": data_quality.get("gaps", [])[:5],
        },
        dedupe_key=f"risk_warning:{week}",
    )


async def handle_risk_check() -> None:
    async with connect() as conn:
        await risk_warning(conn)


__all__ = ["handle_risk_check", "risk_warning"]
