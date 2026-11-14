"""Backtest: forecast as of past days, then score each forecast against what happened next.

Every weekly origin gets one 90-day forecast, scored at horizons of 7, 14, 30, 60, and 90 days on
two targets: the balance at the horizon and the lowest balance up to it. A forecast is a set of
scenarios, so it is scored as a distribution: CRPS, pinball loss at P10/P50/P90, P10-P90 coverage
with its width, the Brier score of a floor breach with its calibration split, and where each
outcome fell in its forecast. Each forecaster is compared with simple baselines, with intervals
that allow for overlapping horizons.

Two sources. `personas` replays the synthetic households in `arno.engine.personas`, which share
no code with the engine. `db` replays the real ledger as it stood on each origin, reading only
rows that existed then. Origins with nothing to project from are dropped and counted, never
scored.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from arno.db.conn import Connection
from arno.engine import personas as personas_mod
from arno.engine import scoring
from arno.engine.inputs import (
    CategoryRow,
    ConfiguredSchedule,
    LedgerSnapshot,
    Occurrence,
    SpendRow,
    build_inputs,
)
from arno.engine.money import money
from arno.engine.projection import (
    DEFAULT_SEED,
    HISTORY_DAYS,
    UNUSABLE_SETUP_GAPS,
    CardAccount,
    GoalPlan,
    LiquidAccount,
    ProjectionInput,
    simulate_paths,
)
from arno.india.recurrence import expand_rule
from arno.india.workdays import HOLIDAY_ADJUSTMENTS, HolidayAdjust

HORIZONS = (7, 14, 30, 60, 90)
LAST_HORIZON = HORIZONS[-1]
STEP_DAYS = 7
ORIGINS_PER_HOUSEHOLD = 52
TAUS = (0.1, 0.5, 0.9)
TARGETS = ("end", "min")
REPORT_HORIZONS = (7, 30, 90)
REFERENCE = "schedule_mean"
REAL_LEDGER = "real ledger"
# Each worker holds a household and its scenario matrices; more would crowd out the app stack.
MAX_WORKERS = 6
SEED_SETS = {"tuning": personas_mod.TUNING_SEEDS, "holdout": personas_mod.HOLDOUT_SEEDS}

Paths = npt.NDArray[np.int64]
_ADJUST: dict[str, HolidayAdjust] = {value: value for value in HOLIDAY_ADJUSTMENTS}
Forecaster = Callable[[ProjectionInput, int], Paths]


# --- Forecasters -------------------------------------------------------------------------------


def _walk(inputs: ProjectionInput, *, runs: int, seed: int) -> Paths:
    paths = simulate_paths(inputs, runs=runs, seed=seed)
    return np.asarray(paths, dtype=np.int64)[:, : LAST_HORIZON + 1]


def engine_walk(inputs: ProjectionInput, seed: int) -> Paths:
    """The shipped engine: 1,000 seeded walks."""
    return _walk(inputs, runs=1_000, seed=seed)


def flat(inputs: ProjectionInput, seed: int) -> Paths:
    """Baseline: the balance never changes."""
    del seed
    return np.full((1, LAST_HORIZON + 1), inputs.starting_balance_paise, dtype=np.int64)


def schedule_only(inputs: ProjectionInput, seed: int) -> Paths:
    """Baseline: dated schedules and card bills, no everyday spending."""
    return _walk(replace(inputs, categories=()), runs=1, seed=seed)


def schedule_mean(inputs: ProjectionInput, seed: int) -> Paths:
    """Baseline: dated schedules plus each category's mean daily spend, every day."""
    flat_categories = tuple(
        replace(
            category,
            history=(),
            baseline_per_day_paise=(
                round(sum(amount for _day, amount in category.history) / len(category.history))
                if category.history
                else category.baseline_per_day_paise
            ),
        )
        for category in inputs.categories
    )
    return _walk(replace(inputs, categories=flat_categories), runs=1, seed=seed)


FORECASTERS: dict[str, Forecaster] = {
    "flat": flat,
    "schedule": schedule_only,
    "schedule_mean": schedule_mean,
    "engine_walk": engine_walk,
}
BASELINES = ("flat", "schedule", "schedule_mean")


