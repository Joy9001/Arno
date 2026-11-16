"""Affordability: the yes, yes-but, or no verdict, and the smallest changes that make it a yes.

The verdict is never prose from a model. The purchase is applied as asked and the scenario kernel
walks every future once: it passes when the floor probability reaches the threshold and every
obligation is met. When it does not, each lever is a known change to each scenario's balances at
the checkpoints, the only days a minimum can fall on:

- paying by card or card EMI moves the cash onto bill due dates (one kernel run per method);
- skipping a goal's contributions adds them back from their dates;
- cutting reducible spend by a share frees that share of the reducible spend that has left cash
  by each checkpoint (card spend only once its bill is paid).

For a lever where more is always at least as safe, each scenario has a critical value, the least
setting at which it passes, and the least setting that passes a share p of scenarios is the
ceil(p*N)-th smallest critical value. Plans combine a method, a set of skipped goals, and the
least cut; a plan is kept only when no other passing plan of that method skips a subset of its
goals with a cut no larger. Every reported plan is applied to the inputs and rerun on the same
scenarios, and every number shown comes from that rerun. A later date alone stays a "no": the
wait date is shown, but a yes-but needs a change that keeps the date.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from itertools import combinations

import numpy as np

from arno.engine import kernel
from arno.engine import projection as projection_mod
from arno.engine.money import DEFAULT_GST_BPS, add_months, card_emi, money
from arno.engine.projection import (
    DEFAULT_RUNS,
    DEFAULT_SEED,
    FLOOR_THRESHOLD,
    MAX_CUT_RATIO,
    MAX_HORIZON_DAYS,
    OBLIGATION_THRESHOLD,
    CardAccount,
    CashEvent,
    GoalRow,
    HypotheticalMeta,
    Installment,
    Projection,
    ProjectionInput,
    Scenario,
    ScenarioBaseline,
    ScenarioChange,
    Tradeoff,
    card_due_date,
    checkpoints_for,
    first_full_payment_date,
    goal_rows,
    order_rank,
    prepare,
    project,
    reduce_categories,
)
from arno.engine.scenario import (
    ScenarioChangeSpec,
    apply_changes,
    scenario_horizon_days,
)

METHODS = ("account", "card", "emi")
DEFAULT_EMI_RATE_BPS = 1_600
DEFAULT_EMI_MONTHS = 3
GOAL_WINDOW_DAYS = 31
MAX_CUT_BPS = round(MAX_CUT_RATIO * 10_000)
MAX_PLANS = 4
# How many later dates the wait tradeoff tests before giving up: a dated payment leaves the floor
# alone, but an obligation it misses can hold the verdict back for a while.
MAX_WAIT_ATTEMPTS = 12
# Rounding can leave a verified cut a half paisa short; one or two basis points more fixes it.
VERIFY_STEPS = 4


@dataclass(frozen=True, slots=True)
class Hypothetical:
    amount_paise: int
    date: date
    method: str = "account"
    emi_months: int | None = None
    account: str | None = None
    # An EMI's annual rate in basis points and its processing fee, when the offer states them.
    emi_rate_bps: int | None = None
    emi_fee_paise: int | None = None
    # A no-cost EMI: the merchant pays the interest, so only the fee and its GST remain.
    no_cost: bool = False


def _pick_card(inputs: ProjectionInput, account: str | None) -> CardAccount | None:
    if not account:
        return max(
            inputs.cards,
            key=lambda card: (card.known, card.available_credit_paise),
            default=None,
        )
    wanted = account.strip().lower()
    return next(
        (card for card in inputs.cards if card.id == account or card.name.lower() == wanted),
        None,
    )


@dataclass(frozen=True, slots=True)
class _Applied:
    """The inputs with the purchase applied, what the purchase was, and why it cannot happen."""

    inputs: ProjectionInput
    meta: HypotheticalMeta
    reason: str | None
    cost_paise: int = 0
    gaps: tuple[str, ...] = ()


def _emi_terms(hypothetical: Hypothetical, card: CardAccount) -> tuple[int, int, list[str]]:
    """The EMI's rate and fee: the offer's, else the card's, else a labeled default."""
    gaps: list[str] = []
    if hypothetical.no_cost:
        rate = 0
    elif hypothetical.emi_rate_bps is not None:
        rate = hypothetical.emi_rate_bps
    elif card.emi_rate_bps is not None:
        rate = card.emi_rate_bps
    else:
        # An unknown rate assumed to be zero is the one value known to be wrong the unsafe way.
        rate = DEFAULT_EMI_RATE_BPS
        gaps.append(
            f"the EMI's cost assumes {DEFAULT_EMI_RATE_BPS / 100:g}% a year plus 18% GST on the "
            f"interest; {card.name}'s EMI rate is not recorded"
        )
    if hypothetical.emi_fee_paise is not None:
        fee = hypothetical.emi_fee_paise
    elif card.emi_fee_paise is not None:
        fee = card.emi_fee_paise
    else:
        fee = 0
        gaps.append("no EMI processing fee is assumed")
    return rate, fee, gaps


