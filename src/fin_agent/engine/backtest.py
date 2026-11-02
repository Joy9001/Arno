"""Backtest: run the projection as of past days and compare with what actually happened.

Two sources are supported. `db` uses the real ledger and only what a balance and spending history
knew on each month's first day. `synthetic` generates a deterministic 12-month Indian-style history
when the real 12-month backfill is absent, and says so in the report. The README records which one
produced the published numbers.
"""

from __future__ import annotations

import calendar
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg

from fin_agent.engine.money import money
from fin_agent.engine.projection import (
    DEFAULT_RUNS,
    DEFAULT_SEED,
    CardAccount,
    CashEvent,
    GoalPlan,
    LiquidAccount,
    ProjectionInput,
    SpendCategory,
    card_due_date,
    project,
)
from fin_agent.india.workdays import last_working_day

SYNTHETIC_END = date(2026, 11, 1)
SYNTHETIC_MONTHS = 12
SYNTHETIC_START_BALANCE = 20_000_000
SYNTHETIC_FLOOR = 5_000_000
SYNTHETIC_SEED = 777_311

# category, mean weekday paise, weekend multiplier, card share, reducible
SYNTHETIC_SPEND = (
    ("Food & Dining", 9_000, 1.35, 0.30, True),
    ("Groceries", 12_000, 1.15, 0.10, True),
    ("Transport", 4_000, 0.75, 0.20, True),
    ("Shopping", 5_000, 1.50, 0.60, True),
    ("Fuel", 2_500, 0.90, 0.20, False),
    ("Entertainment", 2_000, 1.45, 0.55, True),
)
SYNTHETIC_GOAL_TARGET = 12_000_000
SYNTHETIC_GOAL_CONTRIBUTION = 1_000_000


def _month_start(day: date, months: int = 0) -> date:
    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    return date(year, month, 1)


def _on_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _add_months(day: date, months: int) -> date:
    first = _month_start(day, months)
    return _on_day(first.year, first.month, day.day)


@dataclass(frozen=True, slots=True)
class SyntheticResult:
    start: date
    end: date
    balances: dict[date, int]
    card_outstanding: dict[date, int]
    goal_saved: dict[date, int]
    spend: dict[date, dict[str, int]]
    month_starts: tuple[date, ...]


def generate_synthetic_history(
    *,
    end: date = SYNTHETIC_END,
    months: int = SYNTHETIC_MONTHS,
    seed: int = SYNTHETIC_SEED,
) -> SyntheticResult:
    """A deterministic daily history with salary, rent, EMI, a card, a goal, and variable spend."""
    first_month = _month_start(end, -(months + 1))
    start = first_month - timedelta(days=120)
    finish = end + timedelta(days=15)
    rng = random.Random(seed)
    card = CardAccount(
        id="card_syn",
        name="Synthetic card",
        statement_day=5,
        due_day=25,
        outstanding_paise=0,
        credit_limit_paise=10_000_000,
    )
    balance = SYNTHETIC_START_BALANCE
    goal_saved = 0
    card_pending: dict[date, int] = {}
    balances: dict[date, int] = {}
    card_outstanding: dict[date, int] = {}
    goal_by_day: dict[date, int] = {}
    spend: dict[date, dict[str, int]] = {}

    month_starts: list[date] = []
    for index in range(months):
        month_starts.append(_month_start(first_month, index))

    def add_card(day: date, amount: int) -> None:
        due = card_due_date(card, day)
        card_pending[due] = card_pending.get(due, 0) + amount

    cursor = start
    while cursor <= finish:
        month_first = _month_start(cursor)
        # Salary on the last working day, and the fixed commitments.
        salary_day = last_working_day(month_first.year, month_first.month)
        if cursor == salary_day:
            balance += 15_000_000
        if cursor.day == 5:
            balance -= 2_800_000
        if cursor.day == 10:
            balance -= 3_150_000
        if cursor.day == 12:
            add_card(cursor, 64_900)
        if cursor.day == 3:
            balance -= 1_500_000
        if cursor.day == 7:
            balance -= SYNTHETIC_GOAL_CONTRIBUTION
            goal_saved += SYNTHETIC_GOAL_CONTRIBUTION

        # Card bills on their due date.
        bill = card_pending.pop(cursor, 0)
        if bill:
            balance -= bill

        daily: dict[str, int] = {}
        weekend = cursor.weekday() >= 5
        for name, mean, multiplier, _share, _reducible in SYNTHETIC_SPEND:
            expected = mean * (multiplier if weekend else 1.0)
            draw = max(0, int(rng.gauss(expected, expected * 0.35)))
            if draw == 0:
                continue
            daily[name] = draw
            share = next(item[3] for item in SYNTHETIC_SPEND if item[0] == name)
            card_part = int(round(draw * share))
            balance -= draw - card_part
            if card_part:
                add_card(cursor, card_part)
        spend[cursor] = daily
        balances[cursor] = balance
        card_outstanding[cursor] = sum(card_pending.values())
        goal_by_day[cursor] = goal_saved
        cursor += timedelta(days=1)

    return SyntheticResult(
        start=start,
        end=finish,
        balances=balances,
        card_outstanding=card_outstanding,
        goal_saved=goal_by_day,
        spend=spend,
        month_starts=tuple(month_starts),
    )