# --- Cases ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Case:
    """One forecast problem: the inputs as of a day, and the liquid balance that followed."""

    group: str
    as_of: date
    floor_paise: int
    inputs: ProjectionInput
    actual: tuple[int, ...]

    @property
    def blocking(self) -> list[str]:
        gaps = self.inputs.data_quality.get("setup_gaps") or []
        return [gap for gap in gaps if gap in UNUSABLE_SETUP_GAPS]


@dataclass(frozen=True, slots=True)
class Score:
    group: str
    as_of: date
    forecaster: str
    target: str
    horizon: int
    crps: float
    pinball: tuple[float, float, float]
    covered: bool
    width: int
    pit: float
    breach_probability: float | None
    breached: bool | None


def score_case(case: Case, names: Sequence[str], seed: int = DEFAULT_SEED) -> list[Score]:
    """Every forecaster's scores on one case, at every horizon and target."""
    rows: list[Score] = []
    actual = np.asarray(case.actual, dtype=np.int64)
    for name in names:
        paths = FORECASTERS[name](case.inputs, seed)
        for horizon in HORIZONS:
            running_min = paths[:, : horizon + 1].min(axis=1)
            for target in TARGETS:
                if target == "end":
                    samples = np.sort(paths[:, horizon])
                    outcome = int(actual[horizon])
                else:
                    samples = np.sort(running_min)
                    outcome = int(actual[: horizon + 1].min())
                low = scoring.quantile(samples, TAUS[0])
                high = scoring.quantile(samples, TAUS[-1])
                breach: float | None = None
                breached: bool | None = None
                if target == "min":
                    breach = float(np.count_nonzero(samples < case.floor_paise)) / len(samples)
                    breached = outcome < case.floor_paise
                rows.append(
                    Score(
                        group=case.group,
                        as_of=case.as_of,
                        forecaster=name,
                        target=target,
                        horizon=horizon,
                        crps=scoring.crps(samples, outcome),
                        pinball=(
                            scoring.pinball(low, outcome, TAUS[0]),
                            scoring.pinball(scoring.quantile(samples, TAUS[1]), outcome, TAUS[1]),
                            scoring.pinball(high, outcome, TAUS[2]),
                        ),
                        covered=low <= outcome <= high,
                        width=high - low,
                        pit=scoring.pit(samples, outcome),
                        breach_probability=breach,
                        breached=breached,
                    )
                )
    return rows


# --- Personas ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Ledger:
    """A persona's daily balances, precomputed once so each origin is a lookup."""

    persona: personas_mod.Persona
    start: date
    bank: npt.NDArray[np.int64]
    cards: dict[str, npt.NDArray[np.int64]]
    goals: dict[str, npt.NDArray[np.int64]]

    def index(self, day: date) -> int:
        return (day - self.start).days


def _ledger(persona: personas_mod.Persona) -> _Ledger:
    start = persona.history_start
    days = (persona.end - start).days + 1
    bank = np.zeros(days, dtype=np.int64)
    cards = {card.id: np.zeros(days, dtype=np.int64) for card in persona.cards}
    goals = {goal.id: np.zeros(days, dtype=np.int64) for goal in persona.goals}
    for movement in persona.movements:
        offset = (movement.date - start).days
        if offset < 0 or offset >= days:
            continue
        amount = movement.amount_paise
        if movement.kind == "income":
            bank[offset] += amount
        elif movement.kind == "spend":
            if movement.account == "bank":
                bank[offset] -= amount
            else:
                cards[movement.account][offset] += amount
        elif movement.kind == "refund":
            if movement.account == "bank":
                bank[offset] += amount
            else:
                cards[movement.account][offset] -= amount
        elif movement.kind == "card_payment":
            bank[offset] -= amount
            cards[movement.account][offset] -= amount
        elif movement.kind == "goal_contribution":
            bank[offset] -= amount
            if movement.goal_id in goals:
                goals[movement.goal_id][offset] += amount
    bank[0] += persona.opening_bank_paise
    return _Ledger(
        persona=persona,
        start=start,
        bank=np.cumsum(bank),
        cards={key: np.cumsum(value) for key, value in cards.items()},
        goals={key: np.cumsum(value) for key, value in goals.items()},
    )


