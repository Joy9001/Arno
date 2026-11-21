"""Database loaders and persistence for recurring series detection and missed charges.

Spend and income payments over the last 800 days are grouped by payee and passed to the pure
Bayesian detector in ``arno.engine.series``. Detections replace ``series_detections`` in one
transaction; high-confidence uncovered spend detections become ``new_subscription`` proposals,
high-confidence income detections become ``new_income`` proposals, and a spend charge that steps
to a new amount level is proposed as a ``price_change``, once per step.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal, cast

from arno.db.conn import Connection
from arno.engine import series
from arno.engine.patterns import (
    HISTORY_DAYS,
    MISSED_AFTER_DAYS,
    KnownSchedule,
    family_interval_days,
    is_covered,
    rule_interval_days,
)
from arno.india.workdays import calendar_for
from arno.ledger import schedules as schedules_mod
from arno.ledger.common import today as ledger_today
from arno.ledger.schedule_payments import payment_evidence_for_schedules

DetectionKind = Literal["spend", "income"]
_NEXT_DATE_LOOKAHEAD_DAYS = 400
# How far back an alert may look: older missed occurrences and price steps are history, not news.
_ALERT_WINDOW_DAYS = 90


@dataclass(frozen=True, slots=True)
class PaymentRow:
    movement_id: str
    payee_id: str
    merchant: str
    normalized: str
    outside_text: bool
    date: date
    amount_paise: int
    card_id: str | None = None
    category_id: str | None = None


async def _load_spend_payments(conn: Connection, as_of: date) -> list[PaymentRow]:
    start = as_of - timedelta(days=HISTORY_DAYS)
    rows = await conn.fetch(
        """
        SELECT v.movement_id, v.date, v.payee_id, p.display_name, p.outside_text,
               min(v.category_id) AS category_id,
               max(CASE WHEN fa.kind = 'card' THEN fa.id
                        WHEN ta.kind = 'card' THEN ta.id END) AS card_id,
               sum(v.amount_paise)::bigint AS amount_paise
        FROM v_spending v
        JOIN movements m ON m.id = v.movement_id
        JOIN payees p ON p.id = v.payee_id
        JOIN v_movement_state ms ON ms.movement_id = v.movement_id
        JOIN pgledger_transfers t ON t.id = v.transfer_id
        JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
        JOIN accounts ta ON ta.ledger_account_id = t.to_account_id
        WHERE v.amount_paise > 0
          AND v.date BETWEEN $1 AND $2
          AND p.active
          AND ms.is_effective
          AND NOT m.one_off
        GROUP BY v.movement_id, v.date, v.payee_id, p.display_name, p.outside_text
        ORDER BY v.date, v.movement_id
        """,
        start,
        as_of,
    )
    return [
        PaymentRow(
            movement_id=str(row["movement_id"]),
            payee_id=str(row["payee_id"]),
            merchant=str(row["display_name"]),
            normalized=series.normalize(str(row["display_name"])),
            outside_text=bool(row["outside_text"]),
            date=cast(date, row["date"]),
            amount_paise=int(row["amount_paise"]),
            card_id=str(row["card_id"]) if row["card_id"] else None,
            category_id=str(row["category_id"]) if row["category_id"] else None,
        )
        for row in rows
    ]


async def _load_income_payments(conn: Connection, as_of: date) -> list[PaymentRow]:
    start = as_of - timedelta(days=HISTORY_DAYS)
    rows = await conn.fetch(
        """
        SELECT m.id AS movement_id, m.date, ml.payee_id, p.display_name, p.outside_text,
               min(ml.category_id) AS category_id,
               NULL::text AS card_id,
               sum(t.amount)::bigint AS amount_paise
        FROM movement_legs ml
        JOIN pgledger_transfers t ON t.id = ml.transfer_id
        JOIN movements m ON m.id = ml.movement_id
        JOIN payees p ON p.id = ml.payee_id
        JOIN v_movement_state ms ON ms.movement_id = m.id
        JOIN accounts inc ON inc.ledger_account_id = t.from_account_id AND inc.kind = 'income'
        JOIN accounts dest ON dest.ledger_account_id = t.to_account_id AND dest.liquid
        WHERE t.amount > 0
          AND m.date BETWEEN $1 AND $2
          AND p.active
          AND ms.is_effective
          AND NOT m.one_off
        GROUP BY m.id, m.date, ml.payee_id, p.display_name, p.outside_text
        ORDER BY m.date, m.id
        """,
        start,
        as_of,
    )
    return [
        PaymentRow(
            movement_id=str(row["movement_id"]),
            payee_id=str(row["payee_id"]),
            merchant=str(row["display_name"]),
            normalized=series.normalize(str(row["display_name"])),
            outside_text=bool(row["outside_text"]),
            date=cast(date, row["date"]),
            amount_paise=int(row["amount_paise"]),
            card_id=None,
            category_id=str(row["category_id"]) if row["category_id"] else None,
        )
        for row in rows
    ]


async def _load_confirmed_schedules(conn: Connection) -> list[KnownSchedule]:
    rows = await conn.fetch(
        "SELECT id, name, amount_paise, category_id, rule, confirmed "
        "FROM schedules WHERE active AND confirmed ORDER BY name"
    )
    return [
        KnownSchedule(
            id=str(row["id"]),
            name=str(row["name"]),
            normalized=series.normalize(str(row["name"])),
            amount_paise=int(row["amount_paise"]) if row["amount_paise"] else None,
            category_id=str(row["category_id"]) if row["category_id"] else None,
            interval_days=rule_interval_days(row["rule"]),
            confirmed=bool(row["confirmed"]),
        )
        for row in rows
    ]


async def _load_confirmed_schedule_movements(conn: Connection) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT sp.movement_id
        FROM schedule_payments sp
        JOIN schedule_occurrences o ON o.id = sp.occurrence_id
        JOIN schedules s ON s.id = o.schedule_id
        WHERE s.active AND s.confirmed
        """
    )
    return {str(row["movement_id"]) for row in rows}