def _apply(inputs: ProjectionInput, hypothetical: Hypothetical) -> _Applied:
    """Apply the purchase and return the new inputs, its metadata, and an infeasible reason."""
    amount = int(hypothetical.amount_paise)
    if amount <= 0:
        return _Applied(inputs, {}, "the amount must be positive")
    if hypothetical.method not in METHODS:
        return _Applied(inputs, {}, "method is account, card, or emi")
    when = max(hypothetical.date, inputs.as_of)
    at_start = hypothetical.date <= inputs.as_of
    name = "Hypothetical purchase"
    meta: HypotheticalMeta = {
        "amount_paise": amount,
        "date": hypothetical.date.isoformat(),
        "method": hypothetical.method,
        "emi_months": hypothetical.emi_months if hypothetical.method == "emi" else None,
        "account": hypothetical.account,
    }

    if hypothetical.method == "account":
        meta["last_cash_date"] = when.isoformat()
        event = CashEvent(
            date=when, amount_paise=-amount, name=name, kind="hypothetical", at_start=at_start
        )
        return _Applied(replace(inputs, events=(*inputs.events, event)), meta, None)

    months = hypothetical.emi_months if hypothetical.emi_months is not None else DEFAULT_EMI_MONTHS
    if hypothetical.method == "emi":
        if not 2 <= months <= 48:
            return _Applied(inputs, meta, "an EMI tenure is 2 to 48 months")
        meta["emi_months"] = months

    card = _pick_card(inputs, hypothetical.account)
    if card is None:
        return _Applied(inputs, meta, "no card with available credit was found")
    if not card.known:
        return _Applied(
            inputs,
            meta,
            f"{card.name} has no known outstanding balance, so its available credit is unknown.",
        )
    if amount > card.available_credit_paise:
        available = money(card.available_credit_paise)["text"]
        return _Applied(
            inputs,
            {**meta, "available_credit_paise": card.available_credit_paise},
            f"{card.name} has only {available} of available credit",
        )
    meta["card_id"] = card.id
    meta["card_name"] = card.name

    if hypothetical.method == "card":
        due = card_due_date(card, when)
        meta["due_date"] = due.isoformat()
        meta["last_cash_date"] = due.isoformat()
        event = CashEvent(
            date=when,
            amount_paise=-amount,
            name=name,
            kind="hypothetical",
            card_id=card.id,
            at_start=at_start,
        )
        return _Applied(replace(inputs, events=(*inputs.events, event)), meta, None)

    rate, fee, gaps = _emi_terms(hypothetical, card)
    try:
        plan = card_emi(
            amount,
            annual_rate_bps=rate,
            months=months,
            processing_fee_paise=fee,
            gst_bps=DEFAULT_GST_BPS,
        )
    except ValueError as exc:
        return _Applied(inputs, meta, str(exc))
    installments: list[Installment] = []
    events: list[CashEvent] = []
    for row in plan["schedule"]:
        due_day = add_months(when, row["n"] - 1)
        installments.append({"date": due_day.isoformat(), "amount_paise": row["total_paise"]})
        events.append(
            CashEvent(
                date=due_day,
                amount_paise=-row["total_paise"],
                name=f"{name} instalment {row['n']}",
                kind="hypothetical_emi",
                card_id=card.id,
                at_start=due_day <= inputs.as_of,
            )
        )
    meta["installments"] = installments
    meta["total_paise"] = plan["total_paise"]
    meta["emi_rate_bps"] = rate
    meta["emi_cost_paise"] = plan["cost_paise"]
    meta["last_cash_date"] = card_due_date(card, add_months(when, months - 1)).isoformat()
    return _Applied(
        replace(inputs, events=(*inputs.events, *events)),
        meta,
        None,
        cost_paise=plan["cost_paise"],
        gaps=tuple(gaps),
    )


