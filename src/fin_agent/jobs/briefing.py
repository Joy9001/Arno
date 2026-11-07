"""The weekly briefing: ledger queries and the engine compute every number; one CHAT call may
write the prose, and a plain template goes out with the same numbers if that call fails.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from fin_agent.agent.models import primary_model
from fin_agent.db.conn import Connection, connect
from fin_agent.engine.money import money
from fin_agent.india.formatting import format_paise
from fin_agent.ledger import accounts as accounts_mod
from fin_agent.ledger import goals as goals_mod
from fin_agent.ledger import schedules as schedules_mod
from fin_agent.ledger.common import today as ledger_today
from fin_agent.notifications import create_notification, send_push_for_kind
from fin_agent.tools import projection as projection_tools
from fin_agent.tools import read as read_tools

logger = logging.getLogger("fin_agent.jobs.briefing")

BRIEFING_INSTRUCTIONS = """You write a short weekly money briefing for one person in India.
Use only the numbers in the data. Do not calculate, round, convert, or invent a number, and do not
give advice or recommend a product. Two short paragraphs, plain English, no headings, no markdown
tables. Say what changed and what is due. If a number is null, say it is unknown, never zero."""


def _sum_paise(value: Any) -> int | None:
    if not value:
        return None
    return int(value["paise"])


def _percent_change(current: int | None, previous: int | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


async def briefing_numbers(conn: Connection, *, as_of: date | None = None) -> dict[str, Any]:
    """Every number in the brief, computed here from the ledger and the engine."""
    day = as_of or ledger_today()
    week_start = day - timedelta(days=6)
    before_start = day - timedelta(days=13)
    before_end = day - timedelta(days=7)

    this_week = await read_tools.summarize_spending(
        conn, group_by="none", period=f"{week_start.isoformat()}..{day.isoformat()}"
    )
    last_week = await read_tools.summarize_spending(
        conn, group_by="none", period=f"{before_start.isoformat()}..{before_end.isoformat()}"
    )
    categories = await read_tools.summarize_spending(
        conn, group_by="category", period=f"{week_start.isoformat()}..{day.isoformat()}"
    )
    current_paise = _sum_paise(this_week.get("total"))
    previous_paise = _sum_paise(last_week.get("total"))

    summary = await accounts_mod.account_summary(conn)
    liquid_paise = (
        int(summary["liquid_total_paise"]) if summary["cash_quality"]["status"] == "known" else None
    )
    card_owed_paise = summary["card_owed_paise"]

    upcoming_rows = (await schedules_mod.upcoming(conn, days=7, start=day))["events"]
    bills = [
        event
        for event in upcoming_rows
        if event["kind"] in {"obligation", "instalment", "card_due"}
        and event["amount_paise"] is not None
    ]
    bills_total = sum(int(event["amount_paise"]) for event in bills)

    projection = await projection_tools.project(conn, horizon_days=90, as_of=day)

    goal_rows = [row for row in await goals_mod.list_goals(conn) if row["status"] == "active"]
    top_goals = sorted(goal_rows, key=lambda row: row["remaining_paise"], reverse=True)[:3]

    top_categories = []
    for group in categories.get("groups", [])[:5]:
        amount = int(group["amount"]["paise"]) if group.get("amount") else 0
        if amount <= 0:
            continue
        top_categories.append(
            {
                "name": group["key"],
                "amount_paise": amount,
                "amount": group["amount"],
                "count": group["count"],
            }
        )

    return {
        "as_of": day.isoformat(),
        "week": {"from": week_start.isoformat(), "to": day.isoformat()},
        "spent": {
            "paise": current_paise,
            "money": money(current_paise),
            "entries": int(this_week.get("groups", [{}])[0]["count"])
            if this_week.get("groups")
            else 0,
            "previous_paise": previous_paise,
            "previous_money": money(previous_paise),
            "change_percent": _percent_change(current_paise, previous_paise),
        },
        "top_categories": top_categories,
        "liquid": {
            "paise": liquid_paise,
            "money": money(liquid_paise),
            "card_owed_paise": card_owed_paise,
            "card_owed": money(card_owed_paise),
        },
        "bills": {
            "count": len(bills),
            "total_paise": bills_total,
            "total": money(bills_total),
            "items": [
                {
                    "date": event["date"],
                    "name": event["name"],
                    "amount_paise": event["amount_paise"],
                    "amount": money(int(event["amount_paise"])),
                    "source_id": event["schedule_id"] or event["account_id"],
                }
                for event in bills[:8]
            ],
        },
        "floor": {
            "paise": int(projection["floor_paise"]),
            "money": money(int(projection["floor_paise"])),
            "probability": projection["floor_probability"],
            "threshold": projection["floor_threshold"],
            "first_crossing": projection["first_floor_crossing"],
            "median_low": projection["median_low"],
            "data_quality": projection["data_quality"].get("gaps", [])[:3],
        },
        "goals": [
            {
                "name": row["name"],
                "remaining_paise": int(row["remaining_paise"]),
                "remaining": money(int(row["remaining_paise"])),
                "deadline": (
                    row["deadline"].isoformat()
                    if hasattr(row["deadline"], "isoformat")
                    else row["deadline"]
                ),
            }
            for row in top_goals
        ],
        "source_ids": projection["source_ids"],
    }


def _text(paise: int | None) -> str:
    return format_paise(paise) if paise is not None else "unknown"


def render_template(numbers: dict[str, Any]) -> str:
    """The fallback brief: plain prose with exactly the numbers computed above."""
    spent = numbers["spent"]
    current = spent["money"]["text"] if spent["money"] else "not recorded"
    previous = spent["previous_money"]["text"] if spent["previous_money"] else "not recorded"
    change = spent["change_percent"]
    if change is None:
        comparison = f"Last week: {previous}."
    else:
        direction = "up" if change >= 0 else "down"
        comparison = f"That is {direction} {abs(change)}% from {previous} last week."
    categories = (
        ", ".join(f"{row['name']} {row['amount']['text']}" for row in numbers["top_categories"])
        or "none recorded"
    )
    bills = numbers["bills"]
    bill_text = (
        f"{bills['count']} scheduled bill(s), {bills['total']['text']} in all"
        if bills["count"]
        else "no scheduled bills"
    )
    floor = numbers["floor"]
    if floor["first_crossing"]:
        floor_text = (
            f"The projection's median path crosses your floor on {floor['first_crossing']}."
        )
    else:
        floor_text = (
            f"The projection's 90-day chance of staying above your floor is "
            f"{round(floor['probability'] * 100)}%."
        )
    goals = (
        "; ".join(
            f"{row['name']} has {row['remaining']['text']} left"
            + (f" by {row['deadline']}" if row["deadline"] else "")
            for row in numbers["goals"]
        )
        or "no active goals"
    )
    quality = (
        "Data note: " + "; ".join(floor["data_quality"])
        if floor["data_quality"]
        else "No data-quality gaps this week."
    )
    return (
        f"Your week ({numbers['week']['from']} to {numbers['week']['to']}): "
        f"you spent {current} across {spent['entries']} entries. {comparison}\n\n"
        f"Top categories: {categories}. Liquid balance: "
        f"{numbers['liquid']['money']['text'] if numbers['liquid']['money'] else 'unknown'}. "
        f"Next 7 days: {bill_text}.\n\n"
        f"{floor_text} Goals: {goals}. {quality}"
    )


async def write_prose(
    numbers: dict[str, Any], *, model: Any = None, spend_store: Any = None
) -> str:
    """One CHAT call, and only one. The caller falls back to the template on any failure."""
    # TODO(L6): exercised with scripted models and the fallback; rerun against the configured
    # CHAT endpoint so a real provider's prose and timeout behavior are measured.
    from pydantic_ai import Agent, UsageLimits

    from fin_agent.agent.capabilities import RUN_MODEL_SETTINGS, spend_limits
    from fin_agent.agent.spend import spend_store_scope

    async with spend_store_scope(spend_store) as store:
        limits = spend_limits(store)
        agent = Agent(
            model or primary_model(),
            output_type=str,
            instructions=BRIEFING_INSTRUCTIONS,
            capabilities=[limits] if limits is not None else None,
            name="weekly-briefing",
        )
        result = await agent.run(
            "Write the brief from this JSON. Use only these numbers.\n"
            + json.dumps(numbers, ensure_ascii=False, default=str),
            model_settings=RUN_MODEL_SETTINGS,
            usage_limits=UsageLimits(request_limit=1),
        )
        text = str(result.output or "").strip()
        if not text:
            raise ValueError("the briefing model returned no prose")
        return text


async def compose_briefing(conn: Connection, *, as_of: date | None = None) -> dict[str, Any]:
    numbers = await briefing_numbers(conn, as_of=as_of)
    return {"numbers": numbers, "template": render_template(numbers)}


async def send_weekly_briefing(
    conn: Connection,
    *,
    as_of: date | None = None,
    period: str | None = None,
    model: Any = None,
    spend_store: Any = None,
) -> dict[str, Any] | None:
    numbers = await briefing_numbers(conn, as_of=as_of)
    template = render_template(numbers)
    body = template
    prose_error = None
    try:
        body = await write_prose(numbers, model=model, spend_store=spend_store)
    except Exception as exc:  # noqa: BLE001 - the numbers still go out
        prose_error = type(exc).__name__
        logger.info(
            "weekly briefing prose failed (%s); using the template", type(prose_error).__name__
        )
        await conn.execute(
            """
            UPDATE settings
            SET last_model_error_type = $1, last_model_error_at = now(), updated_at = now()
            WHERE id = 1
            """,
            prose_error[:200],
        )
    day = as_of or ledger_today()
    period = period or day.strftime("%G-W%V")
    row = await create_notification(
        conn,
        kind="weekly_briefing",
        title="Weekly briefing",
        body=body,
        data={
            "period": period,
            "numbers": numbers,
            "template": template,
            "prose": "model" if prose_error is None else "template",
            "prose_error": prose_error,
        },
        dedupe_key=f"weekly_briefing:{period}",
    )
    return row


async def handle_weekly_briefing(payload: dict[str, Any]) -> dict[str, Any]:
    async with connect() as conn:
        row = await send_weekly_briefing(
            conn,
            period=payload.get("period"),
        )
        if row is not None:
            await send_push_for_kind(conn, "weekly_briefing")
        return {"notification_id": row["id"] if row else None}


__all__ = [
    "BRIEFING_INSTRUCTIONS",
    "briefing_numbers",
    "compose_briefing",
    "handle_weekly_briefing",
    "render_template",
    "send_weekly_briefing",
    "write_prose",
]