def _most_common_card(payments: Sequence[PaymentRow]) -> str | None:
    if not payments:
        return None
    counts = Counter(p.card_id for p in payments)
    best_card, _ = max(
        counts.items(),
        key=lambda item: (item[1], 1 if item[0] is None else 0, item[0] or ""),
    )
    return best_card


def _most_common_category(payments: Sequence[PaymentRow]) -> str | None:
    cats = [p.category_id for p in payments if p.category_id is not None]
    if not cats:
        return None
    counts = Counter(cats)
    best_cat, _ = min(counts.items(), key=lambda item: (-item[1], item[0]))
    return best_cat


def _series_price_change(matched: Sequence[PaymentRow], step: series.PriceStep) -> dict[str, Any]:
    """A proposal for a detected spend series that stepped to a new level."""
    new_run = matched[step.index :]
    return {
        "kind": "price_change",
        "payee_id": new_run[0].payee_id,
        "merchant": matched[-1].merchant,
        "outside_text": any(item.outside_text for item in matched),
        "old_paise": step.old_paise,
        "new_paise": step.new_paise,
        "step_date": new_run[0].date.isoformat(),
        "movement_ids": [item.movement_id for item in new_run],
    }


def _counted_payments(
    evidence: dict[date, dict[str, Any]], *, stated_paise: int, as_of: date
) -> list[tuple[date, int, dict[str, Any]]]:
    """Paid occurrences that are evidence of a price, not a partial payment still in progress."""
    counted: list[tuple[date, int, dict[str, Any]]] = []
    for due_date in sorted(evidence):
        item = evidence[due_date]
        paid_paise = int(item["paid_paise"])
        if paid_paise <= 0:
            continue
        # A payment below the stated amount may still be topped up until the missed-charge grace.
        if paid_paise <= stated_paise and (as_of - due_date).days < MISSED_AFTER_DAYS:
            continue
        counted.append((due_date, paid_paise, item))
    return counted