def _extend_horizon(inputs: ProjectionInput, meta: HypotheticalMeta) -> ProjectionInput:
    """Cover the plan's last cash date, so an EMI's later installments are not ignored."""
    last_cash = meta.get("last_cash_date")
    if not last_cash:
        return inputs
    needed = (date.fromisoformat(last_cash) - inputs.as_of).days + 1
    if needed > (inputs.ask_horizon_days or 0):
        return replace(inputs, ask_horizon_days=needed)
    return inputs


def _passes(result: Projection, *, threshold: float) -> bool:
    return float(result["floor_probability"]) >= threshold and bool(result["obligations_met"])


# --- The levers on one scenario matrix --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Matrix:
    """One method's scenario balances at the checkpoints, and what a cut frees by each."""

    method: str
    applied: _Applied
    marks: tuple[int, ...]
    balances: kernel.I64  # (scenarios, checkpoints)
    reducible: kernel.I64 | None  # cash-realized reducible spend, same shape
    capped: bool  # a card went past its limit in some scenario, so a cut frees cash sooner


def _matrix(method: str, applied: _Applied, *, cut_until: int, runs: int, seed: int) -> _Matrix:
    model = projection_mod.MODEL
    prepared = prepare(applied.inputs, model)
    marks = checkpoints_for(prepared.spec)
    settings = kernel.Settings(
        scenarios=runs,
        seed=seed,
        block_length=model.block_length,
        checkpoints=marks,
        reducible_until=cut_until,
    )
    run = kernel.run(prepared.spec, settings)
    assert run.checkpoints is not None
    return _Matrix(
        method=method,
        applied=applied,
        marks=marks,
        balances=run.checkpoints,
        reducible=run.reducible,
        capped=run.capped is not None and bool(run.capped.any()),
    )


def _goal_relief(
    inputs: ProjectionInput, goal_ids: tuple[str, ...], when: date
) -> tuple[ProjectionInput, list[tuple[int, int]]]:
    """Skip these goals' contributions in the month after the purchase.

    Returns the inputs without them and each removed contribution as (day index, amount).
    """
    window_end = when + timedelta(days=GOAL_WINDOW_DAYS)
    kept: list[CashEvent] = []
    removed: list[tuple[int, int]] = []
    for event in inputs.events:
        if (
            event.kind == "goal_contribution"
            and event.goal_id in goal_ids
            and when <= event.date <= window_end
        ):
            removed.append((max(0, (event.date - inputs.as_of).days), -event.amount_paise))
        else:
            kept.append(event)
    return replace(inputs, events=tuple(kept)), removed


def _shift(marks: tuple[int, ...], removed: list[tuple[int, int]]) -> kernel.I64:
    """The cash a set of skipped contributions leaves in hand at each checkpoint."""
    days = np.array(marks, dtype=np.int64)
    shift = np.zeros(len(marks), dtype=np.int64)
    for day, amount in removed:
        shift += np.where(days >= day, amount, 0)
    return shift