def _baseline(
    persona: personas_mod.Persona, category: personas_mod.Category, as_of: date
) -> tuple[int | None, str | None]:
    # Mirrors arno.importers.baselines: a computed baseline once the last 90 days hold three
    # rows on two days, else the estimate typed at setup.
    start = as_of - timedelta(days=89)
    rows = [
        movement
        for movement in persona.movements
        if movement.kind == "spend"
        and movement.category_id == category.id
        and start <= movement.date <= as_of
    ]
    if len(rows) >= 3 and len({row.date for row in rows}) >= 2:
        return round(sum(row.amount_paise for row in rows) / 90), "computed"
    if category.stated_per_day_paise is not None:
        return category.stated_per_day_paise, "estimated"
    return None, None


def persona_snapshot(ledger: _Ledger, as_of: date) -> LedgerSnapshot:
    """The persona's ledger as the loader would read it on ``as_of``, with nothing from later."""
    persona = ledger.persona
    at = ledger.index(as_of)
    end = as_of + timedelta(days=LAST_HORIZON)
    occurrences: list[Occurrence] = []
    schedules = [schedule for schedule in persona.schedules if schedule.created <= as_of]
    goals = [goal for goal in persona.goals if goal.created <= as_of]
    goal_ids = {goal.id for goal in goals}
    for schedule in schedules:
        for day in expand_rule(
            schedule.rule,
            schedule.starts,
            start=as_of + timedelta(days=1),
            end=end,
            ends=schedule.ends,
            holiday_adjust=_ADJUST[schedule.holiday_adjust],
        ):
            occurrences.append(
                Occurrence(
                    schedule_id=schedule.id,
                    name=schedule.name,
                    kind=schedule.kind,
                    date=day,
                    amount_paise=schedule.amount_paise,
                    remaining_paise=schedule.amount_paise,
                    category_id=schedule.category_id,
                    card_id=schedule.card_id,
                    goal_id=schedule.goal_id if schedule.goal_id in goal_ids else None,
                    confirmed=schedule.confirmed,
                    linked_goal=schedule.goal_id in goal_ids,
                )
            )
    window = as_of - timedelta(days=HISTORY_DAYS)
    spend: dict[tuple[date, str | None, str | None], int] = {}
    for movement in persona.movements:
        if movement.kind != "spend" or not window <= movement.date < as_of:
            continue
        card_id = None if movement.account == "bank" else movement.account
        key = (movement.date, movement.category_id, card_id)
        spend[key] = spend.get(key, 0) + movement.amount_paise
    categories: list[CategoryRow] = []
    for category in persona.categories:
        baseline, source = _baseline(persona, category, as_of)
        categories.append(
            CategoryRow(
                id=category.id,
                name=category.name,
                reducible=category.reducible,
                baseline_per_day_paise=baseline,
                baseline_source=source,
            )
        )
    return LedgerSnapshot(
        as_of=as_of,
        floor_paise=persona.floor_paise,
        liquid=(
            LiquidAccount(
                id="bank",
                name="Bank",
                balance_paise=int(ledger.bank[at]),
                as_of=as_of,
                balance_age_days=0,
            ),
        ),
        cards=tuple(
            CardAccount(
                id=card.id,
                name=card.name,
                statement_day=card.statement_day,
                due_day=card.due_day,
                outstanding_paise=max(int(ledger.cards[card.id][at]), 0),
                credit_limit_paise=card.credit_limit_paise,
            )
            for card in persona.cards
        ),
        # Deadlines only stretch the horizon past what is scored; they never move the balance.
        goals=tuple(
            GoalPlan(
                id=goal.id,
                name=goal.name,
                target_paise=goal.target_paise,
                saved_paise=int(ledger.goals[goal.id][at]),
                priority=goal.priority,
            )
            for goal in goals
        ),
        occurrences=tuple(occurrences),
        schedules=tuple(
            ConfiguredSchedule(kind=schedule.kind, confirmed=schedule.confirmed, ends=schedule.ends)
            for schedule in schedules
        ),
        categories=tuple(categories),
        spend=tuple(
            SpendRow(date=day, category_id=category, card_id=card, amount_paise=amount)
            for (day, category, card), amount in sorted(
                spend.items(), key=lambda item: (item[0][0], item[0][1] or "", item[0][2] or "")
            )
        ),
        ask_horizon_days=LAST_HORIZON,
    )


