"""Risk warnings: rerun the projection after a write and nightly, warn once per episode.

An episode begins when the floor probability falls below the engine's own threshold, or when the
first projected floor crossing moves earlier. The state row is updated in the same transaction as
the warning, so a retried job cannot send the same episode twice. When the picture recovers the
state re-arms, and a later turn warns again.
"""

from __future__ import annotations

import logging
from contextlib import closing
from datetime import date
from typing import Any

import psycopg

from fin_agent.db.conn import connect
from fin_agent.engine.money import money
from fin_agent.ledger.audit import new_id
from fin_agent.notifications import create_notification, send_push_for_kind
from fin_agent.tools import projection as projection_tools

logger = logging.getLogger("fin_agent.jobs.risk")


def risk_warning(conn: psycopg.Connection, *, horizon_days: int = 90) -> dict[str, Any] | None:
    """Check the projection and create at most one warning for the current episode."""
    projection = projection_tools.project(conn, horizon_days=horizon_days)
    state = conn.execute("SELECT * FROM risk_state WHERE id = 1").fetchone()
    last_crossing = state["first_floor_crossing"] if state else None
    armed = bool(state["armed"]) if state else True

    probability = float(projection["floor_probability"])
    threshold = float(projection["floor_threshold"])
    crossing_text = projection["first_floor_crossing"]
    crossing = date.fromisoformat(crossing_text) if crossing_text else None
    floor = int(projection["floor_paise"])

    probability_low = floor > 0 and probability < threshold
    crossing_moved = crossing is not None and last_crossing is not None and crossing < last_crossing
    at_risk = probability_low or crossing_moved

    row = None
    if at_risk and armed:
        episode_key = new_id(conn, "rsk")
        parts = []
        if probability_low:
            parts.append(
                f"The chance of staying above your floor over the next horizon is "
                f"{round(probability * 100)}%, below the {round(threshold * 100)}% line."
            )
        if crossing_moved:
            parts.append(
                f"The projection now first crosses your floor on {crossing_text}, earlier than "
                f"{last_crossing.isoformat()} before."
            )
        parts.append(
            f"Floor {money(floor)['text'] if floor else 'unknown'}. "
            "This is a projection, not a prediction; the data-quality notes are in the app."
        )
        row = create_notification(
            conn,
            kind="risk_warning",
            title="Your projection is riskier",
            body=" ".join(parts),
            data={
                "floor_probability": probability,
                "floor_threshold": threshold,
                "floor_paise": floor,
                "first_floor_crossing": crossing_text,
                "previous_crossing": last_crossing.isoformat() if last_crossing else None,
                "source_ids": projection["source_ids"],
                "data_quality": projection["data_quality"].get("gaps", [])[:5],
            },
            dedupe_key=f"risk_warning:{episode_key}",
        )
        armed = False
    elif not at_risk:
        armed = True

    conn.execute(
        """
        UPDATE risk_state
        SET floor_probability = %s,
            floor_threshold = %s,
            first_floor_crossing = %s,
            armed = %s,
            episode_key = CASE WHEN %s THEN episode_key ELSE NULL END,
            last_checked_at = now(),
            updated_at = now()
        WHERE id = 1
        """,
        (
            probability,
            threshold,
            crossing,
            armed,
            at_risk,
        ),
    )
    if row is not None:
        conn.execute(
            "UPDATE risk_state SET episode_key = %s WHERE id = 1",
            (row["dedupe_key"],),
        )
    return row


def handle_risk_check(payload: dict[str, Any]) -> dict[str, Any]:
    with closing(connect()) as conn:
        row = None
        with conn.transaction():
            row = risk_warning(conn)
        if row is not None:
            send_push_for_kind(conn, "risk_warning")
            conn.commit()
        return {
            "warning_id": row["id"] if row else None,
            "reason": payload.get("reason", "nightly"),
        }


__all__ = ["handle_risk_check", "risk_warning"]