def _least_cut(
    balances: kernel.I64, reducible: kernel.I64 | None, floor: int, needed: int
) -> int | None:
    """The least cut in basis points that lets ``needed`` scenarios clear the floor.

    A scenario clears it at cut b when every checkpoint has balance + b/10,000 * reducible >=
    floor, so its critical value is the largest ceil(10,000 * (floor - balance) / reducible) over
    its short checkpoints, compared in whole integers. None when no cut up to the cap does.
    """
    short = floor - balances
    if reducible is None:
        clearing = int(np.count_nonzero((short <= 0).all(axis=1)))
        return 0 if clearing >= needed else None
    impossible = (short > 0) & (reducible <= 0)
    bps = np.where(short > 0, -((-10_000 * short) // np.maximum(reducible, 1)), 0)
    critical = np.where(impossible.any(axis=1), np.iinfo(np.int64).max, bps.max(axis=1))
    least = int(np.partition(critical, needed - 1)[needed - 1])
    return least if least <= MAX_CUT_BPS else None


# --- Plans -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Plan:
    method: str  # "asked", "card", or "emi"
    goals: tuple[str, ...]
    cut_bps: int
    cost_paise: int

    @property
    def levers(self) -> int:
        return int(self.method != "asked") + len(self.goals) + int(self.cut_bps > 0)


def _plans(
    matrices: list[_Matrix],
    goals: tuple[str, ...],
    when: date,
    *,
    floor: int,
    needed: int,
    spend_paise: int,
) -> list[_Plan]:
    """Every minimal plan that clears the floor on the scenarios, fewest levers then cheapest.

    `goals` comes most important first; between equally cheap plans, the one that delays the
    less important goals comes first, so a priority change revises which goal is offered.
    """
    found: list[_Plan] = []
    for matrix in matrices:
        clearing: dict[tuple[str, ...], int] = {}
        for size in range(len(goals) + 1):
            for subset in combinations(goals, size):
                _skipped, removed = _goal_relief(matrix.applied.inputs, subset, when)
                shifted = matrix.balances + _shift(matrix.marks, removed)[None, :]
                cut = _least_cut(shifted, matrix.reducible, floor, needed)
                if cut is None:
                    continue
                if any(set(other) < set(subset) and clearing[other] <= cut for other in clearing):
                    continue
                clearing[subset] = cut
                cost = matrix.applied.cost_paise + cut * spend_paise // 10_000
                found.append(_Plan(matrix.method, subset, cut, cost))
    found = [plan for plan in found if plan.levers > 0]
    # Across methods too: a plan that changes no more, skips a subset of the goals, cuts no more,
    # and costs no more leaves the other nothing to offer (card float beside a paid EMI).
    kept = [plan for plan in found if not any(_dominates(other, plan) for other in found)]
    rank = {goal: index for index, goal in enumerate(goals)}
    kept.sort(
        key=lambda plan: (
            plan.levers,
            plan.cost_paise,
            sorted(-rank[goal] for goal in plan.goals),
            plan.method,
            plan.goals,
        )
    )
    return kept


def _dominates(better: _Plan, plan: _Plan) -> bool:
    if better == plan:
        return False
    return (
        int(better.method != "asked") <= int(plan.method != "asked")
        and set(better.goals) <= set(plan.goals)
        and better.cut_bps <= plan.cut_bps
        and better.cost_paise <= plan.cost_paise
    )


def _next_payday(inputs: ProjectionInput, when: date) -> date | None:
    income_days = sorted(
        event.date
        for event in inputs.events
        if event.amount_paise > 0
        and event.kind.startswith("income")
        and event.confirmed
        and event.date > when
    )
    return income_days[0] if income_days else None


@dataclass(frozen=True, slots=True)
class _Verified:
    plan: _Plan
    result: Projection
    inputs: ProjectionInput
    meta: HypotheticalMeta


def _with_plan(
    applied: _Applied, plan: _Plan, cut: int, when: date, until: date
) -> ProjectionInput:
    inputs, _removed = _goal_relief(applied.inputs, plan.goals, when)
    if cut > 0:
        inputs, _rows = reduce_categories(inputs, cut / 10_000, until=until)
    return inputs


def _verify(
    plan: _Plan,
    matrix: _Matrix,
    *,
    when: date,
    until: date,
    runs: int,
    seed: int,
    threshold: float,
) -> _Verified | None:
    """Apply the plan, rerun the kernel, and keep it only if it passes there.

    Rounding can leave the cut a half paisa short, so a failing cut rises a basis point at a
    time. Where a card limit binds, a cut frees cash sooner than its bill date, so the critical
    value is an upper bound; the least passing cut below it is then found by bisection.
    """
    applied = matrix.applied

    def attempt(cut: int) -> _Verified | None:
        inputs = _with_plan(applied, plan, cut, when, until)
        result = project(inputs, runs=runs, seed=seed)
        if not _passes(result, threshold=threshold):
            return None
        return _Verified(replace(plan, cut_bps=cut), result, inputs, applied.meta)

    cut = plan.cut_bps
    verified: _Verified | None = None
    for _step in range(VERIFY_STEPS):
        verified = attempt(cut)
        if verified is not None or cut == 0 or cut >= MAX_CUT_BPS:
            break
        cut += 1
    if verified is None or not matrix.capped or cut == 0:
        return verified
    low, high = 0, cut
    while low < high:
        middle = (low + high) // 2
        better = attempt(middle)
        if better is None:
            low = middle + 1
        else:
            high, verified = middle, better
    return verified


def _describe(
    verified: _Verified, inputs: ProjectionInput, baseline_goals: list[GoalRow], until: date
) -> Tradeoff:
    plan = verified.plan
    meta = verified.meta
    parts: list[str] = []
    kinds: list[str] = []
    trade: Tradeoff = {"kind": "", "detail": ""}
    if plan.method != "asked":
        kinds.append("pay_by_card")
        trade["method"] = str(meta.get("method"))
        trade["account"] = meta.get("card_name")
        trade["due_date"] = meta.get("due_date")
        trade["installments"] = meta.get("installments")
        if meta.get("method") == "card":
            parts.append(
                f"put it on {meta.get('card_name')} and pay the bill on {meta.get('due_date')}"
            )
        else:
            count = len(meta.get("installments") or [])
            cost = money(int(meta.get("emi_cost_paise") or 0))["text"]
            parts.append(
                f"split it into {count} {meta.get('card_name')} instalments, which cost {cost} in "
                "interest, GST, and fees"
            )
    if plan.goals:
        kinds.append("delay_goal")
        rows = {row["id"]: row for row in goal_rows(verified.inputs)}
        before = {row["id"]: row for row in baseline_goals}
        skipped: list[str] = []
        for goal_id in plan.goals:
            row = rows[goal_id]
            old = before[goal_id]["completes_on"] if goal_id in before else None
            new = row["completes_on"]
            slip: int | None = None
            if old is not None and new is not None:
                slip = max(0, (date.fromisoformat(new) - date.fromisoformat(old)).days)
            trade["goal"] = row["name"]
            trade["goal_id"] = goal_id
            trade["slips_days"] = slip
            trade["completes_on"] = new
            if new is None:
                status = "no longer completes within the projection"
            elif slip:
                status = f"completes {slip} days later"
            else:
                status = "still completes on time"
            skipped.append(f"skip the next {row['name']} contribution (the goal {status})")
        parts.append("; ".join(skipped))
    if plan.cut_bps > 0:
        kinds.append("cut_category")
        _reduced, rows_cut = reduce_categories(inputs, plan.cut_bps / 10_000, until=until)
        trade["until"] = until.isoformat()
        trade["ratio"] = round(plan.cut_bps / 10_000, 4)
        trade["categories"] = rows_cut
        names = ", ".join(row["category"] for row in rows_cut)
        parts.append(
            f"cut {names} by {plan.cut_bps / 100:g}% until {until.isoformat()}, then spend as usual"
        )
    trade["kind"] = kinds[0] if len(kinds) == 1 else "combined"
    if len(kinds) > 1:
        trade["levers"] = kinds
    trade["cost_paise"] = plan.cost_paise
    trade["floor_probability"] = verified.result["floor_probability"]
    detail = "; and ".join(parts)
    trade["detail"] = detail[:1].upper() + detail[1:] + "."
    return trade


def _pay_on(inputs: ProjectionInput, amount_paise: int, when: date) -> ProjectionInput:
    """The inputs with an account payment of ``amount_paise`` on ``when``."""
    return replace(
        inputs,
        events=(
            *inputs.events,
            CashEvent(
                date=when,
                amount_paise=-amount_paise,
                name="Hypothetical purchase",
                kind="hypothetical",
            ),
        ),
    )


def _wait_tradeoff(
    inputs: ProjectionInput,
    hypothetical: Hypothetical,
    *,
    runs: int,
    seed: int,
    threshold: float,
) -> Tradeoff | None:
    """The earliest later date the purchase passes on, floor and obligations both.

    `first_full_payment_date` reads only the floor, so every date it offers is paid for real and
    re-projected: a date that still misses an obligation is passed over, searching from the day
    after it.
    """
    when = max(hypothetical.date, inputs.as_of)
    amount = int(hypothetical.amount_paise)
    start = when
    for _attempt in range(MAX_WAIT_ATTEMPTS):
        found = first_full_payment_date(
            inputs, amount, start_on=start, runs=runs, seed=seed, threshold=threshold
        )
        if not found:
            return None
        found_on = date.fromisoformat(found["date"])
        paid = project(_pay_on(inputs, amount, found_on), runs=runs, seed=seed)
        if _passes(paid, threshold=threshold):
            if found_on == when:
                # Paying on the asked date is the ask, not a tradeoff.
                return None
            return {
                "kind": "wait",
                "date": found["date"],
                "days": found["days"],
                "detail": (
                    f"Wait until {found['date']} ({found['days']} days), then pay the full amount."
                ),
            }
        start = found_on + timedelta(days=1)
    return None


def _tradeoffs(
    inputs: ProjectionInput,
    hypothetical: Hypothetical,
    applied: _Applied,
    baseline_goals: list[GoalRow],
    *,
    runs: int,
    seed: int,
    threshold: float,
) -> list[Tradeoff]:
    when = max(hypothetical.date, inputs.as_of)
    payday = _next_payday(inputs, when) or (when + timedelta(days=30))
    reducible = any(
        category.reducible and category.baseline_per_day_paise > 0 for category in inputs.categories
    )
    cut_until = (payday - inputs.as_of).days if reducible else -1
    needed = runs - order_rank(runs, 1 - threshold)

    variants: list[tuple[str, _Applied]] = [("asked", applied)]
    for method in ("card", "emi"):
        if method == hypothetical.method:
            continue
        alternative = replace(
            hypothetical,
            method=method,
            emi_months=(hypothetical.emi_months or DEFAULT_EMI_MONTHS) if method == "emi" else None,
        )
        other = _apply(inputs, alternative)
        if other.reason is None:
            extended = replace(other, inputs=_extend_horizon(other.inputs, other.meta))
            variants.append((method, extended))
    matrices = [
        _matrix(name, variant, cut_until=cut_until, runs=runs, seed=seed)
        for name, variant in variants
    ]
    goals = tuple(
        goal.id
        for goal in sorted(inputs.goals, key=lambda item: (item.priority, item.name))
        if any(
            event.kind == "goal_contribution"
            and event.goal_id == goal.id
            and when <= event.date <= when + timedelta(days=GOAL_WINDOW_DAYS)
            for event in inputs.events
        )
    )
    first = matrices[0]
    spend = int(np.median(first.reducible[:, -1])) if first.reducible is not None else 0
    plans = _plans(
        matrices, goals, when, floor=inputs.floor_paise, needed=needed, spend_paise=spend
    )
    by_method = {matrix.method: matrix for matrix in matrices}
    trades: list[Tradeoff] = []
    for plan in plans:
        if len(trades) >= MAX_PLANS:
            break
        verified = _verify(
            plan,
            by_method[plan.method],
            when=when,
            until=payday,
            runs=runs,
            seed=seed,
            threshold=threshold,
        )
        if verified is not None:
            trades.append(_describe(verified, inputs, baseline_goals, payday))
    return trades


def _validate_hypothetical(inputs: ProjectionInput, hypothetical: Hypothetical) -> None:
    if hypothetical.amount_paise <= 0:
        raise ValueError("the amount must be positive")
    if hypothetical.method not in METHODS:
        raise ValueError("method is account, card, or emi")
    if hypothetical.method == "emi":
        tenure = hypothetical.emi_months if hypothetical.emi_months is not None else 3
        if not 2 <= tenure <= 48:
            raise ValueError("an EMI tenure is 2 to 48 months")
    if (hypothetical.date - inputs.as_of).days > MAX_HORIZON_DAYS - 30:
        raise ValueError("the purchase date is beyond the projection horizon")


def _outcome_reason(result: Projection, *, floor_paise: int, threshold: float) -> tuple[bool, str]:
    if _passes(result, threshold=threshold):
        return (
            True,
            f"{result['floor_probability']:.0%} of simulated futures stay at or above the "
            "floor and every obligation is met.",
        )
    if not result["obligations_met"]:
        return (
            False,
            "An obligation cannot be paid on its date in more than "
            f"{1 - OBLIGATION_THRESHOLD:.0%} of futures.",
        )
    where = "the floor" if floor_paise > 0 else "zero"
    return (
        False,
        f"Only {result['floor_probability']:.0%} of simulated futures stay above "
        f"{where} at the {threshold:.0%} threshold.",
    )


def afford(
    inputs: ProjectionInput,
    hypothetical: Hypothetical,
    *,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
    threshold: float = FLOOR_THRESHOLD,
) -> Projection:
    """Return the projection with the purchase applied, a tested verdict, and minimal plans."""
    _validate_hypothetical(inputs, hypothetical)
    known_start = any(account.known for account in inputs.liquid)
    applied = _apply(inputs, hypothetical)
    meta = applied.meta
    meta["last_cash_date"] = max(
        meta.get("last_cash_date") or hypothetical.date.isoformat(),
        hypothetical.date.isoformat(),
    )
    inputs = _extend_horizon(inputs, meta)
    applied = replace(
        applied,
        inputs=_extend_horizon(applied.inputs, meta) if applied.reason is None else inputs,
    )
    baseline = project(inputs, runs=runs, seed=seed)
    if applied.reason is not None:
        result = baseline
        verdict_reason = applied.reason
    else:
        result = project(applied.inputs, runs=runs, seed=seed)
        _passed_flag, verdict_reason = _outcome_reason(
            result, floor_paise=inputs.floor_paise, threshold=threshold
        )
    feasible = applied.reason is None
    passed = feasible and _passes(result, threshold=threshold)
    tradeoffs: list[Tradeoff] = []
    if known_start and not passed:
        wait = _wait_tradeoff(inputs, hypothetical, runs=runs, seed=seed, threshold=threshold)
        if wait is not None:
            tradeoffs.append(wait)
        if feasible:
            tradeoffs.extend(
                _tradeoffs(
                    inputs,
                    hypothetical,
                    applied,
                    baseline["goals"],
                    runs=runs,
                    seed=seed,
                    threshold=threshold,
                )
            )
    if passed:
        result["verdict"] = "yes"
    else:
        same_day = any(trade["kind"] != "wait" for trade in tradeoffs)
        result["verdict"] = "yes-but" if same_day else "no"
    result["verdict_reason"] = verdict_reason
    result["hypothetical"] = {**meta, "feasible": feasible}
    result["tradeoffs"] = tradeoffs
    # A goal slips only through a delay-a-goal plan, which reports its own slip days.
    result["goal_slips"] = []
    result["floor_threshold"] = threshold
    result["verdict_threshold"] = threshold
    result["baseline"] = {
        "floor_probability": baseline["floor_probability"],
        "median_low": baseline["median_low"],
        "first_floor_crossing": baseline["first_floor_crossing"],
    }
    gaps = result["data_quality"].setdefault("gaps", [])
    gaps.extend(applied.gaps)
    if not known_start:
        # There is no starting balance to test, so there is no verdict and no lever runs.
        result.pop("verdict", None)
        result["tradeoffs"] = []
        result["verdict_reason"] = applied.reason or (
            "No liquid account has a known balance, so there is no affordability verdict."
        )
    gaps.append(
        f"the verdict uses {runs:,} seeded scenarios at a {threshold:.0%} floor probability "
        "threshold"
    )
    return result


def project_scenario(
    inputs: ProjectionInput,
    changes: Sequence[ScenarioChangeSpec],
    *,
    hypothetical: Hypothetical | None = None,
    income_source_ids: Collection[str] = (),
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
    threshold: float = FLOOR_THRESHOLD,
) -> Projection:
    """Run a multi-event what-if (and optional purchase) against an unchanged baseline."""
    needed = scenario_horizon_days(
        inputs.as_of, changes, ask_horizon_days=projection_mod.horizon_days(inputs)
    )
    if hypothetical is not None:
        _validate_hypothetical(inputs, hypothetical)
        probe = _apply(inputs, hypothetical)
        last_cash = max(
            probe.meta.get("last_cash_date") or hypothetical.date.isoformat(),
            hypothetical.date.isoformat(),
        )
        hyp_days = (date.fromisoformat(last_cash) - inputs.as_of).days + 1
        needed = max(needed, min(MAX_HORIZON_DAYS, hyp_days))
    base_inputs = replace(inputs, ask_horizon_days=needed)
    applied = apply_changes(base_inputs, changes, income_source_ids=income_source_ids)
    unchanged = project(base_inputs, runs=runs, seed=seed)
    if hypothetical is not None:
        result = afford(applied.inputs, hypothetical, runs=runs, seed=seed, threshold=threshold)
    else:
        result = project(applied.inputs, runs=runs, seed=seed)

    change_rows: list[ScenarioChange] = [
        {
            "kind": item.kind,
            "label": item.label,
            "occurrences": item.occurrences,
            "amount_paise": item.amount_paise,
        }
        for item in applied.changes
    ]
    assumptions: list[str] = [
        "Existing schedule timing and everyday spend baselines stay unchanged outside the "
        "listed scenario changes."
    ]
    if applied.has_added_flow:
        assumptions.append("Added flows are dated as asked without bank-holiday adjustment.")
    if applied.adds_or_raises_inflow:
        assumptions.append(
            "This scenario counts added or raised income that is assumed, not confirmed."
        )
    crossing = result["first_floor_crossing"]
    runway_days = (
        (date.fromisoformat(crossing) - inputs.as_of).days if crossing is not None else None
    )
    baseline_row: ScenarioBaseline = {
        "floor_probability": unchanged["floor_probability"],
        "median_low": unchanged["median_low"],
        "first_floor_crossing": unchanged["first_floor_crossing"],
        "safe_to_spend": unchanged["safe_to_spend"],
    }
    scenario_row: Scenario = {
        "changes": change_rows,
        "assumptions": assumptions,
        "runway_days": runway_days,
        "baseline": baseline_row,
    }
    if hypothetical is None:
        known_start = any(account.known for account in applied.inputs.liquid)
        if known_start:
            holds, reason = _outcome_reason(
                result, floor_paise=applied.inputs.floor_paise, threshold=threshold
            )
            scenario_row["holds"] = holds
            scenario_row["reason"] = reason
        else:
            scenario_row["reason"] = (
                "No liquid account has a known balance, so there is no floor test."
            )
    gaps = result["data_quality"].setdefault("gaps", [])
    gaps.append("this what-if assumes the listed scenario changes, which have not happened")
    for item in applied.changes:
        if item.occurrences == 0:
            gaps.append(f"scenario change matched no occurrence in the horizon: {item.label}")
    result["scenario"] = scenario_row
    return result


__all__ = ["METHODS", "Hypothetical", "afford", "project_scenario"]