async def _schedule_price_changes(conn: Connection, *, as_of: date) -> list[dict[str, Any]]:
    """Paid occurrences that stepped away from a confirmed outflow schedule's stated amount."""
    rows = await conn.fetch(
        """
        SELECT id, name, amount_paise, outside_text FROM schedules
        WHERE active AND confirmed AND amount_paise IS NOT NULL AND amount_paise > 0
          AND kind IN ('obligation', 'instalment')
        ORDER BY name
        """
    )
    evidence = await payment_evidence_for_schedules(
        conn,
        [str(row["id"]) for row in rows],
        as_of - timedelta(days=HISTORY_DAYS),
        as_of,
        as_of=as_of,
    )
    proposals: list[dict[str, Any]] = []
    for row in rows:
        stated_paise = int(row["amount_paise"])
        counted = _counted_payments(
            evidence[str(row["id"])], stated_paise=stated_paise, as_of=as_of
        )
        for length in range(len(counted), 0, -1):
            run = counted[-length:]
            level = series.stepped_level([paid for _, paid, _ in run], from_paise=stated_paise)
            if level is None:
                continue
            payments = [
                payment for _, _, item in run for payment in item["payments"] if payment["active"]
            ]
            payments.sort(key=lambda payment: (payment["date"], payment["movement_id"]))
            movement_ids = list(dict.fromkeys(str(payment["movement_id"]) for payment in payments))
            proposals.append(
                {
                    "kind": "price_change",
                    "schedule_id": str(row["id"]),
                    "merchant": str(row["name"]),
                    "outside_text": bool(row["outside_text"]),
                    "old_paise": stated_paise,
                    "new_paise": level,
                    "step_date": str(payments[0]["date"]),
                    "movement_ids": movement_ids,
                }
            )
            break
    return proposals


async def detect_missed(conn: Connection, *, as_of: date | None = None) -> list[dict[str, Any]]:
    """Expected obligation occurrences three or more days old with no payment evidence."""
    day = as_of or ledger_today()
    start = day - timedelta(days=_ALERT_WINDOW_DAYS)
    end = day - timedelta(days=MISSED_AFTER_DAYS)
    proposals: list[dict[str, Any]] = []
    rows = await conn.fetch(
        """
        SELECT * FROM schedules
        WHERE active AND kind IN ('obligation', 'instalment') AND amount_paise IS NOT NULL
        ORDER BY name
        """
    )
    starts_by_id = {str(row["id"]): max(start, row["starts"]) for row in rows}
    expanded_by_id = await schedules_mod.expand_schedules(
        conn, rows, start, end, as_of=day, starts_by_id=starts_by_id
    )
    for row in rows:
        occurrences = expanded_by_id[str(row["id"])]
        for occurrence in occurrences:
            amount = occurrence["amount_paise"]
            if not amount or amount <= 0 or occurrence["paid_paise"]:
                continue
            expected = date.fromisoformat(occurrence["date"])
            proposals.append(
                {
                    "kind": "missed_recurring",
                    "schedule_id": str(row["id"]),
                    "merchant": str(occurrence["name"]),
                    "outside_text": bool(occurrence["outside_text"]),
                    "amount_paise": int(amount),
                    "expected_date": expected.isoformat(),
                    "days_late": (day - expected).days,
                    "subtype": occurrence["subtype"],
                }
            )
    return proposals