def persona_origins(persona: personas_mod.Persona) -> list[date]:
    """Weekly origins over the last year that leave a full 90 days to score."""
    last = persona.end - timedelta(days=LAST_HORIZON)
    first = persona.history_start + timedelta(days=14)
    origins = [last - timedelta(days=STEP_DAYS * step) for step in range(ORIGINS_PER_HOUSEHOLD)]
    return sorted(day for day in origins if day >= first)


def persona_cases(key: str, seed: int) -> list[Case]:
    persona = personas_mod.generate(key, seed)
    ledger = _ledger(persona)
    cases: list[Case] = []
    for as_of in persona_origins(persona):
        at = ledger.index(as_of)
        cases.append(
            Case(
                group=f"{key}:{seed}",
                as_of=as_of,
                floor_paise=persona.floor_paise,
                inputs=build_inputs(persona_snapshot(ledger, as_of)),
                actual=tuple(int(value) for value in ledger.bank[at : at + LAST_HORIZON + 1]),
            )
        )
    return cases


def _household_scores(key: str, seed: int, names: tuple[str, ...]) -> tuple[list[Score], int]:
    scores: list[Score] = []
    dropped = 0
    for case in persona_cases(key, seed):
        if case.blocking:
            dropped += 1
            continue
        scores.extend(score_case(case, names))
    return scores, dropped


def backtest_personas(
    *,
    seeds: Sequence[int] = personas_mod.HOLDOUT_SEEDS,
    names: Sequence[str] = tuple(FORECASTERS),
    keys: Sequence[str] = personas_mod.PERSONA_KEYS,
    workers: int | None = None,
) -> dict[str, Any]:
    """Score every forecaster on every persona and seed, one process per household."""
    jobs = [(key, seed, tuple(names)) for key in keys for seed in seeds]
    cores = (os.cpu_count() or 2) - 1
    count = workers if workers is not None else max(1, min(len(jobs), cores, MAX_WORKERS))
    if count == 1:
        results = [_household_scores(*job) for job in jobs]
    else:
        with ProcessPoolExecutor(max_workers=count) as pool:
            results = list(pool.map(_household_scores, *zip(*jobs, strict=True)))
    scores = [score for household, _dropped in results for score in household]
    dropped = sum(dropped for _household, dropped in results)
    label = f"synthetic personas, seeds {', '.join(str(seed) for seed in seeds)}"
    return summarize(scores, source=label, dropped=dropped, names=names)


# --- The real ledger ---------------------------------------------------------------------------


async def _liquid_path(conn: Connection, account_ids: list[str], start: date) -> tuple[int, ...]:
    if not account_ids:
        return (0,) * (LAST_HORIZON + 1)
    rows = await conn.fetch(
        """
        SELECT m.date <= $2 AS opening, m.date, coalesce(sum(e.amount), 0)::bigint AS amount
        FROM accounts a
        JOIN pgledger_entries e ON e.account_id = a.ledger_account_id
        JOIN movement_legs ml ON ml.transfer_id = e.transfer_id
        JOIN movements m ON m.id = ml.movement_id
        WHERE a.id = ANY(ARRAY(SELECT unnest($1::text[])))
          AND m.date <= $3
        GROUP BY 1, 2
        """,
        account_ids,
        start,
        start + timedelta(days=LAST_HORIZON),
    )
    daily = [0] * (LAST_HORIZON + 1)
    for row in rows:
        offset = 0 if row["opening"] else (row["date"] - start).days
        daily[offset] += int(row["amount"])
    return tuple(int(value) for value in np.cumsum(daily))