def _synthetic_events(start: date, end: date) -> tuple[list[CashEvent], CardAccount]:
    card = CardAccount(
        id="card_syn",
        name="Synthetic card",
        statement_day=5,
        due_day=25,
        outstanding_paise=0,
        credit_limit_paise=10_000_000,
    )
    events: list[CashEvent] = []
    cursor = _month_start(start)
    while cursor <= end:
        salary_day = last_working_day(cursor.year, cursor.month)
        rent = _on_day(cursor.year, cursor.month, 5)
        emi = _on_day(cursor.year, cursor.month, 10)
        subscription = _on_day(cursor.year, cursor.month, 12)
        family = _on_day(cursor.year, cursor.month, 3)
        goal = _on_day(cursor.year, cursor.month, 7)
        for when, amount, name, kind, card_id in (
            (salary_day, 15_000_000, "Synthetic salary", "income_fixed", None),
            (rent, -2_800_000, "Synthetic rent", "obligation", None),
            (emi, -3_150_000, "Synthetic home loan EMI", "obligation", None),
            (subscription, -64_900, "Synthetic subscription", "obligation", "card_syn"),
            (family, -1_500_000, "Synthetic family transfer", "obligation", None),
            (
                goal,
                -SYNTHETIC_GOAL_CONTRIBUTION,
                "Synthetic goal contribution",
                "goal_contribution",
                None,
            ),
        ):
            if start < when <= end:
                events.append(
                    CashEvent(
                        date=when,
                        amount_paise=amount,
                        name=name,
                        kind=kind,
                        source_id=f"syn:{kind}:{when.isoformat()}",
                        goal_id="goal_syn" if kind == "goal_contribution" else None,
                        card_id=card_id,
                    )
                )
        cursor = _add_months(cursor, 1)
    return events, card


def _synthetic_inputs(history: SyntheticResult, as_of: date) -> ProjectionInput:
    end = as_of + timedelta(days=90)
    events, card = _synthetic_events(as_of, end)
    card = CardAccount(
        id=card.id,
        name=card.name,
        statement_day=card.statement_day,
        due_day=card.due_day,
        outstanding_paise=history.card_outstanding.get(as_of, 0),
        credit_limit_paise=card.credit_limit_paise,
    )
    categories: list[SpendCategory] = []
    history_start = as_of - timedelta(days=90)
    for name, _mean, _multiplier, share, reducible in SYNTHETIC_SPEND:
        daily = [
            (
                history_start + timedelta(days=index),
                history.spend.get(history_start + timedelta(days=index), {}).get(name, 0),
            )
            for index in range(90)
        ]
        total = sum(amount for _day, amount in daily)
        categories.append(
            SpendCategory(
                id=f"cat_{name.lower().replace(' ', '_')}",
                name=name,
                baseline_per_day_paise=round(total / 90),
                baseline_source="computed",
                reducible=reducible,
                card_share=share,
                history=tuple(daily),
            )
        )
    return ProjectionInput(
        as_of=as_of,
        floor_paise=SYNTHETIC_FLOOR,
        liquid=(
            LiquidAccount(
                id="acc_syn",
                name="Synthetic salary account",
                balance_paise=history.balances.get(as_of, SYNTHETIC_START_BALANCE),
                as_of=as_of,
                statement_age_days=0,
            ),
        ),
        events=tuple(events),
        cards=(card,),
        goals=(
            GoalPlan(
                id="goal_syn",
                name="Synthetic trip",
                target_paise=SYNTHETIC_GOAL_TARGET,
                saved_paise=history.goal_saved.get(as_of, 0),
                deadline=_month_start(as_of) + timedelta(days=90),
            ),
        ),
        categories=tuple(categories),
        ask_horizon_days=90,
        data_quality={
            "balance_ages": [],
            "baseline_sources": [],
            "gaps": ["synthetic history"],
            "setup_gaps": [],
        },
    )


def _row(as_of: date, projected: dict[str, Any], actual: int) -> dict[str, Any]:
    wanted = (as_of + timedelta(days=30)).isoformat()
    day = next(point for point in projected["curve"] if point["date"] == wanted)
    median = int(day["median_paise"])
    p10 = int(day["p10_paise"])
    p90 = int(day["p90_paise"])
    return {
        "as_of": as_of.isoformat(),
        "actual_paise": actual,
        "median_paise": median,
        "p10_paise": p10,
        "p90_paise": p90,
        "error_paise": median - actual,
        "absolute_error_paise": abs(median - actual),
        "in_band": p10 <= actual <= p90,
        "floor_probability": projected["floor_probability"],
    }


