"""From a ledger snapshot to the projection's inputs, with no database.

`arno.tools.projection` reads the ledger into a `LedgerSnapshot`; the backtest builds the same
snapshot from a synthetic household. Both go through `build_inputs`, so the backtest measures the
production input path, not a copy of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from arno.engine.money import money
from arno.engine.projection import (
    HISTORY_DAYS,
    MIN_REAL_DAYS,
    NO_CONFIRMED_INCOME,
    NO_LIQUID_ACCOUNT,
    BalanceAge,
    BaselineSource,
    CardAccount,
    CashEvent,
    GoalPlan,
    LiquidAccount,
    ProjectionInput,
    SourceRef,
    SpendCategory,
)


@dataclass(frozen=True, slots=True)
class Occurrence:
    """One expanded schedule occurrence inside the horizon. Names are already display-safe."""

    schedule_id: str
    name: str
    kind: str
    date: date
    amount_paise: int | None
    remaining_paise: int | None
    category_id: str | None = None
    card_id: str | None = None
    goal_id: str | None = None
    confirmed: bool = True
    linked_goal: bool = True


@dataclass(frozen=True, slots=True)
class ConfiguredSchedule:
    """A schedule as configured, whether or not it falls inside this horizon."""

    kind: str
    confirmed: bool
    ends: date | None


@dataclass(frozen=True, slots=True)
class SpendRow:
    """Spend on one day in one category from one account; ``card_id`` is None for cash."""

    date: date
    category_id: str | None
    card_id: str | None
    amount_paise: int


@dataclass(frozen=True, slots=True)
class CategoryRow:
    id: str
    name: str
    reducible: bool
    baseline_per_day_paise: int | None = None
    baseline_source: str | None = None


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    """Everything the projection reads from the ledger as of one day."""

    as_of: date
    floor_paise: int
    liquid: tuple[LiquidAccount, ...] = ()
    cards: tuple[CardAccount, ...] = ()
    goals: tuple[GoalPlan, ...] = ()
    occurrences: tuple[Occurrence, ...] = ()
    schedules: tuple[ConfiguredSchedule, ...] = ()
    owed: tuple[CashEvent, ...] = ()
    categories: tuple[CategoryRow, ...] = ()
    # Positive spend in the HISTORY_DAYS days before as_of; as_of itself is not history yet.
    spend: tuple[SpendRow, ...] = ()
    ask_horizon_days: int | None = None
    gaps: tuple[str, ...] = ()
    balance_ages: tuple[BalanceAge, ...] = ()
    schedule_sources: tuple[SourceRef, ...] = ()
    owed_sources: tuple[SourceRef, ...] = ()
    outside_text: bool = False


def _events(snapshot: LedgerSnapshot) -> tuple[list[CashEvent], set[str], list[str]]:
    events: list[CashEvent] = []
    covered: set[str] = set()
    gaps: list[str] = []
    for occurrence in snapshot.occurrences:
        if (
            occurrence.kind in {"obligation", "instalment"}
            and occurrence.amount_paise is not None
            and occurrence.category_id
        ):
            covered.add(occurrence.category_id)
        remaining = occurrence.remaining_paise
        if remaining is None or remaining <= 0:
            continue
        common = {
            "date": occurrence.date,
            "name": occurrence.name,
            "kind": occurrence.kind,
            "source_id": occurrence.schedule_id,
        }
        if occurrence.kind.startswith("income"):
            events.append(
                CashEvent(**common, amount_paise=remaining, confirmed=occurrence.confirmed)
            )
        elif occurrence.kind in {"obligation", "instalment"}:
            events.append(CashEvent(**common, amount_paise=-remaining, card_id=occurrence.card_id))
        elif occurrence.kind == "goal_contribution":
            events.append(CashEvent(**common, amount_paise=-remaining, goal_id=occurrence.goal_id))
            if not occurrence.linked_goal:
                gaps.append(
                    f"{occurrence.name} is not linked to a goal; it still leaves liquid cash"
                )
    return events, covered, gaps


def _spend_categories(
    snapshot: LedgerSnapshot, covered: set[str]
) -> tuple[list[SpendCategory], list[BaselineSource], list[str]]:
    window_start = snapshot.as_of - timedelta(days=HISTORY_DAYS)
    daily: dict[str, dict[date, int]] = {}
    by_card: dict[str, dict[str, int]] = {}
    uncategorized = 0
    for row in snapshot.spend:
        if row.amount_paise <= 0:
            continue
        if row.category_id is None:
            uncategorized += row.amount_paise
            continue
        days = daily.setdefault(row.category_id, {})
        days[row.date] = days.get(row.date, 0) + row.amount_paise
        if row.card_id is not None:
            cards = by_card.setdefault(row.category_id, {})
            cards[row.card_id] = cards.get(row.card_id, 0) + row.amount_paise

    gaps: list[str] = []
    if uncategorized > 0:
        gaps.append(
            "uncategorized spending is not simulated as variable spend; label those rows in the "
            "ledger"
        )
    result: list[SpendCategory] = []
    baseline_rows: list[BaselineSource] = []
    days = [window_start + timedelta(days=offset) for offset in range(HISTORY_DAYS)]
    for category in snapshot.categories:
        if category.id in covered:
            gaps.append(
                f"{category.name} is covered by an obligation and is not simulated as variable "
                "spend"
            )
            continue
        by_day = daily.get(category.id, {})
        real_days = sum(1 for amount in by_day.values() if amount > 0)
        day_label = f"{real_days} real spending day" + ("" if real_days == 1 else "s")
        if real_days < MIN_REAL_DAYS and category.baseline_per_day_paise is None:
            if by_day:
                gaps.append(
                    f"{category.name} has only {day_label} and no setup estimate, so it is left out"
                )
            continue
        if category.baseline_per_day_paise is not None:
            baseline_per_day = category.baseline_per_day_paise
            source = category.baseline_source or "estimated"
        else:
            baseline_per_day = round(sum(by_day.values()) / HISTORY_DAYS) if by_day else 0
            source = "computed"
        history = tuple((day, max(0, by_day.get(day, 0))) for day in days)
        total_spend = sum(by_day.values())
        cards = by_card.get(category.id, {})
        # The card with the largest share; ties keep the first card met, as the loader always has.
        chosen: tuple[str, int] | None = None
        for card_id, amount in cards.items():
            if chosen is None or amount > chosen[1]:
                chosen = (card_id, amount)
        share = round(chosen[1] / total_spend, 4) if chosen is not None and total_spend > 0 else 0.0
        card_id = chosen[0] if chosen is not None and share > 0 else None
        if len(cards) > 1:
            gaps.append(
                f"{category.name} was charged to more than one card; its variable spend uses the "
                "card with the largest share"
            )
        if real_days < MIN_REAL_DAYS:
            gaps.append(
                f"{category.name} has only {day_label}; its "
                f"{money(baseline_per_day)['text']}/day {source} estimate is used flat"
            )
        elif share == 0:
            gaps.append(
                f"{category.name} has no card history; its variable spend is treated as cash"
            )
        result.append(
            SpendCategory(
                id=category.id,
                name=category.name,
                baseline_per_day_paise=baseline_per_day,
                baseline_source=source,
                reducible=category.reducible,
                card_share=share,
                card_id=card_id,
                history=history,
            )
        )
        baseline_rows.append(
            {
                "category": category.name,
                "id": category.id,
                "source": source,
                "amount_per_day_paise": baseline_per_day,
                "real_days": real_days,
                "card_share": share,
            }
        )
    return result, baseline_rows, gaps


def _setup_gaps(
    snapshot: LedgerSnapshot, baseline_rows: list[BaselineSource], floor: int
) -> list[str]:
    # Setup gaps describe what is configured, not what happens to fall inside this horizon: an
    # annual salary or a fully paid one-off is still configured.
    current = [row for row in snapshot.schedules if row.ends is None or row.ends >= snapshot.as_of]
    gaps: list[str] = []
    if not snapshot.liquid:
        gaps.append(NO_LIQUID_ACCOUNT)
    if floor <= 0:
        gaps.append("no floor set; the projection uses zero")
    if not any(row.kind.startswith("income") and row.confirmed for row in current):
        gaps.append(NO_CONFIRMED_INCOME)
    if not any(row.kind in {"obligation", "instalment"} for row in current):
        gaps.append("no obligations")
    if not snapshot.goals:
        gaps.append("no active goals")
    if not baseline_rows:
        gaps.append("no spending baselines or history")
    if not any(account.known for account in snapshot.liquid):
        gaps.append("no liquid account has a known balance")
    return gaps


def _sources(snapshot: LedgerSnapshot, baseline_rows: list[BaselineSource]) -> list[SourceRef]:
    sources: list[SourceRef] = []
    for account in snapshot.liquid:
        sources.append(
            {
                "kind": "account",
                "id": account.id,
                "name": account.name,
                "detail": (
                    f"liquid · balance as of {account.as_of.isoformat()}"
                    if account.as_of
                    else "liquid · balance unknown"
                ),
                "amount_paise": account.balance_paise,
            }
        )
    for card in snapshot.cards:
        sources.append(
            {
                "kind": "card",
                "id": card.id,
                "name": card.name,
                "detail": (
                    f"statement day {card.statement_day} · due day {card.due_day} · "
                    f"outstanding {card.outstanding_paise} paise"
                ),
                "amount_paise": card.outstanding_paise,
            }
        )
    sources.extend(snapshot.schedule_sources)
    for goal in snapshot.goals:
        sources.append(
            {
                "kind": "goal",
                "id": goal.id,
                "name": goal.name,
                "detail": (
                    f"saved {goal.saved_paise} of {goal.target_paise} paise"
                    + (f" · due {goal.deadline.isoformat()}" if goal.deadline else "")
                ),
                "amount_paise": goal.target_paise,
            }
        )
    for row in baseline_rows:
        days = int(row["real_days"])
        origin = "from your history" if row["source"] == "computed" else "from your setup estimate"
        detail = f"{money(int(row['amount_per_day_paise']))['text']}/day {origin}"
        if days:
            detail += f" · {days} real day" + ("" if days == 1 else "s")
        sources.append(
            {
                "kind": "category",
                "id": row["id"],
                "name": row["category"],
                "detail": detail,
                "amount_paise": int(row["amount_per_day_paise"]),
            }
        )
    sources.extend(snapshot.owed_sources)
    unique: dict[tuple[str, str], SourceRef] = {}
    for source in sources:
        unique[(source["kind"], source["id"])] = source
    return list(unique.values())


def build_inputs(snapshot: LedgerSnapshot) -> ProjectionInput:
    """The projection's inputs: dated events, simulated spend, gaps, and every citation."""
    floor = snapshot.floor_paise
    events, covered, event_gaps = _events(snapshot)
    events.extend(snapshot.owed)
    categories, baseline_rows, category_gaps = _spend_categories(snapshot, covered)
    gaps = [*snapshot.gaps, *event_gaps, *category_gaps]
    return ProjectionInput(
        as_of=snapshot.as_of,
        floor_paise=floor,
        liquid=snapshot.liquid,
        events=tuple(sorted(events, key=lambda event: (event.date, event.name))),
        cards=snapshot.cards,
        goals=snapshot.goals,
        categories=tuple(categories),
        ask_horizon_days=snapshot.ask_horizon_days,
        data_quality={
            "balance_ages": list(snapshot.balance_ages),
            "baseline_sources": baseline_rows,
            "gaps": gaps,
            "setup_gaps": _setup_gaps(snapshot, baseline_rows, floor),
            "outside_text": snapshot.outside_text,
        },
        sources=tuple(_sources(snapshot, baseline_rows)),
    )


__all__ = [
    "CategoryRow",
    "ConfiguredSchedule",
    "LedgerSnapshot",
    "Occurrence",
    "SpendRow",
    "build_inputs",
]