async def db_cases(conn: Connection) -> list[Case]:
    """Weekly origins over the real ledger, each read as the ledger stood that day."""
    from arno.tools.projection import load_snapshot

    bounds = await conn.fetchrow("SELECT min(date) AS first, max(date) AS last FROM movements")
    if not bounds or bounds["first"] is None:
        return []
    last = bounds["last"] - timedelta(days=LAST_HORIZON)
    origins = [last - timedelta(days=STEP_DAYS * step) for step in range(ORIGINS_PER_HOUSEHOLD)]
    first = bounds["first"] + timedelta(days=14)
    cases: list[Case] = []
    for as_of in sorted(day for day in origins if day >= first):
        snapshot = await load_snapshot(
            conn, as_of=as_of, horizon_days=LAST_HORIZON, known_then=True
        )
        inputs = build_inputs(replace(snapshot, goals=tuple(_no_deadline(snapshot.goals))))
        cases.append(
            Case(
                group="ledger",
                as_of=as_of,
                floor_paise=inputs.floor_paise,
                inputs=inputs,
                actual=await _liquid_path(conn, [row.id for row in inputs.liquid], as_of),
            )
        )
    return cases


def _no_deadline(goals: Iterable[GoalPlan]) -> Iterable[GoalPlan]:
    # A far deadline only stretches the walk past the scored 90 days.
    return (replace(goal, deadline=None) for goal in goals)


async def backtest_db(
    conn: Connection, *, names: Sequence[str] = tuple(FORECASTERS)
) -> dict[str, Any]:
    scores: list[Score] = []
    dropped = 0
    for case in await db_cases(conn):
        if case.blocking:
            dropped += 1
            continue
        scores.extend(score_case(case, names))
    return summarize(scores, source=REAL_LEDGER, dropped=dropped, names=names)


async def backtest(
    conn: Connection | None = None,
    *,
    source: str = "personas",
    seeds: str = "holdout",
    names: Sequence[str] = tuple(FORECASTERS),
) -> dict[str, Any]:
    if source not in {"personas", "db"}:
        raise ValueError("source is personas or db")
    if source == "db":
        if conn is None:
            raise ValueError("the db source needs a database connection")
        return await backtest_db(conn, names=names)
    if seeds not in SEED_SETS:
        raise ValueError("seeds is tuning or holdout")
    return backtest_personas(seeds=SEED_SETS[seeds], names=names)


# --- Summary and report --------------------------------------------------------------------------


def _groups(scores: Sequence[Score], value: Callable[[Score], float]) -> list[list[float]]:
    by_group: dict[str, list[tuple[date, float]]] = {}
    for score in scores:
        by_group.setdefault(score.group, []).append((score.as_of, value(score)))
    return [[item for _day, item in sorted(rows)] for _group, rows in sorted(by_group.items())]


def _estimate(
    estimate: scoring.Estimate, *, bounds: tuple[float, float] | None = None
) -> dict[str, float | int]:
    low, high = estimate.low, estimate.high
    if bounds is not None:
        low, high = max(low, bounds[0]), min(high, bounds[1])
    return {"mean": estimate.mean, "low": low, "high": high, "n": estimate.n}