async def detect(
    conn: Connection,
    *,
    as_of: date | None = None,
    write: bool = True,
) -> list[dict[str, Any]]:
    """Run series detection over spend and income payments, store detections, and return proposals.

    Replaces all rows of ``series_detections`` in one transaction when ``write`` is True.
    """
    day = as_of or ledger_today()
    window_start = day - timedelta(days=HISTORY_DAYS)
    cal = calendar_for(
        window_start - timedelta(days=21),
        day + timedelta(days=_NEXT_DATE_LOOKAHEAD_DAYS + 21),
    )

    spend_rows = await _load_spend_payments(conn, day)
    income_rows = await _load_income_payments(conn, day)
    schedules = await _load_confirmed_schedules(conn)
    linked_movements = await _load_confirmed_schedule_movements(conn)

    to_store: list[tuple[Any, ...]] = []
    proposals: list[dict[str, Any]] = []

    for kind, rows in (("spend", spend_rows), ("income", income_rows)):
        grouped: dict[str, list[PaymentRow]] = {}
        for row in rows:
            grouped.setdefault(row.payee_id, []).append(row)

        # shortcut: one detected series per payee and raw token overlap in same_merchant, revisit
        # when a real payee holds two plans or false-matches generic words
        for payee_id in sorted(grouped):
            group = sorted(grouped[payee_id], key=lambda item: (item.date, item.movement_id))
            payments = [
                series.Payment(
                    movement_id=item.movement_id,
                    date=item.date,
                    amount_paise=item.amount_paise,
                )
                for item in group
            ]
            detection = series.detect(
                payments,
                as_of=day,
                window_start=window_start,
                calendar=cal,
            )
            if detection is None:
                continue

            matched_set = set(detection.matched)
            matched_payments = [item for item in group if item.movement_id in matched_set]
            card_id = _most_common_card(matched_payments) if kind == "spend" else None
            category_id = _most_common_category(matched_payments) if kind == "spend" else None

            to_store.append(
                (
                    payee_id,
                    kind,
                    detection.rule.family,
                    detection.rule.rrule,
                    detection.rule.starts,
                    detection.rule.holiday_adjust,
                    detection.rule.description,
                    detection.confidence,
                    detection.alive,
                    detection.skip,
                    json.dumps([[offset, prob] for offset, prob in detection.jitter]),
                    detection.amounts.kind,
                    detection.amounts.level_paise,
                    detection.amounts.low_paise,
                    detection.amounts.high_paise,
                    list(detection.amounts.samples),
                    list(detection.matched),
                    card_id,
                    category_id,
                    day,
                )
            )

            if (
                detection.confidence < series.PROPOSE_CONFIDENCE
                or detection.alive < series.PROPOSE_ALIVE
                or not matched_payments
            ):
                continue

            latest = matched_payments[-1]
            interval_days = family_interval_days(detection.rule.family)
            covered = False
            if kind == "spend":
                categories = {item.category_id for item in matched_payments if item.category_id}
                linked = any(item.movement_id in linked_movements for item in matched_payments)
                covered = linked or is_covered(
                    normalized=latest.normalized,
                    categories=categories,
                    amount_paise=detection.amounts.level_paise,
                    interval_days=interval_days,
                    schedules=schedules,
                )
                step = series.price_step([item.amount_paise for item in matched_payments])
                # A schedule that already covers the new level needs nothing, and one that covers
                # the old level or linked payments owns the change through
                # `_schedule_price_changes`, so the same charge is never reported twice.
                if step is not None and not covered:
                    covered = is_covered(
                        normalized=latest.normalized,
                        categories=categories,
                        amount_paise=step.old_paise,
                        interval_days=interval_days,
                        schedules=schedules,
                    )
                    if not covered:
                        proposals.append(_series_price_change(matched_payments, step))
            if covered:
                continue

            upcoming_dates = series.upcoming(
                detection,
                after=day,
                until=day + timedelta(days=_NEXT_DATE_LOOKAHEAD_DAYS),
                calendar=cal,
            )
            next_date = upcoming_dates[0].isoformat() if upcoming_dates else None
            proposals.append(
                {
                    "kind": "new_subscription" if kind == "spend" else "new_income",
                    "merchant": latest.merchant,
                    "normalized": latest.normalized,
                    "payee_id": payee_id,
                    "outside_text": any(item.outside_text for item in matched_payments),
                    "amount_paise": detection.amounts.level_paise,
                    "interval_days": interval_days if interval_days is not None else 30,
                    "occurrences": len(matched_payments),
                    "first_date": matched_payments[0].date.isoformat(),
                    "last_date": latest.date.isoformat(),
                    "confidence": detection.confidence,
                    "rule": {
                        "family": detection.rule.family,
                        "rrule": detection.rule.rrule,
                        "description": detection.rule.description,
                    },
                    "next_date": next_date,
                }
            )

    if write:
        async with conn.transaction():
            await conn.execute("DELETE FROM series_detections")
            if to_store:
                await conn.executemany(
                    """
                    INSERT INTO series_detections (
                        payee_id, kind, family, rrule, starts, holiday_adjust, description,
                        confidence, alive, skip, jitter, amount_kind, level_paise, low_paise,
                        high_paise, samples, matched_movement_ids, card_id, category_id, as_of
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7,
                        $8, $9, $10, $11::jsonb, $12, $13, $14,
                        $15, $16::bigint[], $17::text[], $18, $19, $20
                    )
                    """,
                    to_store,
                )

    proposals.extend(await _schedule_price_changes(conn, as_of=day))
    proposals.extend(await detect_missed(conn, as_of=day))
    alert_start = day - timedelta(days=_ALERT_WINDOW_DAYS)
    proposals = [
        proposal
        for proposal in proposals
        if proposal["kind"] != "price_change"
        or date.fromisoformat(proposal["step_date"]) >= alert_start
    ]
    proposals.sort(key=lambda item: (item["kind"], item["merchant"]))
    return proposals


__all__ = [
    "PaymentRow",
    "_load_income_payments",
    "_load_spend_payments",
    "detect",
    "detect_missed",
]