def _summarize(rows: list[dict[str, Any]], source: str) -> dict[str, Any]:
    if not rows:
        return {
            "source": source,
            "months": 0,
            "median_error_paise": None,
            "median_absolute_error_paise": None,
            "band_calibration": None,
            "rows": [],
        }
    errors = sorted(row["error_paise"] for row in rows)
    absolute = sorted(row["absolute_error_paise"] for row in rows)
    middle = len(errors) // 2

    def median(values: list[int]) -> int:
        return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) // 2

    return {
        "source": source,
        "months": len(rows),
        "median_error_paise": median(errors),
        "median_absolute_error_paise": median(absolute),
        "band_calibration": round(sum(1 for row in rows if row["in_band"]) / len(rows), 4),
        "rows": rows,
    }


def backtest_synthetic(
    *, runs: int = DEFAULT_RUNS, seed: int = DEFAULT_SEED, end: date = SYNTHETIC_END
) -> dict[str, Any]:
    history = generate_synthetic_history(end=end)
    rows: list[dict[str, Any]] = []
    for as_of in history.month_starts:
        actual_day = as_of + timedelta(days=30)
        if actual_day not in history.balances:
            continue
        if (as_of - history.start).days < 90:
            continue
        inputs = _synthetic_inputs(history, as_of)
        projected = project(inputs, runs=runs, seed=seed)
        rows.append(_row(as_of, projected, history.balances[actual_day]))
    return _summarize(rows, "synthetic 12-month history")


def backtest_db(
    conn: psycopg.Connection, *, runs: int = DEFAULT_RUNS, seed: int = DEFAULT_SEED
) -> dict[str, Any]:
    from fin_agent.tools.projection import _balance_at
    from fin_agent.tools.projection import project as project_tool

    bounds = conn.execute(
        "SELECT min(date) AS first_day, max(date) AS last_day FROM movements"
    ).fetchone()
    if not bounds or bounds["first_day"] is None:
        return _summarize([], "real backfill")
    first = bounds["first_day"]
    last = bounds["last_day"]
    starts: list[date] = []
    cursor = _month_start(first)
    while cursor <= last - timedelta(days=30):
        if cursor >= first:
            starts.append(cursor)
        cursor = _add_months(cursor, 1)
    starts = starts[-12:]
    liquid = conn.execute(
        """
        SELECT id, ledger_account_id FROM accounts
        WHERE liquid AND active ORDER BY name
        """
    ).fetchall()
    rows: list[dict[str, Any]] = []
    for as_of in starts:
        try:
            projected = project_tool(conn, as_of=as_of, horizon_days=90, runs=runs, seed=seed)
        except ValueError:
            continue
        actual = sum(
            _balance_at(conn, row["ledger_account_id"], as_of + timedelta(days=30))
            for row in liquid
        )
        rows.append(_row(as_of, projected, actual))
    return _summarize(rows, "real backfill")


def backtest(
    conn: psycopg.Connection | None = None,
    *,
    source: str = "auto",
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    if source not in {"auto", "db", "synthetic"}:
        raise ValueError("source is auto, db, or synthetic")
    if source in {"auto", "db"} and conn is not None:
        months = conn.execute(
            """
            SELECT count(DISTINCT date_trunc('month', date)) AS n
            FROM movements
            """
        ).fetchone()
        enough = bool(months and int(months["n"]) >= 12)
        if source == "db" and not enough:
            raise ValueError(
                "the ledger does not have 12 months of movements; run the 12-month backfill first"
            )
        if enough:
            result = backtest_db(conn, runs=runs, seed=seed)
            if result["months"]:
                return result
    return backtest_synthetic(runs=runs, seed=seed)


def format_report(result: dict[str, Any]) -> str:
    if not result["months"]:
        return f"No completed 30-day windows were available for the {result['source']}."
    lines = [
        f"Source: {result['source']}",
        f"Months: {result['months']}",
        f"Median 30-day error: {money(result['median_error_paise'])['text']}",
        f"Median absolute 30-day error: {money(result['median_absolute_error_paise'])['text']}",
        f"Actual balance inside the P10-P90 band: {result['band_calibration']:.0%} of months",
        "",
        "| Month start | Actual | Median | P10 | P90 | In band |",
        "|---|---|---|---|---|---|",
    ]
    for row in result["rows"]:
        lines.append(
            f"| {row['as_of']} | {money(row['actual_paise'])['text']} | "
            f"{money(row['median_paise'])['text']} | {money(row['p10_paise'])['text']} | "
            f"{money(row['p90_paise'])['text']} | {'yes' if row['in_band'] else 'no'} |"
        )
    return "\n".join(lines)


def publish_report(result: dict[str, Any], readme: Path) -> None:
    start = "<!-- backtest:start -->"
    end = "<!-- backtest:end -->"
    body = f"{start}\n{format_report(result)}\n{end}"
    content = readme.read_text(encoding="utf-8")
    if start in content and end in content:
        before, rest = content.split(start, 1)
        _old, after = rest.split(end, 1)
        readme.write_text(f"{before}{body}{after}", encoding="utf-8")
    else:
        readme.write_text(
            f"{content.rstrip()}\n\n## Projection backtest\n\n{body}\n", encoding="utf-8"
        )


__all__ = [
    "SYNTHETIC_END",
    "backtest",
    "backtest_db",
    "backtest_synthetic",
    "format_report",
    "generate_synthetic_history",
]