def summarize(
    scores: Sequence[Score], *, source: str, dropped: int, names: Sequence[str]
) -> dict[str, Any]:
    """Aggregate per-case scores into one row per forecaster, target, and horizon."""
    metrics: list[dict[str, Any]] = []
    cases = len({(score.group, score.as_of) for score in scores})
    for horizon in HORIZONS:
        lag = max(0, math.ceil(horizon / STEP_DAYS) - 1)
        for target in TARGETS:
            subset = {
                name: [
                    score
                    for score in scores
                    if score.forecaster == name
                    and score.target == target
                    and score.horizon == horizon
                ]
                for name in names
            }
            for name in names:
                rows = subset[name]
                if not rows:
                    continue
                crps_groups = _groups(rows, lambda score: score.crps)
                row: dict[str, Any] = {
                    "forecaster": name,
                    "target": target,
                    "horizon": horizon,
                    "crps": _estimate(scoring.hac_estimate(crps_groups, lag)),
                    "pinball": [
                        sum(score.pinball[index] for score in rows) / len(rows)
                        for index in range(len(TAUS))
                    ],
                    "coverage": _estimate(
                        scoring.hac_estimate(
                            _groups(rows, lambda score: 1.0 if score.covered else 0.0), lag
                        ),
                        bounds=(0.0, 1.0),
                    ),
                    "width_paise": round(sum(score.width for score in rows) / len(rows)),
                    "pit": scoring.histogram([score.pit for score in rows]),
                    "skill": {},
                }
                if target == "min":
                    brier = scoring.brier(
                        [score.breach_probability or 0.0 for score in rows],
                        [bool(score.breached) for score in rows],
                    )
                    row["brier"] = {
                        "score": brier.score,
                        "reliability": brier.reliability,
                        "resolution": brier.resolution,
                        "uncertainty": brier.uncertainty,
                        "base_rate": brier.base_rate,
                    }
                for baseline in BASELINES:
                    reference = subset.get(baseline) or []
                    if baseline == name or not reference:
                        continue
                    reference_groups = _groups(reference, lambda score: score.crps)
                    reference_mean = sum(map(sum, reference_groups)) / len(reference)
                    statistic, p_value = scoring.diebold_mariano(crps_groups, reference_groups, lag)
                    row["skill"][baseline] = {
                        "skill": (
                            1 - row["crps"]["mean"] / reference_mean if reference_mean else math.nan
                        ),
                        "dm": statistic,
                        "p": p_value,
                    }
                metrics.append(row)
    return {"source": source, "cases": cases, "dropped": dropped, "metrics": metrics}


def _rupees(paise: float) -> str:
    return money(round(paise))["text"]


def format_report(result: dict[str, Any]) -> str:
    if not result["cases"]:
        return (
            f"NOT A MEASUREMENT: no origin of the {result['source']} had anything to project "
            f"from ({result['dropped']} dropped). This run must not be published."
        )
    lines = [
        f"Source: {result['source']}. {result['cases']} weekly origins scored, "
        f"{result['dropped']} dropped for missing setup.",
        "CRPS is the forecast's distance from the outcome in rupees (lower is better); skill is "
        f"against `{REFERENCE}` with its Diebold-Mariano p-value; coverage is how often the "
        "outcome fell inside P10-P90, with a 95% interval.",
    ]
    if result["source"] == REAL_LEDGER:
        lines.append(
            "Known limit: accounts, schedules, and goals created after an origin are excluded, "
            "but a later edit to one that already existed is read in its current form."
        )
    lines += [
        "",
        "| Target | Days | Forecaster | CRPS | Skill (p) | P10-P90 coverage | Width "
        "| Breach Brier |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in result["metrics"]:
        if row["horizon"] not in REPORT_HORIZONS:
            continue
        skill = row["skill"].get(REFERENCE)
        coverage = row["coverage"]
        brier = row.get("brier")
        lines.append(
            "| "
            + " | ".join(
                [
                    "lowest" if row["target"] == "min" else "end",
                    str(row["horizon"]),
                    row["forecaster"],
                    _rupees(row["crps"]["mean"]),
                    f"{skill['skill']:+.1%} ({skill['p']:.2f})" if skill else "-",
                    f"{coverage['mean']:.0%} ({coverage['low']:.0%}-{coverage['high']:.0%})",
                    _rupees(row["width_paise"]),
                    f"{brier['score']:.3f}" if brier else "-",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def publish_report(result: dict[str, Any], readme: Path) -> None:
    if not result["cases"]:
        # A published score with no measured origin would read as a claim about the engine.
        raise ValueError(
            f"this backtest is not a measurement: all {result['dropped']} origins had nothing to "
            "project from. README.md was not changed."
        )
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
    "BASELINES",
    "FORECASTERS",
    "HORIZONS",
    "Case",
    "Score",
    "backtest",
    "backtest_db",
    "backtest_personas",
    "format_report",
    "persona_cases",
    "persona_snapshot",
    "publish_report",
    "score_case",
    "summarize",
]
